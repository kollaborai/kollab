"""Hub lifecycle notices remain visible without waking peer models."""

import json
import logging
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


def test_presence_model_follows_the_live_profile():
    plugin = HubPlugin()
    plugin._identity = _runtime("lapis", "lapis-id")
    profile = SimpleNamespace(
        name="main", get_provider=lambda: "openai", get_model=lambda: "gpt-a", get_effort=lambda: ""
    )
    manager = SimpleNamespace(get_active_profile=lambda: profile)
    plugin.event_bus = _EventBus({"profile_manager": manager})

    plugin._refresh_presence_model()
    assert plugin._identity.to_presence_dict()["tags"]["model"] == "gpt-a"

    profile.get_model = lambda: "gpt-b"  # a /model switch, however it happened
    plugin._refresh_presence_model()
    assert plugin._identity.to_presence_dict()["tags"]["model"] == "gpt-b"

    manager.get_active_profile = lambda: None  # a lookup that fails keeps the last model
    plugin._refresh_presence_model()
    assert plugin._identity.to_presence_dict()["tags"]["model"] == "gpt-b"


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
                "agent 'lapis' just came online in project /Users/me/dev/kollab.\n"
                "cwd: /Users/me/dev/kollab\n"
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


class _Config:
    """The `plugins.hub.*` values a project's config holds, behind `get`."""

    def __init__(self, **hub):
        self.values = {f"plugins.hub.{key}": value for key, value in hub.items()}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _arrival(to):
    return HubMessage(
        action="message",
        from_agent="lapis-id",
        from_identity="lapis",
        to=to,
        scope=MessageScope.DIRECT.value,
        content=(
            "agent 'lapis' just came online in project /home/user/proj.\n"
            "cwd: /home/user/proj\n"
            "current hub roster:\n  - koordinator (coordinator): idle"
        ),
        metadata={"lifecycle_event": "arrival"},
    )


def _peer(identity, config=None, *, holds_the_role=False):
    plugin = HubPlugin()
    plugin._task_ledger = None
    plugin._identity = _runtime(identity, f"{identity}-id")
    plugin._identity.is_coordinator = holds_the_role
    plugin.config = config
    return plugin


def _decide(plugin, message):
    return plugin._decide_hub_wake(
        message,
        is_intended=True,
        is_human_elsewhere=False,
        llm_service=SimpleNamespace(is_processing=False),
    )


@pytest.mark.asyncio
async def test_arrival_notice_is_tagged_and_still_delivered_plainly():
    plugin = HubPlugin(event_bus=_EventBus())
    plugin._identity = _runtime("lapis", "lapis-id")
    plugin._deliver_to_agent = AsyncMock(return_value=True)

    await plugin._announce_to_peers([_runtime("koordinator", "koordinator-id")])

    message = plugin._deliver_to_agent.await_args.args[1]
    assert message.metadata == {"lifecycle_event": "arrival"}
    assert not message.force


@pytest.mark.parametrize(
    ("setting", "me", "holds_the_role", "wakes"),
    [
        (None, "lapis", False, True),  # unset: every peer wakes, as always
        ("all", "lapis", False, True),
        ("all", "koordinator", True, True),
        ("koordinator", "koordinator", True, True),
        ("koordinator", "koordinator", False, True),  # named, not the holder
        ("koordinator", "lapis", True, True),  # lapis led first
        ("koordinator", "lapis", False, False),
        ("none", "koordinator", True, False),
        ("none", "lapis", True, False),
    ],
)
def test_arrival_wake_decides_who_spends_a_model_call(
    setting, me, holds_the_role, wakes
):
    config = None if setting is None else _Config(arrival_wake=setting)

    decision = _decide(
        _peer(me, config, holds_the_role=holds_the_role), _arrival(to=me)
    )

    assert (decision.mode, decision.should_trigger) == (
        ("wake", True) if wakes else ("observe", False)
    )
    if not wakes:
        assert decision.reason == "arrival"


@pytest.mark.parametrize("setting", ["sometimes", False, 3])
def test_an_unknown_arrival_wake_wakes_everyone_and_says_so(setting, caplog):
    plugin = _peer("lapis", _Config(arrival_wake=setting))

    with caplog.at_level(logging.WARNING, logger="plugins.hub.plugin"):
        decision = _decide(plugin, _arrival(to="lapis"))

    assert (decision.mode, decision.should_trigger) == ("wake", True)
    assert "plugins.hub.arrival_wake" in caplog.text


def test_arrival_wake_none_leaves_every_other_message_waking():
    plugin = _peer("lapis", _Config(arrival_wake="none"))
    request = HubMessage(
        action="message",
        from_agent="koordinator-id",
        from_identity="koordinator",
        to="lapis",
        scope=MessageScope.DIRECT.value,
        # Words alone never make a notice: only the arrival tag does.
        content="sable just came online, please review plugins/hub/plugin.py",
    )

    decision = _decide(plugin, request)

    assert (decision.mode, decision.should_trigger) == ("wake", True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "calls_the_model"),
    [("all", True), ("koordinator", False), ("none", False)],
)
async def test_arrival_notice_shows_and_waits_for_the_next_turn(
    setting, calls_the_model
):
    llm = _PassiveLLM()
    llm.queue_agent_hud = MagicMock()
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=MagicMock())
    )
    event_bus = _EventBus({"llm_service": llm, "renderer": renderer})
    plugin = HubPlugin(event_bus=event_bus)
    plugin.config = _Config(arrival_wake=setting)
    plugin._identity = _runtime("sable", "sable-id")
    plugin._task_ledger = None
    plugin._presence = MagicMock()

    await plugin._on_message_received(_arrival(to="sable"))

    displayed = renderer.message_coordinator.display_message_sequence.call_args.args[0]
    assert "agent 'lapis' just came online" in displayed[0][1]
    assert bool(event_bus.emit_with_hooks.await_count) is calls_the_model
    # Either way the notice is queued for the agent's next turn.
    assert "just came online" in llm.queue_agent_hud.call_args.kwargs["content"]


def test_a_projects_arrival_wake_overrides_the_global_one(tmp_path, monkeypatch):
    from kollabor_config.service import ConfigService

    home, project = tmp_path / "home", tmp_path / "project"
    for folder in (home, project):
        (folder / ".kollab").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    global_file = home / ".kollab" / "config.json"

    def hub(value):
        return json.dumps({"plugins": {"hub": {"arrival_wake": value}}})

    def koordinator_wakes():
        service = ConfigService(global_file, fast_mode=True)
        try:
            peer = _peer("koordinator", service)
            return _decide(peer, _arrival(to="koordinator")).should_trigger
        finally:
            service.shutdown()

    global_file.write_text(hub("none"))
    assert koordinator_wakes() is False  # the global setting alone: nobody wakes

    (project / ".kollab" / "config.json").write_text(hub("koordinator"))
    assert koordinator_wakes() is True  # this folder's own file wins


def test_config_modal_offers_arrival_wake_with_every_peer_waking_by_default():
    row = next(
        widget
        for widget in HubPlugin.get_config_widgets()["widgets"]
        if widget["config_path"] == "plugins.hub.arrival_wake"
    )

    assert (row["type"], row["label"]) == ("dropdown", "Wake on Agent Arrival")
    assert row["options"] == ["all", "koordinator", "none"]
    assert HubPlugin.get_default_config()["plugins"]["hub"]["arrival_wake"] == "all"
