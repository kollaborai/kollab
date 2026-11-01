"""Long sessions retain compact settlement state without losing late turns."""

import asyncio
import gc
import tracemalloc
from itertools import permutations
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPError, ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator

USAGE = {"total_tokens": 13, "input_tokens": 10, "output_tokens": 3}


def result(turn_id, usage=None):
    return SimpleNamespace(request_id=f"request-{turn_id}", turn_id=turn_id,
                           usage=usage, end_frame={})


def make_orchestrator():
    stats = {}
    llm = SimpleNamespace(session_stats=stats)
    bus = SimpleNamespace(get_service=lambda name: llm if name == "llm_service" else None)
    return ACPOrchestrator(bus), stats


@pytest.mark.parametrize("with_usage", [False, True])
def test_100000_completed_turns_have_constant_retention(with_usage, monkeypatch, tmp_path):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path)
    orch, stats = make_orchestrator()
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        for turn_id in range(1, 100001):
            orch._settle_turn_usage(result(turn_id, USAGE if with_usage else None))
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        assert orch._usage_settled_through == 100000
        assert orch._usage_settled_ranges == []
        assert retained < 300000
        if with_usage:
            assert stats["total_input_tokens"] == 1000000
            assert stats["total_output_tokens"] == 300000
            assert orch._settle_turn_usage(result(1, USAGE)) is None
            assert stats["total_input_tokens"] == 1000000
    finally:
        if not already_tracing:
            tracemalloc.stop()


def test_late_old_turn_settles_and_replays_do_not_recount(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path)
    orch, stats = make_orchestrator()
    orch._settle_turn_usage(result(2, USAGE))
    for turn_id in range(3, 100003):
        orch._settle_turn_usage(result(turn_id))
    assert orch._usage_settled_through == 0
    assert orch._usage_settled_ranges == [(2, 100002)]
    assert stats["total_input_tokens"] == 10
    assert orch._settle_turn_usage(result(2, USAGE)) is None
    assert orch._settle_turn_usage(result(1, USAGE))["input_total"] == 10
    assert orch._usage_settled_through == 100002
    assert orch._usage_settled_ranges == []
    assert stats["total_input_tokens"] == 20
    assert stats["total_output_tokens"] == 6
    assert orch._settle_turn_usage(result(1, USAGE)) is None
    assert stats["total_input_tokens"] == 20


def test_settlement_ranges_match_unique_turns_for_all_small_orderings(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path)
    for ordering in permutations(range(1, 7)):
        orch, stats = make_orchestrator()
        seen = set()
        for turn_id in ordering:
            assert orch._settle_turn_usage(result(turn_id, USAGE)) is not None
            seen.add(turn_id)
            assert orch._settle_turn_usage(result(turn_id, USAGE)) is None
            assert stats["total_input_tokens"] == len(seen) * 10
        assert orch._usage_settled_through == 6
        assert orch._usage_settled_ranges == []


@pytest.mark.asyncio
async def test_rejected_queue_admission_does_not_leave_an_id_gap(tmp_path):
    runner = ACPRunner(ACPProfile.builtin("codex-cli", cwd=str(tmp_path)))
    runner.session_id = "session"
    runner._connection = object()
    runner._queue = asyncio.Queue(maxsize=1)
    first = runner.admit("one", request_id="one", source="rpc")
    with pytest.raises(ACPError, match="queue is full"):
        runner.admit("rejected", request_id="rejected", source="rpc")
    assert runner._queue.get_nowait() is first
    second = runner.admit("two", request_id="two", source="rpc")
    assert (first.turn_id, second.turn_id) == (1, 2)
