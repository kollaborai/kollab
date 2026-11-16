"""Regression tests for MessageInjector."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from plugins.agent_orchestrator.message_injector import MessageInjector
from kollabor_events.models import EventType


class TestMessageInjector(unittest.IsolatedAsyncioTestCase):
    async def test_inject_does_not_lose_content_when_hooks_return_phase_results(self):
        """emit_with_hooks returns phase results, not the context dict.

        Previously this caused KeyError 'content' and silent failure.
        """
        event_bus = MagicMock()
        conversation = MagicMock()
        injector = MessageInjector(event_bus, conversation)

        # Simulate the real return shape of emit_with_hooks
        phase_results = {
            "pre": {},
            "main": {"final_data": {"content": "hello from agent", "blocked": False, "trigger_llm": True}},
            "post": {},
            "cancelled": False,
            "source": "message_injector",
            "event_type": "pre_message_inject",
        }
        event_bus.emit_with_hooks = AsyncMock(return_value=phase_results)

        success = await injector.inject("agent", "hello from agent", trigger_llm=True)

        self.assertTrue(success)
        conversation.add_message.assert_called_once()
        args, kwargs = conversation.add_message.call_args
        self.assertEqual(kwargs.get("role") or args[0] if args else kwargs["role"], "user")
        # content should be the (possibly formatted) message
        called_content = kwargs.get("content") or (args[1] if len(args) > 1 else None)
        self.assertIn("hello from agent", called_content)

    async def test_inject_honors_blocked_from_hooks(self):
        event_bus = MagicMock()
        conversation = MagicMock()
        injector = MessageInjector(event_bus, conversation)

        phase_results = {
            "main": {"final_data": {"content": "should not appear", "blocked": True}},
            "cancelled": False,
        }
        event_bus.emit_with_hooks = AsyncMock(return_value=phase_results)

        success = await injector.inject("agent", "blocked message")

        self.assertFalse(success)
        conversation.add_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
