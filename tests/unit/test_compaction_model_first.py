"""Model-first compaction: the model is asked what to keep, then compacts.

Covers: threshold asks instead of compacting, <compact> starts compaction and
its notes land verbatim in the summary, the idle fallback, /compact and
/compact now, uncurated ledger items reaching the summarizer.

Run: python -m pytest tests/unit/test_compaction_model_first.py -v
"""

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

from kollabor_events import EventType
from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import ContextCompactionPlugin


def _msg(role: str, content: str, **meta) -> ConversationMessage:
    return ConversationMessage(
        role=role, content=content, metadata=meta, timestamp=datetime.now()
    )


class _Ledger:
    """Minimal context_service stand-in (a real class, not a Mock)."""

    def __init__(self, entries=None):
        self.entries = entries or []
        self.injected = []

    def all_entries(self):
        return self.entries

    def queue_ephemeral_injection(self, content):
        self.injected.append(content)


def _plugin(ledger=None, busy=False, history=None, tokens=150_000):
    values: Dict[str, Any] = {
        "plugins.context_compaction.enabled": True,
        "plugins.context_compaction.token_threshold_k": 100,
        "plugins.context_compaction.min_human_turns": 2,
        "plugins.context_compaction.keep_recent": 2,
        "plugins.context_compaction.max_summary_tokens": 2000,
        "plugins.context_compaction.log_compaction_events": False,
    }
    config = MagicMock()
    config.get = lambda key, default=None: values.get(key, default)

    bus = MagicMock()
    services = {"context_service": ledger}
    bus.get_service = lambda name: services.get(name)
    bus.emit_with_hooks = AsyncMock()

    plugin = ContextCompactionPlugin("test", bus, MagicMock(), config)
    llm = MagicMock()
    llm.is_processing = busy
    llm.conversation_history = (
        history
        if history is not None
        else [_msg("user", f"u{i}") if i % 2 == 0 else _msg("assistant", f"a{i}") for i in range(12)]
    )
    llm.session_stats = {"input_tokens": tokens, "output_tokens": 0}
    llm._pending_agent_hud = []
    plugin._llm_service = llm
    plugin._conversation_logger = SimpleNamespace(session_id="s1")
    return plugin, bus, llm


