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


    # ------------------------------------------------------------------ #
    # Regression: addressed messages must always wake (#39)               #
    # ------------------------------------------------------------------ #

    async def _make_plugin_for_wake(self, identity="sapphire"):
        """Minimal HubPlugin wired for _decide_hub_wake / _on_message_received tests."""
        llm_service = FakeLLMService()
        event_bus = FakeEventBus(llm_service)
        plugin = HubPlugin(event_bus=event_bus)
        plugin._task_ledger = None
        plugin._presence = MagicMock()
        plugin._presence.publish = MagicMock()
        plugin._identity = AgentRuntime(
            name="coder",
            identity=identity,
            state="waiting",
            waiting_since=1.0,
            cooldown_until=2.0,
            waiting_reason="standing by",
        )
        return plugin, llm_service, event_bus

    async def test_re_sent_addressed_request_wakes_within_ttl(self):
        """A coordinator re-sending a near-identical direct request to a
        stuck peer must still fire a wake — even inside the 120s fingerprint
        TTL.  Bug #39: the 2nd send was silently suppressed."""
        plugin, llm_service, event_bus = await self._make_plugin_for_wake()

        wake_msg = HubMessage(
            action="message",
            from_agent="koordinator-id",
            from_identity="koordinator",
            to="sapphire",
            content="please investigate plugins/hub/plugin.py and report findings",
            scope=MessageScope.DIRECT.value,
            id="msg-wake-1",
        )
        resend = HubMessage(
            action="message",
            from_agent="koordinator-id",
            from_identity="koordinator",
            to="sapphire",
            # Near-identical content — same fingerprint under old code.
            content="please investigate plugins/hub/plugin.py and report findings",
            scope=MessageScope.DIRECT.value,
            id="msg-wake-2",  # different message id — not a redelivery
        )

        await plugin._on_message_received(wake_msg)
        first_emits = len(event_bus.emitted)
        self.assertGreater(first_emits, 0, "first send must emit TRIGGER_LLM_CONTINUE")

        # Re-send within TTL with a different msg_id (deliberate retry).
        await plugin._on_message_received(resend)
        self.assertGreater(
            len(event_bus.emitted),
            first_emits,
            "re-sent addressed request must emit another TRIGGER_LLM_CONTINUE "
            "even though content fingerprint matches the first message",
        )

    async def test_same_msg_id_redelivery_still_deduped(self):
        """Exact same message id arriving twice must be suppressed (true
        redelivery guard must remain intact after the #39 fix)."""
        plugin, llm_service, event_bus = await self._make_plugin_for_wake()

        msg = HubMessage(
            action="message",
            from_agent="koordinator-id",
            from_identity="koordinator",
            to="sapphire",
            content="investigate plugins/hub/plugin.py and report findings",
            scope=MessageScope.DIRECT.value,
            id="msg-dedup-1",
        )

        await plugin._on_message_received(msg)
        first_emits = len(event_bus.emitted)
        self.assertGreater(first_emits, 0)

        # Deliver the exact same msg_id again (network redelivery).
        await plugin._on_message_received(msg)
        self.assertEqual(
            len(event_bus.emitted),
            first_emits,
            "duplicate msg_id must be suppressed — not wake again",
        )

    async def test_broadcast_spam_still_fingerprint_deduped(self):
        """Repeated broadcasts with the same content must NOT pile up wakes —
        the anti-storm fingerprint guard must remain active for broadcasts."""
        plugin, llm_service, event_bus = await self._make_plugin_for_wake()

        broadcast_content = "team standup: share your status"
        for i in range(3):
            await plugin._on_message_received(
                HubMessage(
                    action="message",
                    from_agent="koordinator-id",
                    from_identity="koordinator",
                    to="sapphire",
                    content=broadcast_content,
                    scope=MessageScope.BROADCAST.value,
                    id=f"msg-broadcast-{i}",
                )
            )

        # Only the first broadcast should have caused a wake emission; the
        # rest are fingerprint-deduped.
        wake_count = sum(
            1
            for _, data, _ in event_bus.emitted
            if data.get("wake") or data.get("source") == "hub"
        )
        # Conservatively: total TRIGGER_LLM_CONTINUE emissions must be at
        # most 1 for identical broadcast spam (fingerprint dedup active).
        from kollabor_events import EventType

        llm_continue_count = sum(
            1 for evt, _, _ in event_bus.emitted if evt == EventType.TRIGGER_LLM_CONTINUE
        )
        self.assertLessEqual(
            llm_continue_count,
            1,
            "broadcast fingerprint dedup must suppress repeat wakes from "
            "identical broadcast content",
        )


if __name__ == "__main__":
    unittest.main()
