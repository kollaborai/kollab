"""Temporary #901 containment; synthetic data only."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from plugins.context_compaction_plugin import ContextCompactionPlugin, _append_jsonl_durable


def make_plugin(tmp_path):
    config = SimpleNamespace(get=lambda key, default=None: default)
    plugin = ContextCompactionPlugin("context_compaction", None, MagicMock(), config)
    history = [SimpleNamespace(role="user", content="synthetic", metadata={})]
    plugin._llm_service = SimpleNamespace(conversation_history=history)
    plugin._conversation_logger = SimpleNamespace(session_file=tmp_path / "session.jsonl", session_id="synthetic")
    plugin._call_summarization_llm = AsyncMock(side_effect=AssertionError("summarizer invoked"))
    plugin._display_compaction_event = MagicMock(side_effect=AssertionError("display invoked"))
    return plugin, history


def test_all_entry_points_refuse_and_preserve_history(tmp_path):
    p, history = make_plugin(tmp_path)
    before = list(history)
    async def run():
        assert not p._start_compaction(force=True)
        await p._run_compaction()
        assert not await p._rollover_with_summary({}, None, boundary="request")
        for args in ([], ["now"]):
            assert "temporarily unavailable" in await p._handle_compact_command(SimpleNamespace(args=args))
        result = await p._handle_compact_tool({"id": "synthetic"})
        assert not result.success and "temporarily unavailable" in result.error
        p._pending_compaction = [SimpleNamespace(role="user", content="replacement", metadata={})]
        await p._apply_pending_compaction({}, None)
        assert p._pending_compaction is None
        await p._log_compaction_event(1, 1, 1, 1, 1, summary_text="synthetic")
    asyncio.run(run())
    assert history == before
    assert p._compaction_status == "failed"
    assert not list(tmp_path.iterdir())
    assert p._write_compaction_checkpoint(history) is None
    p._log_checkpoint_event("synthetic", 1)
    with pytest.raises(RuntimeError, match="temporarily disabled"):
        _append_jsonl_durable(str(tmp_path / "session.jsonl"), {"type": "context_compaction"})
    assert not list(tmp_path.iterdir())
    p._call_summarization_llm.assert_not_awaited()
    p._display_compaction_event.assert_not_called()


def test_budget_hooks_stop_at_cap_even_with_cached_success(tmp_path):
    p, history = make_plugin(tmp_path)
    p._get_hard_cap_threshold = lambda: 100
    p._outgoing_prompt_tokens = lambda _: 100
    p._last_hard_cap_guard_turn = p._turn_count
    p._last_hard_cap_guard_signature = p._history_signature(history)
    async def run():
        for method in (p._on_llm_request_pre, p._on_tool_call_pre):
            event = SimpleNamespace(cancelled=False, data={})
            result = await method({}, event)
            assert result["cancelled"] and event.cancelled
            assert not result["permission_decision"]["allowed"]
            assert "temporarily unavailable" in result["permission_decision"]["reason"]
        p._outgoing_prompt_tokens = lambda _: 99
        assert not (await p._on_llm_request_pre({}, None)).get("cancelled", False)
        assert not (await p._on_tool_call_pre({}, None)).get("cancelled", False)
    asyncio.run(run())
    assert not list(tmp_path.iterdir())


def test_marker_writers_do_not_touch_failing_storage(tmp_path, monkeypatch):
    p, history = make_plugin(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("storage accessed")
    monkeypatch.setattr("builtins.open", forbidden)
    assert p._write_compaction_checkpoint(history) is None
    p._log_checkpoint_event("synthetic", 1)
    asyncio.run(p._log_compaction_event(1, 1, 1, 1, 1, summary_text="synthetic"))
    with pytest.raises(RuntimeError, match="temporarily disabled"):
        _append_jsonl_durable(str(tmp_path / "session.jsonl"), {"type": "context_compaction"})


def test_cancelled_direct_compaction_never_stages(tmp_path):
    p, history = make_plugin(tmp_path)
    before = list(history)
    async def run():
        task = asyncio.create_task(p._run_compaction())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert history == before and p._pending_compaction is None
    assert not list(tmp_path.iterdir())
    p._call_summarization_llm.assert_not_awaited()
