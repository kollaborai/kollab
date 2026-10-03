"""Compaction stop conditions and side-call hygiene (issue #129, tasks 17/24/25/27).

- 24: a compaction that cannot get the prompt under the threshold must stop
  re-firing every turn, say so once, and retry only after meaningful growth.
- 25: "coordination in flight" may defer compaction a bounded number of turns.
- 27: only the newest "[Task Reminder after context compaction]" survives a round.
- 17: the summarizer's provider.call() retries a 429/5xx like the main request path.

Run: python -m pytest tests/unit/test_compaction_stop_conditions.py -v
"""

import asyncio
import unittest
from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor_ai.providers.errors import RateLimitError
from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import (
    MAX_COORDINATION_DEFERRALS,
    ContextCompactionPlugin,
)

THRESHOLD = 96_000
MARGIN = THRESHOLD // 10


def _msg(role: str, content: str, **meta) -> ConversationMessage:
    return ConversationMessage(
        role=role, content=content, metadata=meta, timestamp=datetime.now()
    )


def _make_plugin(history: List[ConversationMessage], reported: int):
    values: Dict[str, Any] = {
        "plugins.context_compaction.enabled": True,
        "plugins.context_compaction.token_threshold_k": 96,
        "plugins.context_compaction.min_human_turns": 4,
        "plugins.context_compaction.log_compaction_events": False,
    }
    config = MagicMock()
    config.get = lambda key, default=None: values.get(key, default)
    bus = MagicMock()
    bus.get_service = lambda name: None  # no hub ledger: nothing pending
    plugin = ContextCompactionPlugin("test", bus, MagicMock(), config)
    svc = MagicMock()
    svc.conversation_history = history
    svc.session_stats = {"input_tokens": reported, "output_tokens": 0}
    svc._pending_agent_hud = []
    plugin._llm_service = svc
    return plugin


def _history() -> List[ConversationMessage]:
    return [_msg("user", f"m{i}") for i in range(8)]


def _apply_staged_compaction(plugin, history) -> None:
    """Stage a compaction that keeps 6 messages and swap it in at LLM_REQUEST_PRE."""
    plugin._compaction_round = 1
    plugin._pending_compaction = list(history[:6])
    plugin._pre_compaction_len = len(history)
    asyncio.run(plugin._apply_pending_compaction({}, None))


def _report(plugin, tokens: int) -> None:
    plugin._llm_service.session_stats["input_tokens"] = tokens


def _warnings(plugin) -> List[str]:
    calls = plugin.renderer.message_coordinator.display_message_sequence.call_args_list
    return [
        c.args[0][0][1]
        for c in calls
        if c.args[0][0][2].get("display_type") == "warning"
    ]


class TestCannotShrinkStop(unittest.TestCase):
    def _stuck_plugin(self):
        history = _history()
        plugin = _make_plugin(history, reported=100_649)
        self.assertTrue(plugin._should_compact(history))
        _apply_staged_compaction(plugin, history)
        return plugin, history

    def test_compaction_that_cannot_shrink_stops_refiring_and_says_so_once(self) -> None:
        plugin, history = self._stuck_plugin()

        with self.assertLogs(level="WARNING") as logs:
            results = []
            for tokens in (99_000, 99_400, 99_800, 100_200):
                _report(plugin, tokens)  # compacted prompt, still >= the threshold
                results.append(plugin._should_compact(history))

        self.assertEqual(results, [False] * 4)
        self.assertEqual(len([m for m in logs.output if "paused" in m]), 1)
        self.assertEqual(len(_warnings(plugin)), 1)
        self.assertIn("96K", _warnings(plugin)[0])

    def test_retries_only_after_meaningful_growth(self) -> None:
        plugin, history = self._stuck_plugin()
        _report(plugin, 99_000)
        self.assertFalse(plugin._should_compact(history))

        _report(plugin, 99_000 + MARGIN - 1)
        self.assertFalse(plugin._should_compact(history))
        _report(plugin, 99_000 + MARGIN)
        self.assertTrue(plugin._should_compact(history))

    def test_prompt_back_under_the_threshold_rearms_compaction(self) -> None:
        plugin, history = self._stuck_plugin()
        _report(plugin, 99_000)
        self.assertFalse(plugin._should_compact(history))

        _report(plugin, 44_000)  # e.g. /clear
        self.assertFalse(plugin._should_compact(history))
        _report(plugin, 100_000)
        self.assertTrue(plugin._should_compact(history))

    def test_compaction_that_shrinks_is_not_flagged(self) -> None:
        history = _history()
        plugin = _make_plugin(history, reported=100_649)
        self.assertTrue(plugin._should_compact(history))
        _apply_staged_compaction(plugin, history)

        _report(plugin, 44_005)
        self.assertFalse(plugin._should_compact(history))
        self.assertEqual(_warnings(plugin), [])
        _report(plugin, 100_000)
        self.assertTrue(plugin._should_compact(history))

    def test_stale_usage_after_the_swap_is_not_a_verdict(self) -> None:
        plugin, history = self._stuck_plugin()

        # The request after the swap failed: usage is still the pre-compaction count.
        self.assertFalse(plugin._should_compact(history))
        self.assertEqual(_warnings(plugin), [])

        _report(plugin, 99_000)  # the next real request: now it is a verdict
        self.assertFalse(plugin._should_compact(history))
        self.assertEqual(len(_warnings(plugin)), 1)


