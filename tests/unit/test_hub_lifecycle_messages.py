"""Hub lifecycle notices remain visible without waking peer models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.commands.system_commands.handlers.model import ModelCommandHandler
from kollabor_agent.runtime import AgentRuntime
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin
from plugins.hub.startup_messages import (
    HUB_NEW_FEATURES,
    HUB_STARTUP_TIPS,
    choose_startup_tip,
)


class _EventBus:
    def __init__(self, services=None):
        self.services = services or {}
        self.emit_with_hooks = AsyncMock()

    def get_service(self, name):
        return self.services.get(name)


class _PassiveLLM:
    def __init__(self):
        self.conversation_history = []
        self.current_parent_uuid = "parent-1"
        self.is_processing = False
        self.conversation_logger = SimpleNamespace(log_system_message=AsyncMock())

    def queue_agent_hud(self, **_kwargs):
        return None

    def drain_pending_agent_hud(self):
        return ""


def _runtime(identity, agent_id, *, name="coder"):
    return AgentRuntime(name=name, identity=identity, agent_id=agent_id)


@pytest.mark.asyncio
async def test_model_switch_notice_is_delivered_to_each_peer():
    plugin = HubPlugin()
    plugin._identity = _runtime("lapis", "lapis-id")
    plugin._presence = MagicMock()
    plugin._presence.discover_agents_async = AsyncMock(
        return_value=[
            _runtime("koordinator", "koordinator-id", name="koordinator"),
            _runtime("sable", "sable-id"),
            _runtime("lapis", "lapis-other-id"),
        ]
    )
    plugin._deliver_to_agent = AsyncMock(return_value=True)

    await plugin.announce_model_switch(
        profile_name="openrouter-main",
        provider="openrouter",
        model="anthropic/claude-sonnet-4",
        previous_model="old-model",
    )

    delivered = [call.args[1] for call in plugin._deliver_to_agent.await_args_list]
    assert [message.to for message in delivered] == ["koordinator", "sable"]
    assert delivered[0].content.startswith("agent 'lapis' switched model.")
    assert "previous model: old-model" in delivered[0].content
    assert "model: anthropic/claude-sonnet-4" in delivered[0].content
    assert delivered[0].metadata == {
        "lifecycle_event": "model_switch",
        "profile_name": "openrouter-main",
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4",
        "previous_model": "old-model",
    }
    assert delivered[0].force is True


@pytest.mark.asyncio
async def test_join_notice_uses_the_visible_hub_message_renderer():
    coordinator = _runtime("koordinator", "koordinator-id", name="koordinator")
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=MagicMock())
    )
    plugin = HubPlugin(event_bus=_EventBus({"renderer": renderer}))
    plugin._identity = coordinator
    plugin._task_ledger = None

    await plugin._on_message_received(
        HubMessage(
            action="message",
            from_agent="lapis-id",
            from_identity="lapis",
            to="koordinator",
            scope=MessageScope.DIRECT.value,
            content=(
                "agent 'lapis' just came online in project /Users/malmazan/dev/kollab.\n"
                "cwd: /Users/malmazan/dev/kollab\n"
                "model: anthropic/claude-sonnet-4\n"
                "provider: anthropic\n"
                "profile: anthropic-main\n"
                "current hub roster:\n"
                "  - koordinator (coordinator): idle\n"
                "if you need help with anything, let them know.\n"
                'respond back using: <hub_msg to="lapis">your message</hub_msg>'
            ),
        )
    )

    displayed = renderer.message_coordinator.display_message_sequence.call_args.args[0]
    assert displayed[0][0] == "agent"
    assert displayed[0][1].startswith("lapis -> koordinator\n")
    assert "agent 'lapis' just came online" in displayed[0][1]
    assert "model: anthropic/claude-sonnet-4" in displayed[0][1]
    assert displayed[0][2]["tag_char"] == " ◆ "


@pytest.mark.asyncio
async def test_join_notice_includes_the_active_model():
    profile = SimpleNamespace(
        name="anthropic-main",
        get_provider=lambda: "anthropic",
        get_model=lambda: "anthropic/claude-sonnet-4",
    )
    event_bus = _EventBus(
        {"profile_manager": SimpleNamespace(get_active_profile=lambda: profile)}
    )
    plugin = HubPlugin(event_bus=event_bus)
    plugin._identity = _runtime("lapis", "lapis-id")
    peer = _runtime("koordinator", "koordinator-id", name="koordinator")
    plugin._deliver_to_agent = AsyncMock(return_value=True)

    await plugin._announce_to_peers([peer])

    message = plugin._deliver_to_agent.await_args.args[1]
    assert "model: anthropic/claude-sonnet-4" in message.content
    assert "provider: anthropic" in message.content
    assert "profile: anthropic-main" in message.content


def test_startup_status_explicitly_shows_stop_all_command():
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=MagicMock())
    )
    plugin = HubPlugin()
    plugin._identity = _runtime("koordinator", "koordinator-id", name="koordinator")

    plugin._display_startup_status(renderer, "coordinator", [])

    displayed = renderer.message_coordinator.display_message_sequence.call_args.args[0]
    assert displayed[0][1] == "koordinator (coordinator) | peers: none"
    assert displayed[1][0] == "system"
    assert "To stop all agents, submit: /hub stop all" in displayed[1][1]
    assert "Tip: " in displayed[1][1]
    assert "New features:" in displayed[1][1]
    for feature in HUB_NEW_FEATURES:
        assert feature in displayed[1][1]
    assert displayed[1][2] == {"display_type": "info"}


def test_startup_message_catalog_has_50_tips_and_rotates_by_seed():
    assert len(HUB_STARTUP_TIPS) == 50
    assert len(HUB_NEW_FEATURES) >= 3
    assert choose_startup_tip(seed=0) == HUB_STARTUP_TIPS[0]
    assert choose_startup_tip(seed=49) == HUB_STARTUP_TIPS[49]


@pytest.mark.asyncio
async def test_model_switch_notice_renders_without_triggering_peer_llm():
    llm = _PassiveLLM()
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=MagicMock())
    )
    event_bus = _EventBus({"llm_service": llm, "renderer": renderer})
    plugin = HubPlugin(event_bus=event_bus)
    plugin._identity = _runtime("koordinator", "koordinator-id", name="koordinator")
    plugin._task_ledger = None
    plugin._presence = MagicMock()

    message = HubMessage(
        action="message",
        from_agent="lapis-id",
        from_identity="lapis",
        to="koordinator",
        scope=MessageScope.DIRECT.value,
        content=(
            "agent 'lapis' switched model.\n"
            "profile: openrouter-main\n"
            "provider: openrouter\n"
            "model: anthropic/claude-sonnet-4"
        ),
        metadata={
            "lifecycle_event": "model_switch",
            "profile_name": "openrouter-main",
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4",
        },
    )

    await plugin._on_message_received(message)

    displayed = renderer.message_coordinator.display_message_sequence.call_args.args[0]
    assert displayed[0][0] == "agent"
    assert displayed[0][1].startswith("lapis -> koordinator\n")
    assert "switched model" in displayed[0][1]
    assert displayed[0][2]["tag_char"] == " ◆ "
    event_bus.emit_with_hooks.assert_not_awaited()
    assert llm.conversation_history == []
    llm.conversation_logger.log_system_message.assert_awaited_once()


class _Profile:
    name = "openrouter-main"
    provider = "openrouter"
    model = "old-model"

    def get_provider(self):
        return self.provider

    def get_model(self):
        return self.model


class _ProfileManager:
    def __init__(self):
        self.profile = _Profile()

    def get_active_profile(self):
        return self.profile

    def update_profile(self, _name, **kwargs):
        self.profile.model = kwargs["model"]
        return True


@pytest.mark.asyncio
async def test_model_command_announces_after_active_model_updates():
    state_service = SimpleNamespace(set_active_profile=AsyncMock())
    hub = SimpleNamespace(announce_model_switch=AsyncMock())
    event_bus = _EventBus({"state_service": state_service, "hub_plugin": hub})
    handler = ModelCommandHandler(
        command_registry=None,
        event_bus=event_bus,
        profile_manager=_ProfileManager(),
        llm_service=None,
    )

    result = await handler._set_active_profile_model("new-model", True)

    assert result.success is True
    hub.announce_model_switch.assert_awaited_once_with(
        profile_name="openrouter-main",
        provider="openrouter",
        model="new-model",
        previous_model="old-model",
    )
