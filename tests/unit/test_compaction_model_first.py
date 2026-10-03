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

    async def test_compact_tag_starts_compaction_and_ends_turn(self):
        plugin, _bus, _llm = _plugin(ledger=_Ledger())
        plugin._awaiting_model = True
        result = await plugin._handle_compact_tool({"id": "t1", "notes": "task: X"})
        self.assertTrue(result.success)
        self.assertTrue(result.metadata.get("end_turn"))
        self.assertTrue(plugin._compaction_in_progress)
        self.assertFalse(plugin._awaiting_model)
        self.assertEqual(plugin._model_notes, "task: X")
        await _drain(plugin)

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