async def _drain(plugin):
    for task in list(plugin._compaction_tasks) + [plugin._watch_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


class TestModelFirst(unittest.IsolatedAsyncioTestCase):
    async def test_threshold_asks_model_instead_of_compacting(self):
        ledger = _Ledger(
            [SimpleNamespace(ctx_id="ctx-1", kind="file_read", label="a.py",
                             size_bytes=40_000, decision="pending")]
        )
        plugin, _bus, _llm = _plugin(ledger=ledger)
        await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertTrue(plugin._awaiting_model)
        self.assertFalse(plugin._compaction_in_progress)
        self.assertEqual(len(ledger.injected), 1)
        self.assertIn("ctx-1", ledger.injected[0])
        self.assertIn("<compact>", ledger.injected[0])
        # A second threshold hit while waiting does not ask again.
        await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertEqual(len(ledger.injected), 1)
        await _drain(plugin)

    async def test_compact_tag_stages_before_resuming_model_turn(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger())
        plugin._awaiting_model = True
        plugin._call_summarization_llm = AsyncMock(return_value="S" * 200)
        plugin._write_compaction_checkpoint = MagicMock(return_value=None)
        plugin._log_compaction_event = AsyncMock()

        result = await asyncio.wait_for(
            plugin._handle_compact_tool({"id": "t1", "notes": "task: X"}),
            timeout=1,
        )

        self.assertTrue(result.success)
        self.assertFalse(result.metadata.get("end_turn", False))
        self.assertIsNotNone(plugin._pending_compaction)
        self.assertFalse(plugin._compaction_in_progress)
        self.assertFalse(plugin._awaiting_model)
        self.assertIsNone(plugin._model_notes)
        self.assertEqual(plugin._call_summarization_llm.await_count, 1)
        compact_summary = next(
            message
            for message in plugin._pending_compaction
            if message.metadata.get("context_compaction")
        )
        self.assertIn("task: X", compact_summary.content)

    async def test_compact_tag_returns_actionable_failure_without_retry(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger())
        plugin._awaiting_model = True
        plugin._call_summarization_llm = AsyncMock(
            side_effect=RuntimeError("summarizer unavailable")
        )
        plugin._write_compaction_checkpoint = MagicMock(return_value=None)
        plugin._log_compaction_event = AsyncMock()

        try:
            result = await asyncio.wait_for(
                plugin._handle_compact_tool({"id": "t1", "notes": "task: X"}),
                timeout=1,
            )
        finally:
            await _drain(plugin)

        self.assertFalse(result.success)
        self.assertIn("/compact now", result.error)
        self.assertIn("not applied", result.error)
        self.assertEqual(plugin._call_summarization_llm.await_count, 1)
        self.assertIsNone(plugin._pending_compaction)
        self.assertFalse(plugin._compaction_in_progress)
        self.assertIsNone(plugin._model_notes)

    async def test_compact_tag_keeps_staged_swap_if_logging_fails(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger())
        plugin._awaiting_model = True
        plugin._call_summarization_llm = AsyncMock(return_value="S" * 200)
        plugin._write_compaction_checkpoint = MagicMock(return_value=None)
        plugin._log_compaction_event = AsyncMock(
            side_effect=RuntimeError("event logger unavailable")
        )

        result = await asyncio.wait_for(
            plugin._handle_compact_tool({"id": "t1", "notes": "task: X"}),
            timeout=1,
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(plugin._pending_compaction)
        self.assertEqual(plugin._consecutive_failures, 0)
        self.assertEqual(plugin._compaction_status, "ready_to_retry")
        await _drain(plugin)

    async def test_compact_tag_does_not_clear_notes_for_active_run(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger())
        plugin._compaction_in_progress = True
        plugin._model_notes = "notes owned by the active compaction"

        result = await asyncio.wait_for(
            plugin._handle_compact_tool({"id": "t2", "notes": "new notes"}),
            timeout=1,
        )

        self.assertFalse(result.success)
        self.assertIn("already running", result.error)
        self.assertEqual(plugin._model_notes, "notes owned by the active compaction")

    async def test_compact_tag_disabled_returns_actionable_failure(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger())
        plugin._disabled_for_session = True
        plugin._awaiting_model = True
        plugin._call_summarization_llm = AsyncMock(return_value="S" * 200)

        result = await asyncio.wait_for(
            plugin._handle_compact_tool({"id": "t1", "notes": "task: X"}),
            timeout=1,
        )

        self.assertFalse(result.success)
        self.assertIn("disabled for this session", result.error.lower())
        self.assertIn("new session", result.error.lower())
        plugin._call_summarization_llm.assert_not_awaited()
        self.assertFalse(plugin._compaction_in_progress)
        self.assertIsNone(plugin._model_notes)

    async def test_compact_tag_cancellation_cancels_bounded_compaction_task(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger())
        plugin._awaiting_model = True
        summary_started = asyncio.Event()

        async def block_until_cancelled(*_args, **_kwargs):
            summary_started.set()
            await asyncio.Event().wait()

        plugin._call_summarization_llm = AsyncMock(side_effect=block_until_cancelled)
        plugin._write_compaction_checkpoint = MagicMock(return_value=None)
        plugin._log_compaction_event = AsyncMock()
        handler_task = asyncio.create_task(
            plugin._handle_compact_tool({"id": "t1", "notes": "task: X"})
        )
        compaction_task = None
        try:
            await asyncio.wait_for(summary_started.wait(), timeout=1)
            compaction_task = plugin._compaction_task
            handler_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(handler_task, timeout=1)
        finally:
            await _drain(plugin)

        self.assertIsNotNone(compaction_task)
        self.assertTrue(compaction_task.cancelled())
        self.assertFalse(plugin._compaction_in_progress)
        self.assertIsNone(plugin._pending_compaction)
        self.assertIsNone(plugin._model_notes)
        self.assertEqual(plugin._compaction_status, "failed")
        self.assertIn("cancelled", plugin._compaction_status_message.lower())
        self.assertTrue(plugin._compaction_status_action)

    async def test_notes_land_verbatim_in_summary(self):
        plugin, _bus, llm = _plugin(ledger=None)
        plugin._model_notes = "keep editing plugins/x.py, step 3 next"
        plugin._call_summarization_llm = AsyncMock(return_value="S" * 200)
        plugin._write_compaction_checkpoint = MagicMock(return_value=None)
        plugin._log_compaction_event = AsyncMock()
        await plugin._run_compaction()
        staged = plugin._pending_compaction
        self.assertIsNotNone(staged)
        summary = next(m for m in staged if m.metadata.get("context_compaction"))
        self.assertIn("YOUR NOTES", summary.content)
        self.assertIn("keep editing plugins/x.py, step 3 next", summary.content)
        self.assertIsNone(plugin._model_notes)

    async def test_idle_fallback_compacts_after_model_turn(self):
        plugin, _bus, llm = _plugin(ledger=_Ledger())
        plugin._start_compaction = MagicMock(return_value=True)
        await plugin._request_model_curation(wake=False, reason="threshold")
        self.assertTrue(plugin._awaiting_model)
        await asyncio.sleep(0)  # watcher started, model has not answered yet
        plugin._start_compaction.assert_not_called()
        plugin._turn_count += 1  # the model answered (POST fired) ...
        llm.is_processing = False  # ... and its turn ended without <compact>
        await asyncio.wait_for(plugin._watch_task, timeout=3)
        plugin._start_compaction.assert_called_once()

    async def test_slash_compact_wakes_idle_model(self):
        plugin, bus, llm = _plugin(ledger=_Ledger(), tokens=10_000)
        msg = await plugin._handle_compact_command(SimpleNamespace(args=[]))
        self.assertIn("asked the model", msg)
        self.assertTrue(plugin._awaiting_model)
        self.assertTrue(llm.conversation_history[-1].metadata.get("compaction_request"))
        events = [c.args[0] for c in bus.emit_with_hooks.await_args_list]
        self.assertIn(EventType.TRIGGER_LLM_CONTINUE, events)
        await _drain(plugin)

    async def test_slash_compact_busy_rides_next_request(self):
        ledger = _Ledger()
        plugin, bus, _llm = _plugin(ledger=ledger, busy=True)
        await plugin._handle_compact_command(SimpleNamespace(args=[]))
        self.assertEqual(len(ledger.injected), 1)
        bus.emit_with_hooks.assert_not_awaited()
        await _drain(plugin)

    async def test_slash_compact_now_skips_the_question(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger(), tokens=10_000)
        msg = await plugin._handle_compact_command(SimpleNamespace(args=["now"]))
        self.assertIn("compacting now", msg)
        self.assertTrue(plugin._compaction_in_progress)
        self.assertFalse(plugin._awaiting_model)
        await _drain(plugin)


class TestUncuratedItems(unittest.TestCase):
    def test_pending_ledger_item_goes_to_summarizer(self):
        entry = SimpleNamespace(ctx_id="ctx-1", decision="pending", decision_body="",
                                kind="file_read", label="a.py", size_bytes=40_000)
        svc = SimpleNamespace(entry_for_message=lambda *_: None, all_entries=lambda: [entry])
        plugin, bus, _llm = _plugin()
        bus.get_service = lambda name: svc if name == "context_service" else None
        msg = _msg("user", "big file", ctx_ids=["ctx-1"])
        handled, untracked = plugin._partition_by_ledger([msg])
        self.assertEqual(handled, [])
        self.assertEqual(untracked, [msg])


if __name__ == "__main__":
    unittest.main()


class TestCompactTag(unittest.TestCase):
    def test_compact_tag_registered_and_matches(self):
        plugin, bus, _llm = _plugin()
        parser, executor = MagicMock(), MagicMock()
        bus.get_service = lambda name: {"response_parser": parser, "tool_executor": executor}.get(name)
        plugin._register_compact_tag()
        name, pattern, _label, extract = parser.register_plugin_tag.call_args.args
        self.assertEqual(name, "compact")
        executor.register_plugin_handler.assert_called_once_with("compact", plugin._handle_compact_tool)
        self.assertEqual(extract(pattern.search("ok <compact>task: X</compact>")), {"notes": "task: X"})
        self.assertEqual(extract(pattern.search("<compact/>")), {"notes": ""})
        self.assertIsNone(pattern.search("<compaction>x</compaction>"))


class TestNativeToolResultsInLedger(unittest.TestCase):
    def _partition(self, decision, body=""):
        entry = SimpleNamespace(ctx_id="ctx-1", decision=decision, decision_body=body,
                                kind="file_read", label="a.py", size_bytes=40_000)
        svc = SimpleNamespace(entry_for_message=lambda *_: None, all_entries=lambda: [entry])
        plugin, bus, _llm = _plugin()
        bus.get_service = lambda name: svc if name == "context_service" else None
        msg = _msg("tool", "file body", ctx_ids=["ctx-1"], tool_call_id="call_1")
        return plugin._partition_by_ledger([msg])

    def test_kept_native_result_becomes_plain_user_message(self):
        handled, untracked = self._partition("keep")
        self.assertEqual(untracked, [])
        self.assertEqual(handled[0].role, "user")
        self.assertNotIn("tool_call_id", handled[0].metadata)
        self.assertIn("file body", handled[0].content)

    def test_summarized_native_result_becomes_user_message(self):
        handled, _ = self._partition("summary", "a.py: 3 config keys matter")
        self.assertEqual(handled[0].role, "user")
        self.assertIn("a.py: 3 config keys matter", handled[0].content)


class TestNativeCurateCall(unittest.TestCase):
    def test_curate_id_argument_does_not_replace_the_call_id(self):
        from kollabor_agent.tool_call_contract import normalize_native_tool_call

        call = SimpleNamespace(
            name="curate", id="call_1", type="tool_use",
            input={"id": "ctx-1", "decision": "summary", "summary": "short"},
        )
        data = normalize_native_tool_call(call, plugin_handler_names={"curate"})
        self.assertEqual(data["id"], "call_1")  # the provider matches the result on this
        self.assertEqual(data["input"]["id"], "ctx-1")
        self.assertEqual(data["summary"], "short")


class TestCompactAskOnAcpChat(unittest.IsolatedAsyncioTestCase):
    """/compact asks the model by waking it natively; an ACP chat refuses that
    wake, so the ask must not start (#579, PR #793 review)."""

    @staticmethod
    def _acp(llm):
        llm._profile_selects_acp = lambda: (True, False)
        llm._acp_service = lambda: None

    async def test_slash_compact_on_acp_chat_changes_nothing(self):
        plugin, bus, llm = _plugin(ledger=_Ledger(), tokens=10_000)
        self._acp(llm)
        before = list(llm.conversation_history)

        first = await plugin._handle_compact_command(SimpleNamespace(args=[]))
        second = await plugin._handle_compact_command(SimpleNamespace(args=[]))

        self.assertEqual(llm.conversation_history, before)
        self.assertFalse(plugin._awaiting_model)
        bus.emit_with_hooks.assert_not_awaited()
        self.assertIn("ACP", first)
        self.assertEqual(first, second)  # never "already asked the model"
        await _drain(plugin)

    async def test_refused_wake_leaves_no_stuck_state_with_real_handler(self):
        from kollabor.llm.message_handler import MessageHandler
        from tests.unit.llm.test_message_handler import (
            _acp_runner,
            _make_coordinator,
            _set_acp_state,
        )

        coord = _make_coordinator()
        _set_acp_state(coord, runner=_acp_runner(), selects_acp=True)
        handler = MessageHandler(coord)

        plugin, bus, _llm = _plugin(ledger=_Ledger(), tokens=10_000)
        coord.conversation_history = _llm.conversation_history
        plugin._llm_service = coord

        async def route(event_type, data, source):
            if event_type == EventType.TRIGGER_LLM_CONTINUE:
                return await handler.handle_llm_continue(data, MagicMock())
            return {}

        bus.emit_with_hooks = AsyncMock(side_effect=route)
        before = list(coord.conversation_history)

        await plugin._handle_compact_command(SimpleNamespace(args=[]))
        again = await plugin._handle_compact_command(SimpleNamespace(args=[]))

        coord.create_background_task.assert_not_called()  # no native turn
        self.assertEqual(coord.conversation_history, before)  # no stray prompt
        self.assertFalse(plugin._awaiting_model)
        self.assertNotIn("already asked", again)
        await _drain(plugin)


class TestOperatorWordsAndOpenState(unittest.IsolatedAsyncioTestCase):
    OPERATOR_BLOCK = "=== OPERATOR'S OWN WORDS (verbatim, oldest first) ==="
    OPERATOR_END = "=== END OPERATOR'S OWN WORDS ==="
    OPEN_STATE = "=== OPEN STATE ==="

    @staticmethod
    def _operator_message(text, timestamp, source="user"):
        return _msg(
            "user",
            text,
            operator_inputs=[
                {"source": source, "timestamp": timestamp, "text": text}
            ],
        )

    @classmethod
    def _operator_block(cls, content):
        start = content.find(cls.OPERATOR_BLOCK)
        if start < 0:
            return ""
        end = content.find(cls.OPERATOR_END, start)
        return content[start:] if end < 0 else content[start : end + len(cls.OPERATOR_END)]

    @classmethod
    def _open_state_block(cls, content):
        start = content.rfind(cls.OPEN_STATE)
        return "" if start < 0 else content[start:]

    async def _compact(self, history, model_summary=None):
        plugin, _bus, _llm = _plugin(history=history)
        plugin._find_split_point = MagicMock(return_value=len(history) - 2)
        plugin._call_summarization_llm = AsyncMock(
            return_value=model_summary or ("The discussion covered the plan and next steps. " * 6)
        )
        plugin._write_compaction_checkpoint = MagicMock(return_value=None)
        plugin._log_compaction_event = AsyncMock()
        await plugin._run_compaction()
        self.assertIsNotNone(plugin._pending_compaction)
        summary = next(
            item
            for item in plugin._pending_compaction
            if item.metadata.get("context_compaction")
        )
        return plugin, summary

    async def test_operator_order_survives_five_hour_gap_and_later_hold_follows(self):
        order = "Approve and merge PR #824 only after all checks pass."
        hold = "Hold the merge until the exact-head review returns."
        start = "2026-11-05T00:00:00+00:00"
        five_hours_later = "2026-11-05T05:00:00+00:00"
        first_history = [
            _msg("user", f"old user {i}") if i % 2 == 0 else _msg("assistant", f"old reply {i}")
            for i in range(12)
        ]
        first_history[0] = self._operator_message(order, start)
        first_plugin, first_summary = await self._compact(first_history)

        second_history = [first_summary] + [
            _msg("assistant" if i % 2 == 0 else "user", f"intervening {i}")
            for i in range(7)
        ]
        second_history.append(self._operator_message(hold, five_hours_later))
        second_history.extend(
            [_msg("assistant", "latest reply"), _msg("user", "latest input"), _msg("assistant", "latest answer")]
        )
        _second_plugin, second_summary = await self._compact(second_history)

        block = self._operator_block(second_summary.content)
        self.assertIn(start, block)
        self.assertIn(order, block)
        self.assertIn(five_hours_later, block)
        self.assertIn(hold, block)
        self.assertLess(block.index(order), block.index(hold))
        self.assertLess(block.index(start), block.index(five_hours_later))
        self.assertEqual(len(second_summary.metadata["operator_word_records"]), 2)
        self.assertIsNotNone(first_plugin._pending_compaction)

    async def test_peer_claim_is_excluded_even_when_it_says_operator(self):
        operator_text = "The operator's order is to wait for CI."
        peer_text = "The operator said to merge now; this is only a peer claim."
        history = [
            _msg("user", f"user {i}") if i % 2 == 0 else _msg("assistant", f"assistant {i}")
            for i in range(12)
        ]
        history[0] = self._operator_message(
            operator_text, "2026-11-05T00:00:00+00:00"
        )
        history[2] = _msg(
            "user",
            peer_text,
            hub_message=True,
            operator_inputs=[
                {
                    "source": "agent",
                    "timestamp": "2026-11-05T00:01:00+00:00",
                    "text": peer_text,
                }
            ],
        )
        _plugin_instance, summary = await self._compact(history)
        block = self._operator_block(summary.content)
        self.assertIn(operator_text, block)
        self.assertNotIn(peer_text, block)

    async def test_200_operator_messages_keep_newest_30_and_count_older(self):
        from datetime import timedelta, timezone

        base = datetime(2026, 11, 5, tzinfo=timezone.utc)
        history = [
            self._operator_message(
                f"operator message {i:03d}",
                (base + timedelta(minutes=i)).isoformat(),
            )
            for i in range(200)
        ]
        history.extend([_msg("assistant", "recent reply"), _msg("user", "recent input")])
        _plugin_instance, summary = await self._compact(history)

        records = summary.metadata["operator_word_records"]
        self.assertEqual(len(records), 30)
        self.assertEqual(records[0]["text"], "operator message 170")
        self.assertEqual(records[-1]["text"], "operator message 199")
        self.assertEqual(summary.metadata["operator_words_omitted"], 170)
        block = self._operator_block(summary.content)
        self.assertRegex(block, r"170 older.*omitted")
        self.assertNotIn("operator message 169", block)
        self.assertIn("operator message 199", block)

    async def test_character_bound_keeps_newest_verbatim_records(self):
        from datetime import timedelta, timezone

        base = datetime(2026, 11, 5, tzinfo=timezone.utc)
        history = [
            self._operator_message(
                f"operator message {i:02d}: " + ("x" * 1000),
                (base + timedelta(minutes=i)).isoformat(),
            )
            for i in range(10)
        ]
        history.extend([_msg("assistant", "recent reply"), _msg("user", "recent input")])
        _plugin_instance, summary = await self._compact(history)

        records = summary.metadata["operator_word_records"]
        self.assertLess(len(records), 10)
        self.assertEqual(records[-1]["text"], history[9].content)
        self.assertLessEqual(sum(len(record["text"]) for record in records), 8000)
        self.assertGreater(summary.metadata["operator_words_omitted"], 0)
        self.assertNotEqual(records[0]["text"], history[0].content)

    async def test_omitted_count_accumulates_when_prior_block_is_carried_forward(self):
        from datetime import timedelta, timezone

        base = datetime(2026, 11, 5, tzinfo=timezone.utc)
        first_history = [
            self._operator_message(
                f"older operator message {i:02d}",
                (base + timedelta(minutes=i)).isoformat(),
            )
            for i in range(40)
        ]
        first_history.extend([_msg("assistant", "recent reply"), _msg("user", "recent input")])
        _first_plugin, first_summary = await self._compact(first_history)

        second_history = [first_summary] + [
            self._operator_message(
                f"new operator message {i:02d}",
                (base + timedelta(hours=1, minutes=i)).isoformat(),
            )
            for i in range(5)
        ]
        second_history.extend(
            [_msg("assistant", f"middle {i}") for i in range(4)]
            + [_msg("assistant", "recent reply"), _msg("user", "recent input")]
        )
        _second_plugin, second_summary = await self._compact(second_history)

        self.assertEqual(second_summary.metadata["operator_words_omitted"], 15)
        records = second_summary.metadata["operator_word_records"]
        self.assertEqual(len(records), 30)
        self.assertEqual(records[-5]["text"], "new operator message 00")
        self.assertEqual(records[-1]["text"], "new operator message 04")

    async def test_open_state_drops_unverified_ids_and_shas(self):
        valid_sha = "a1b2c3d4e5f6"
        fabricated_sha = "deadbeefcafefeed"
        source_text = f"Prepare PR #824 at head {valid_sha}."
        history = [
            _msg("user", source_text) if i % 2 == 0 else _msg("assistant", f"reply {i}")
            for i in range(12)
        ]
        history[0] = self._operator_message(
            source_text, "2026-11-05T00:00:00+00:00"
        )
        model_summary = (
            "The summary includes the plan and current review state. " * 4
            + "\n=== OPEN STATE ===\n"
            + f"- PR #824 | owner: operator | head SHA: {valid_sha} | verdict: pending | next: await CI\n"
            + f"- PR #999 | owner: unknown | head SHA: {fabricated_sha} | verdict: pass | next: merge"
        )
        _plugin_instance, summary = await self._compact(history, model_summary)
        block = self._open_state_block(summary.content)

        self.assertIn("#824", block)
        self.assertIn(valid_sha, block)
        self.assertNotIn("#999", block)
        self.assertNotIn(fabricated_sha, block)
        self.assertTrue(summary.content.rstrip().endswith("next: await CI"))

    async def test_no_operator_messages_omits_operator_block_and_adds_final_open_state(self):
        history = [
            _msg("user", f"ordinary user {i}")
            if i % 2 == 0
            else _msg("assistant", f"ordinary answer {i}")
            for i in range(12)
        ]
        _plugin_instance, summary = await self._compact(history)

        self.assertNotIn(self.OPERATOR_BLOCK, summary.content)
        self.assertEqual(self._open_state_block(summary.content).strip(), self.OPEN_STATE)
        self.assertTrue(summary.content.rstrip().endswith(self.OPEN_STATE))