class TestBoundedCoordinationDeferral(unittest.TestCase):
    def test_pending_coordination_defers_at_most_a_few_turns_in_a_row(self) -> None:
        history = _history()
        plugin = _make_plugin(history, reported=100_649)
        plugin._llm_service._pending_agent_hud = [object()]  # never drains
        runs: List[int] = []

        async def fake_run() -> None:
            runs.append(1)

        plugin._run_compaction = fake_run

        async def drive() -> None:
            for _ in range(MAX_COORDINATION_DEFERRALS + 1):
                await plugin._on_llm_turn_complete({}, None)
                await asyncio.sleep(0)

        with self.assertLogs(level="INFO") as logs:
            asyncio.run(drive())

        deferred = [m for m in logs.output if "Compaction deferred" in m]
        self.assertEqual(len(deferred), MAX_COORDINATION_DEFERRALS)
        self.assertEqual(runs, [1])  # the turn after the last deferral compacts

    def test_a_cleared_trigger_resets_the_deferral_count(self) -> None:
        history = _history()
        plugin = _make_plugin(history, reported=100_649)
        plugin._llm_service._pending_agent_hud = [object()]

        asyncio.run(plugin._on_llm_turn_complete({}, None))
        self.assertEqual(plugin._coordination_deferrals, 1)
        _report(plugin, 40_000)
        asyncio.run(plugin._on_llm_turn_complete({}, None))
        self.assertEqual(plugin._coordination_deferrals, 0)


class TestOnlyNewestTaskReminderSurvives(unittest.TestCase):
    @staticmethod
    def _reminder(size: int) -> ConversationMessage:
        return _msg(
            "user",
            "[Task Reminder after context compaction]\n" + "x" * size,
            compaction_task_reminder=True,
        )

    @staticmethod
    def _reminders(msgs):
        return [m for m in msgs if m.metadata.get("compaction_task_reminder")]

    def test_old_reminders_do_not_ride_along_or_get_quoted(self) -> None:
        plugin = _make_plugin([], 0)
        old_kept, old_task = self._reminder(5000), self._reminder(4000)
        real_task = _msg(
            "user",
            "[hub channel: lapis -> koordinator] fix the bug and report back",
            hub_from="lapis",
            hub_message=True,
        )

        out = plugin._build_compacted_history(
            system_msg=None,
            summary_text="S",
            to_keep=[_msg("user", "recent"), old_kept],
            preserved_tasks=[old_task, real_task],
        )

        reminders = self._reminders(out)
        self.assertEqual(len(reminders), 1)
        self.assertNotIn(old_kept, out)
        self.assertNotIn(old_task, out)
        self.assertIn("From lapis:", reminders[0].content)
        self.assertNotIn("From unknown", reminders[0].content)
        self.assertLess(sum(len(m.content) for m in out), 1000)

    def test_with_no_live_tasks_the_newest_old_reminder_is_kept(self) -> None:
        plugin = _make_plugin([], 0)
        older, newest = self._reminder(10), self._reminder(20)

        out = plugin._build_compacted_history(
            None, "S", to_keep=[older, _msg("user", "recent"), newest], preserved_tasks=[]
        )

        self.assertEqual(self._reminders(out), [newest])
        self.assertEqual(sum(1 for m in out if m.content == "recent"), 1)


class TestSummarizerRetry(unittest.TestCase):
    def _run(self, plugin, provider):
        plugin._profile_manager = MagicMock()
        plugin._profile_manager.get_active_profile.return_value.to_dict.return_value = {
            "provider": "test"
        }
        with patch(
            "kollabor_ai.providers.registry.ProviderRegistry.get_provider",
            new=AsyncMock(return_value=provider),
        ), patch(
            "kollabor_ai.providers.registry.create_config_from_profile",
            return_value=MagicMock(),
        ), patch("asyncio.sleep", new=AsyncMock()) as sleep:
            out = asyncio.run(plugin._call_summarization_llm("msgs", "sys", 100))
        return out, sleep

    def test_a_429_is_retried_then_succeeds(self) -> None:
        ok = MagicMock()
        ok.get_text_content.return_value = "SUMMARY"
        provider = MagicMock()
        provider.call = AsyncMock(
            side_effect=[RateLimitError("429", provider="test", retry_after=7), ok]
        )

        out, sleep = self._run(_make_plugin([], 0), provider)

        self.assertEqual(out, "SUMMARY")
        self.assertEqual(provider.call.await_count, 2)
        sleep.assert_awaited_once_with(7.0)

    def test_retries_are_bounded(self) -> None:
        plugin = _make_plugin([], 0)
        base = plugin.config.get
        plugin.config.get = lambda k, d=None: 2 if k == "kollabor.llm.max_retries" else base(k, d)
        provider = MagicMock()
        provider.call = AsyncMock(side_effect=RateLimitError("429", provider="test"))

        out, _ = self._run(plugin, provider)

        self.assertIsNone(out)
        self.assertEqual(provider.call.await_count, 3)  # first try + 2 retries

    def test_a_non_transient_error_is_not_retried(self) -> None:
        provider = MagicMock()
        provider.call = AsyncMock(side_effect=ValueError("bad request"))

        out, sleep = self._run(_make_plugin([], 0), provider)

        self.assertIsNone(out)
        self.assertEqual(provider.call.await_count, 1)
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
