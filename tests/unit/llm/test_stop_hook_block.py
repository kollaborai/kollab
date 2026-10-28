"""Stop-hook block channel (#357): a blocked turn-final reply continues the
turn with the hook's reason injected for the model, capped and flagged.

The block is produced by kollabor/config_hooks.py (`_hook_blocked` /
`_hook_reason` in LLM_RESPONSE_POST final_data) and honored in
QueueProcessor._emit_llm_response_and_handle.
"""

import asyncio
import unittest
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from kollabor_agent.queue_processor import (
    MAX_STOP_HOOK_CONTINUATIONS,
    QueueProcessor,
)
from kollabor_events.data_models import ConversationMessage


@dataclass
class MockQueueConfig:
    """Mock queue config for testing."""

    overflow_strategy: str = "drop_oldest"
    log_queue_events: bool = False
    enable_queue_metrics: bool = False
    block_timeout: Optional[float] = None


@dataclass
class MockTaskConfig:
    """Mock task config for QueueProcessor tests."""

    queue: MockQueueConfig = field(default_factory=MockQueueConfig)


def _blocked_context(reason: str) -> dict:
    return {
        "post": {
            "final_data": {"_hook_blocked": True, "_hook_reason": reason},
        },
    }


class TestStopHookBlock(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures (mirrors test_queue_processor.py)."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.conversation_history = [
            ConversationMessage(role="user", content="do the thing"),
            ConversationMessage(role="assistant", content="done"),
        ]
        self.event_bus = MagicMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.conversation_logger = AsyncMock()

        self.processor = QueueProcessor(
            conversation_history=self.conversation_history,
            session_stats={},
            stats={},
            queue_metrics={},
            task_config=MockTaskConfig(),
            api_service=AsyncMock(),
            tool_executor=MagicMock(),
            response_parser=MagicMock(),
            message_display_service=MagicMock(),
            renderer=MagicMock(),
            config=MagicMock(),
            event_bus=self.event_bus,
            conversation_logger=self.conversation_logger,
            streaming_handler=MagicMock(),
            native_tools_handler=MagicMock(),
            add_message_fn=MagicMock(),
            max_queue_size=10,
        )

    def tearDown(self):
        self.loop.close()

    def _emit(self, **kwargs):
        defaults = dict(
            response_text="done",
            clean_response="done",
            thinking_duration=0.0,
            parent_uuid="uuid-1",
        )
        defaults.update(kwargs)
        return self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(**defaults)
        )

    def test_blocked_final_reply_continues_turn_with_reason(self):
        """A blocked turn-final reply keeps the turn open and injects the reason."""
        self.processor.turn_completed = True
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value=_blocked_context("no live proof")
        )

        result = self._emit()

        self.assertEqual(result, ("done", False, False))
        self.assertFalse(self.processor.turn_completed)
        self.assertTrue(self.processor._stop_hook_active)
        self.assertEqual(self.processor._stop_hook_continuations, 1)
        # Reason rides the newest user/tool message as model-only context.
        target = self.conversation_history[0]  # assistant msg is no target
        self.assertIn("no live proof", target.metadata["injected_context"])
        # The transcript records the block for audits and external tools.
        self.conversation_logger.log_system_message.assert_awaited_once()
        args, kwargs = self.conversation_logger.log_system_message.call_args
        self.assertEqual(kwargs.get("subtype"), "stop_hook_block")
        self.assertIn("no live proof", args[0])

    def test_blocked_tool_bearing_reply_is_not_turn_final(self):
        """A reply still holding tools continues anyway; its block is not honored."""
        self.processor.turn_completed = False
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value=_blocked_context("no live proof")
        )

        result = self._emit(has_native_tools=True)

        self.assertEqual(result, ("done", False, False))
        self.assertFalse(self.processor.turn_completed)
        self.assertEqual(self.processor._stop_hook_continuations, 0)
        self.assertNotIn(
            "injected_context", self.conversation_history[0].metadata
        )
        self.conversation_logger.log_system_message.assert_not_awaited()

    def test_block_reason_falls_back_when_empty(self):
        """`_hook_blocked` without a reason still honors with a default string."""
        self.processor.turn_completed = True
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "post": {"final_data": {"_hook_blocked": True}},
            }
        )

        self._emit()

        self.assertFalse(self.processor.turn_completed)
        self.assertIn(
            "Blocked by Stop hook",
            self.conversation_history[0].metadata["injected_context"],
        )

    def test_max_continuations_cap_ends_turn(self):
        """After MAX blocks the turn ends even if the hook still blocks."""
        self.processor.turn_completed = True
        self.processor._stop_hook_continuations = MAX_STOP_HOOK_CONTINUATIONS
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value=_blocked_context("still blocked")
        )

        self._emit()

        self.assertTrue(self.processor.turn_completed)
        self.assertFalse(self.processor._stop_hook_active)
        self.assertEqual(self.processor._stop_hook_continuations, 0)
        self.processor.message_display_service.display_error_message.assert_called_once()

    def test_clean_final_reply_resets_state(self):
        """A clean turn-final reply clears the flag and budget."""
        self.processor.turn_completed = True
        self.processor._stop_hook_active = True
        self.processor._stop_hook_continuations = 2
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})

        self._emit()

        self.assertTrue(self.processor.turn_completed)
        self.assertFalse(self.processor._stop_hook_active)
        self.assertEqual(self.processor._stop_hook_continuations, 0)

    def test_stop_hook_active_reaches_hook_payload(self):
        """The flag marks a continuation-triggered Stop pass on hook stdin data."""
        self.processor.turn_completed = True
        self.processor._stop_hook_active = True
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})

        self._emit()

        emitted = self.event_bus.emit_with_hooks.call_args[0][1]
        self.assertIs(emitted["stop_hook_active"], True)

    def test_new_user_batch_resets_budget_and_flag(self):
        """process_message_batch starts a fresh stop-hook budget per user turn
        and clears stop_hook_active, so a turn that died mid-block cannot
        leak the flag into the next turn's Stop payload."""
        self.processor._stop_hook_continuations = 2
        self.processor._stop_hook_active = True
        self.processor._execute_llm_turn = AsyncMock(return_value="uuid-1")

        self.loop.run_until_complete(
            self.processor.process_message_batch(
                [ConversationMessage(role="user", content="again")],
                current_parent_uuid="uuid-1",
            )
        )

        self.assertEqual(self.processor._stop_hook_continuations, 0)
        self.assertFalse(self.processor._stop_hook_active)

    def test_blocked_reason_without_parent_uuid_skips_transcript_log(self):
        """No parent_uuid (legacy callers) still continues the turn."""
        self.processor.turn_completed = True
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value=_blocked_context("no live proof")
        )

        self._emit(parent_uuid=None)

        self.assertFalse(self.processor.turn_completed)
        self.conversation_logger.log_system_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
