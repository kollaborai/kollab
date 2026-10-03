"""Compaction must not re-fire every turn once the real prompt is under the threshold.

Incident, koordinator on 2026-09-30 (128K window, auto threshold 96K): rounds 6-9
fired 7 to 40 seconds apart. The "tokens" in each "Compaction triggered" line were
the chars//3 estimate of the local history (r8 checkpoint: 304,369 chars -> 101,456,
logged 101,663) while the API-reported prompt was ~44K. Compaction cannot shrink
that estimate (116K-char system prompt, verbatim-preserved task reminders and HUD
messages, the summary itself), so every turn re-triggered a summarization.

Run: python -m pytest tests/unit/test_compaction_retrigger.py -v
"""

import asyncio
import unittest
from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import MagicMock

from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import ContextCompactionPlugin

THRESHOLD_K = 96


def _msg(role: str, content: str, **meta) -> ConversationMessage:
    return ConversationMessage(
        role=role, content=content, metadata=meta, timestamp=datetime.now()
    )


def _make_plugin(history: List[ConversationMessage], reported: int):
    values: Dict[str, Any] = {
        "plugins.context_compaction.enabled": True,
        "plugins.context_compaction.token_threshold_k": THRESHOLD_K,
        "plugins.context_compaction.min_human_turns": 4,
        "plugins.context_compaction.log_compaction_events": False,
    }
    config = MagicMock()
    config.get = lambda key, default=None: values.get(key, default)

    bus = MagicMock()
    bus.get_service = lambda name: None  # no hub ledger: nothing pending
    plugin = ContextCompactionPlugin("test", bus, MagicMock(), config)

    llm_service = MagicMock()
    llm_service.conversation_history = history
    llm_service.session_stats = {"input_tokens": reported, "output_tokens": 0}
    llm_service.api_service.get_last_token_usage.return_value = {
        "prompt_tokens": reported
    }
    llm_service._pending_agent_hud = []
    plugin._llm_service = llm_service
    return plugin


def _incident_history() -> List[ConversationMessage]:
    """Shaped like the r8 checkpoint: ~298K chars, so ~99K at chars//3."""
    history = [
        _msg("system", "s" * 116_000),
        _msg("user", "x" * 33_500, context_compaction=True),
    ]
    history += [_msg("user", "h" * 5_500, agent_hud=True) for _ in range(27)]
    for _ in range(8):
        history.append(_msg("assistant", ""))
        history.append(_msg("tool", "[historical tool output omitted]"))
    return history


class TestNoRetriggerAfterCompaction(unittest.TestCase):
    def test_estimate_inflated_history_does_not_trigger_under_real_threshold(
        self,
    ) -> None:
        history = _incident_history()
        plugin = _make_plugin(history, reported=44_005)

        # Premise: the estimate alone is over the threshold, the real prompt is not.
        self.assertGreaterEqual(
            plugin._estimate_history_tokens(history), THRESHOLD_K * 1000
        )
        self.assertFalse(plugin._should_compact(history))

    def test_next_turn_after_compaction_does_not_retrigger(self) -> None:
        # Before: real prompt over the threshold, so compaction is right to fire.
        before = _incident_history()
        plugin = _make_plugin(before, reported=100_649)
        self.assertTrue(plugin._should_compact(before))

        # After: history is a different list, no smaller in chars (the summary and
        # preserved tasks replaced messages that were ~0 chars); the next request
        # reports the real, smaller prompt. It must not fire again.
        after = _incident_history()
        plugin._llm_service.conversation_history = after
        plugin._llm_service.session_stats["input_tokens"] = 44_005
        self.assertFalse(plugin._should_compact(after))

    def test_hook_does_not_start_a_compaction_when_real_prompt_is_under(self) -> None:
        history = _incident_history()
        plugin = _make_plugin(history, reported=44_005)
        runs: List[int] = []

        async def fake_run() -> None:
            runs.append(1)

        plugin._run_compaction = fake_run

        async def drive() -> None:
            await plugin._on_llm_turn_complete({}, None)
            await asyncio.sleep(0)

        asyncio.run(drive())
        self.assertEqual(runs, [])
        self.assertFalse(plugin._compaction_in_progress)

    def test_deferred_trigger_is_evaluated_and_logged_once(self) -> None:
        history = _incident_history()
        plugin = _make_plugin(history, reported=100_649)
        plugin._llm_service._pending_agent_hud = [object()]  # coordination in flight

        with self.assertLogs(level="INFO") as logs:
            asyncio.run(plugin._on_llm_turn_complete({}, None))

        triggered = [m for m in logs.output if "Compaction triggered" in m]
        deferred = [m for m in logs.output if "Compaction deferred" in m]
        self.assertEqual(len(triggered), 1)
        self.assertEqual(len(deferred), 1)


class TestSignalSelection(unittest.TestCase):
    def test_reported_over_threshold_triggers_even_with_a_small_history(self) -> None:
        history = [_msg("user", f"m{i}") for i in range(6)]
        plugin = _make_plugin(history, reported=100_649)

        self.assertTrue(plugin._should_compact(history))

    def test_estimate_is_the_fallback_when_no_usage_was_reported(self) -> None:
        big = _incident_history()
        self.assertTrue(_make_plugin(big, reported=0)._should_compact(big))

        small = [_msg("user", f"m{i}") for i in range(6)]
        self.assertFalse(_make_plugin(small, reported=0)._should_compact(small))


if __name__ == "__main__":
    unittest.main()
