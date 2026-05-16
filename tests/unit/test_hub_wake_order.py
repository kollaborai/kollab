import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor_agent.runtime import AgentRuntime
from kollabor.llm.agent_hud import (
    AgentHudEntry,
    format_agent_hud,
    merge_agent_hud_with_user_message,
)
from kollabor_events import EventType

from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin


class FakeLLMService:
    def __init__(self):
        self.conversation_history = []
        self.current_parent_uuid = "parent-1"
        self.conversation_logger = MagicMock()
        self.conversation_logger.log_system_message = AsyncMock()
        self._pending_agent_hud = []

    async def inject_system_message(self, content: str, subtype: str = "injection"):
        entry = self.queue_agent_hud(
            section="system",
            label=subtype,
            content=content,
        )
        await self.conversation_logger.log_system_message(
            format_agent_hud([entry]),
            parent_uuid=self.current_parent_uuid,
            subtype=subtype,
        )

    def queue_agent_hud(self, section: str, label: str, content: str):
        entry = AgentHudEntry(section=section, label=label, content=content)
        self._pending_agent_hud.append(entry)
        return entry

    def drain_pending_agent_hud(self) -> str:
        pending = list(self._pending_agent_hud)
        self._pending_agent_hud.clear()
        return format_agent_hud(pending)

    def merge_pending_agent_hud(self, user_message: str) -> str:
        pending = list(self._pending_agent_hud)
        self._pending_agent_hud.clear()
        return merge_agent_hud_with_user_message(pending, user_message)


class FakeEnvQueue:
    def drain(self):
        return []


class FakeEventBus:
    def __init__(self, llm_service):
        self.llm_service = llm_service
        self.emitted = []

    def get_service(self, name):
        if name == "llm_service":
            return self.llm_service
        if name == "env_queue":
            return FakeEnvQueue()
        return None

    async def emit_with_hooks(self, event_type, data, source):
        self.emitted.append((event_type, data, source))
        return None


class TestHubWakeOrder(unittest.IsolatedAsyncioTestCase):
    async def test_direct_hub_task_stays_after_wake_header(self):
        llm_service = FakeLLMService()
        event_bus = FakeEventBus(llm_service)
        plugin = HubPlugin(event_bus=event_bus)
        plugin._task_ledger = None
        plugin._presence = MagicMock()
        plugin._presence.publish = MagicMock()
        plugin._identity = AgentRuntime(
            name="coder",
            identity="sapphire",
            state="waiting",
            waiting_since=1.0,
            cooldown_until=2.0,
            waiting_reason="standing by",
        )

        await plugin._on_message_received(
            HubMessage(
                action="message",
                from_agent="koordinator-id",
                from_identity="koordinator",
                to="sapphire",
                content=(
                    "you have an active task. investigate plugins/hub/plugin.py "
                    "and report findings."
                ),
                scope=MessageScope.DIRECT.value,
            )
        )

        contents = [msg.content for msg in llm_service.conversation_history]

        self.assertEqual(len(contents), 1)
        self.assertTrue(contents[-1].startswith("<agent_hud>"))
        self.assertIn("[system:wake_header]", contents[-1])
        self.assertIn("[hub:koordinator->sapphire]", contents[-1])
        self.assertIn("[wake:", contents[-1])
        self.assertIn("[hub channel: koordinator -> sapphire]", contents[-1])
        self.assertIn("you have an active task", contents[-1])
        self.assertIn("Handle this once if actionable", contents[-1])
        self.assertNotIn("<system_messages>", contents[-1])
        self.assertNotIn("<sys_msg>", contents[-1])
        self.assertTrue(llm_service.conversation_history[-1].metadata["hub_message"])
        self.assertTrue(llm_service.conversation_history[-1].metadata["agent_hud"])
        self.assertEqual(event_bus.emitted[0][0], EventType.TRIGGER_LLM_CONTINUE)

    async def test_pure_standing_by_ack_queues_hud_without_wake(self):
        llm_service = FakeLLMService()
        event_bus = FakeEventBus(llm_service)
        plugin = HubPlugin(event_bus=event_bus)
        plugin._task_ledger = None
        plugin._presence = MagicMock()
        plugin._presence.publish = MagicMock()
        plugin._identity = AgentRuntime(
            name="coder",
            identity="sapphire",
            state="idle",
        )

        await plugin._on_message_received(
            HubMessage(
                action="message",
                from_agent="koordinator-id",
                from_identity="koordinator",
                to="sapphire",
                content="Standing by. No action needed until koordinator assigns next task.",
                scope=MessageScope.DIRECT.value,
            )
        )

        self.assertEqual(llm_service.conversation_history, [])
        self.assertEqual(event_bus.emitted, [])
        self.assertEqual(len(llm_service._pending_agent_hud), 1)
        hud = llm_service.drain_pending_agent_hud()
        self.assertTrue(hud.startswith("<agent_hud>"))
        self.assertIn("[hub:koordinator->sapphire]", hud)
        self.assertIn("Standing by", hud)


if __name__ == "__main__":
    unittest.main()
