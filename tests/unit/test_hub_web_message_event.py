"""A hub message that wakes the agent tells web clients, so the web UI shows it
before the reply it starts (the terminal draws its own hub box)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.runtime import AgentRuntime
from kollabor_tui.display_tap import DisplayTap
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin

HUD = "<agent_hud>\n[hub:lapis->koordinator]\n+ [hub channel: lapis -> koordinator]\n  run the tests\n</agent_hud>"


class _EventBus:
    def __init__(self, services):
        self.services = services
        self.emit_with_hooks = AsyncMock()

    def get_service(self, name):
        return self.services.get(name)


class _LLM:
    def __init__(self):
        self.conversation_history = []
        self.current_parent_uuid = "parent-1"
        self.is_processing = False
        self.conversation_logger = SimpleNamespace(log_system_message=AsyncMock())

    def queue_agent_hud(self, **_kwargs):
        return None

    def drain_pending_agent_hud(self):
        return HUD


@pytest.mark.asyncio
async def test_a_waking_hub_message_is_published_for_web_clients():
    tap = DisplayTap()
    events = tap.subscribe("web")
    llm = _LLM()
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=MagicMock())
    )
    event_bus = _EventBus({"llm_service": llm, "renderer": renderer, "display_tap": tap})
    plugin = HubPlugin(event_bus=event_bus)
    plugin._identity = AgentRuntime(
        name="koordinator", identity="koordinator", agent_id="koordinator-id"
    )
    plugin._task_ledger = None
    plugin._presence = MagicMock()

    message = HubMessage(
        action="message",
        from_agent="lapis-id",
        from_identity="lapis",
        to="koordinator",
        scope=MessageScope.DIRECT.value,
        content="Please run the unit tests and report the failures back to me.",
    )
    await plugin._on_message_received(message)

    assert llm.conversation_history[-1].content == HUD
    published = []
    while not events.empty():
        published.append(events.get_nowait())
    hub_events = [event for event in published if event["type"] == "hub_message"]
    assert [event["message_id"] for event in hub_events] == [message.id]
