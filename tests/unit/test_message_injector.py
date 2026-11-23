"""Regression tests for MessageInjector."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor_events.models import EventType
from plugins.agent_orchestrator.message_injector import MessageInjector


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


    async def test_inject_through_a_real_event_bus_lands_in_the_conversation(self):
        """#1151 acceptance: inject through a real EventBus with a real hook
        that edits the message; the edited message reaches the conversation."""
        from kollabor_events import EventBus, Hook, HookPriority

        event_bus = EventBus()

        async def tag(data, event):
            return {"data": {**data, "content": data["content"] + "\n[tagged]"}}

        await event_bus.register_hook(
            Hook(
                name="tag",
                plugin_name="test",
                event_type=EventType.PRE_MESSAGE_INJECT,
                priority=HookPriority.LLM.value,
                callback=tag,
            )
        )
        conversation = MagicMock()
        injector = MessageInjector(event_bus, conversation)

        ok = await injector.inject("agent", "hello from agent", trigger_llm=False)

        self.assertTrue(ok)
        conversation.add_message.assert_called_once()
        content = conversation.add_message.call_args.kwargs["content"]
        self.assertIn("hello from agent", content)
        self.assertIn("[tagged]", content)

if __name__ == "__main__":
    unittest.main()
