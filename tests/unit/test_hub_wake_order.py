import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor_agent.runtime import AgentRuntime
from kollabor.llm.system_messages import (
    PendingSystemMessage,
    format_system_message,
    merge_system_messages_with_user_message,
)
from kollabor_events import EventType
from kollabor_events.data_models import ConversationMessage

from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin


class FakeLLMService:
    def __init__(self):
        self.conversation_history = []
        self.current_parent_uuid = "parent-1"
        self.conversation_logger = MagicMock()
        self.conversation_logger.log_system_message = AsyncMock()
        self._pending_system_messages = []

    async def inject_system_message(self, content: str, subtype: str = "injection"):
        self._pending_system_messages.append(
            PendingSystemMessage(subtype=subtype, content=content)
        )
        await self.conversation_logger.log_system_message(
            format_system_message(content, subtype=subtype),
            parent_uuid=self.current_parent_uuid,
            subtype=subtype,
        )

    def merge_pending_system_messages(self, user_message: str) -> str:
        pending = list(self._pending_system_messages)
        self._pending_system_messages.clear()
        return merge_system_messages_with_user_message(pending, user_message)


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
        self.assertTrue(contents[-1].startswith("<system_messages>"))
        self.assertIn("[wake_header]", contents[-1])
        self.assertIn("[wake:", contents[-1])
        self.assertIn("[hub channel: koordinator -> sapphire]", contents[-1])
        self.assertIn("you have an active task", contents[-1])
        self.assertIn("Handle this once if actionable", contents[-1])
        self.assertTrue(llm_service.conversation_history[-1].metadata["hub_message"])
        self.assertEqual(event_bus.emitted[0][0], EventType.TRIGGER_LLM_CONTINUE)


if __name__ == "__main__":
    unittest.main()
