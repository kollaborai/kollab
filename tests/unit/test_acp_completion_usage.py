"""ACP results settle usage even if later persistence or cancellation fails."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_engine.session import EngineSession

from kollabor.llm.acp_bridge import ACPAdmission, ACPResult
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.acp_state import ACPBinding, ACPBindingStore
from kollabor.state.hub_client import HubStateClient
from kollabor.state.local import LocalStateService
from kollabor.state.refresher import WidgetStateRefresher
from kollabor.state.remote import RemoteStateService
from kollabor_tui.display_tap import DisplayTap
from kollabor_tui.status.core_widgets import render_stats

USAGE = {
    "total_tokens": 96664, "input_tokens": 19337, "cached_read_tokens": 77184,
    "cached_write_tokens": 0, "output_tokens": 143, "thought_tokens": 66,
}


@pytest.fixture(autouse=True)
def isolated_bindings(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")


class Bus:
    def __init__(self, services):
        self.services = services

    def get_service(self, name):
        return self.services.get(name)


def result():
    return ACPResult(1, "session", "answer", "end_turn", (),
                     {"type": "turn_end", "acp_turn_usage": dict(USAGE)},
                     1, "request", "rpc", None, dict(USAGE))


async def rig():
    events, queue = [], asyncio.Queue()
    def on_event(event):
        events.append(event)
        queue.put_nowait(event)
    tap = DisplayTap(on_event=on_event)
    stats = {"acp_usage": {"used": 27905, "size": 200000}}
    llm = SimpleNamespace(session_stats=stats, conversation_manager=None,
                          conversation_history=[], conversation_logger=None)
    orch = ACPOrchestrator(Bus({"llm_service": llm, "display_tap": tap}))
    engine = EngineSession(session_id="engine", profile=SimpleNamespace(name="test", provider="acp"))
    engine.daemon = SimpleNamespace(subscribe=lambda: queue, unsubscribe=lambda q: None)
    engine.refresh_history = AsyncMock(return_value=[])
    tracker = asyncio.create_task(engine._track_events())
    await asyncio.sleep(0)
    tap.publish({"type": "turn_start", "request_id": "request", "turn_id": 1, "generation": 1})
    future = asyncio.get_running_loop().create_future()
    admission = ACPAdmission("request", "rpc", None, 1, 1, "prompt", future)
    return orch, llm, engine, events, queue, tracker, admission


async def finish(queue, tracker):
    queue.put_nowait({"type": "daemon_closed"})
    await asyncio.wait_for(tracker, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "none", "binding", "history", "cancel_history", "user_history", "cancel_user_history",
])
async def test_result_usage_survives_persistence_failure_and_cancellation(tmp_path, failure):
    orch, llm, engine, events, queue, tracker, admission = await rig()
    entered, release = asyncio.Event(), asyncio.Event()
    class Logger:
        async def log_assistant_message(self, *args, **kwargs):
            if failure == "history":
                raise OSError("history failed")
            if failure == "cancel_history":
                entered.set()
                await release.wait()
    if failure in {"history", "cancel_history"}:
        llm.conversation_logger = Logger()
    if failure == "binding":
        occupied = tmp_path / "occupied"
        occupied.write_text("not a directory")
        orch._bindings = ACPBindingStore(occupied)
        orch._runner = SimpleNamespace(binding=ACPBinding("s", "/", "p", ("p",), {}))
    hub = SimpleNamespace(on_acp_completion=AsyncMock())
    orch.hub_plugin = hub
    if failure in {"user_history", "cancel_user_history"}:
        history = asyncio.get_running_loop().create_future()
        if failure == "user_history":
            history.set_exception(OSError("user history failed"))
        orch._user_history_tasks["request"] = history
    admission.future.set_result(result())
    task = asyncio.create_task(orch._complete(admission))
    if failure == "cancel_history":
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
    elif failure == "cancel_user_history":
        await asyncio.sleep(0)
        task.cancel()
    outcome = await asyncio.wait_for(task, 1)
    await finish(queue, tracker)
    terminal = [e for e in events if e["type"] == "turn_complete"]
    assert len(terminal) == 1
    assert terminal[0]["input_tokens"] == 96521
    assert terminal[0]["output_tokens"] == 143
    assert terminal[0]["turn_usage"] == USAGE
    assert llm.session_stats["total_input_tokens"] == engine.total_input_tokens == 96521
    assert llm.session_stats["total_output_tokens"] == engine.total_output_tokens == 143
    assert llm.session_stats["total_cache_read_tokens"] == 77184
    assert engine.acp_turn_usage == USAGE
    if failure == "none":
        assert outcome["stop_reason"] == "end_turn"
    elif failure in {"cancel_history", "cancel_user_history"}:
        assert outcome["stop_reason"] == "cancelled"
    else:
        assert outcome["stop_reason"] == "error"
    if failure != "none":
        assert outcome["end_frame"]["outcome"] == "failed"
        assert outcome["end_frame"]["acp_turn_usage"] == USAGE
    hub.on_acp_completion.assert_awaited_once()
    assert hub.on_acp_completion.await_args.kwargs["end_frame"]["acp_turn_usage"] == USAGE
    assert hub.on_acp_completion.await_args.kwargs["failed"] is (failure != "none")

    local = LocalStateService(llm_service=llm, profile_manager=None)
    class RPC:
        async def call(self, method, params, timeout=None):
            if method != "state.get_session_stats":
                raise RuntimeError("not needed")
            return json.loads(json.dumps((await local.get_session_stats()).to_dict()))
    for state in (local, RemoteStateService(RPC()), HubStateClient(RPC())):
        ctx = SimpleNamespace(remote_state={}, llm_service=None)
        await WidgetStateRefresher(ctx, state).refresh_once()
        assert "96K tok/turn" in render_stats(60, ctx)


@pytest.mark.asyncio
async def test_no_result_cancellation_has_no_invented_usage():
    orch, llm, engine, events, queue, tracker, admission = await rig()
    admission.future.cancel()
    outcome = await orch._complete(admission)
    await finish(queue, tracker)
    terminal = next(e for e in events if e["type"] == "turn_complete")
    assert outcome["stop_reason"] == "cancelled"
    assert "input_tokens" not in terminal
    assert "turn_usage" not in terminal
    assert "acp_turn_usage" not in outcome["end_frame"]
    assert "total_input_tokens" not in llm.session_stats
    assert engine.total_input_tokens == engine.total_output_tokens == 0
    assert orch._usage_settled_through == 1
    assert orch._usage_settled_ranges == []


@pytest.mark.asyncio
async def test_result_ready_before_completion_task_cancel_still_settles():
    orch, llm, engine, events, queue, tracker, admission = await rig()
    task = asyncio.create_task(orch._complete(admission))
    await asyncio.sleep(0)
    admission.future.set_result(result())
    task.cancel()
    outcome = await asyncio.wait_for(task, 1)
    await finish(queue, tracker)
    terminal = next(e for e in events if e["type"] == "turn_complete")
    assert outcome["stop_reason"] == "cancelled"
    assert terminal["input_tokens"] + terminal["output_tokens"] == 96664
    assert llm.session_stats["total_input_tokens"] == engine.total_input_tokens == 96521
    assert engine.total_output_tokens == 143


@pytest.mark.asyncio
async def test_repeated_completion_does_not_double_count_or_erase_display():
    orch, llm, engine, events, queue, tracker, admission = await rig()
    admission.future.set_result(result())
    await orch._complete(admission)
    await orch._complete(admission)
    await finish(queue, tracker)
    assert llm.session_stats["total_input_tokens"] == engine.total_input_tokens == 96521
    assert llm.session_stats["total_output_tokens"] == engine.total_output_tokens == 143
    assert engine.acp_turn_usage == USAGE


@pytest.mark.asyncio
async def test_cache_write_and_read_remain_distinct_after_a_failed_repeated_completion():
    orch, llm, engine, events, queue, tracker, admission = await rig()
    usage = {"total_tokens": 625, "input_tokens": 100, "cached_read_tokens": 200,
             "cached_write_tokens": 300, "output_tokens": 25}
    class FailingLogger:
        async def log_assistant_message(self, *args, **kwargs):
            raise OSError("history failed")
    llm.conversation_logger = FailingLogger()
    admission.future.set_result(replace(result(), usage=usage, end_frame={"acp_turn_usage": usage}))
    await orch._complete(admission)
    await orch._complete(admission)
    await finish(queue, tracker)
    assert llm.session_stats["total_input_tokens"] == engine.total_input_tokens == 600
    assert llm.session_stats["total_output_tokens"] == engine.total_output_tokens == 25
    assert llm.session_stats["total_cache_read_tokens"] == 200
    assert llm.session_stats["total_cache_creation_tokens"] == 300
    assert engine.acp_turn_usage == usage


@pytest.mark.asyncio
async def test_late_old_completion_counts_usage_without_replacing_newer_display():
    orch, llm, engine, events, queue, tracker, admission = await rig()
    entered, release = asyncio.Event(), asyncio.Event()
    class DelayedLogger:
        async def log_assistant_message(self, text, *args, **kwargs):
            if text == "answer":
                entered.set()
                await release.wait()
    llm.conversation_logger = DelayedLogger()
    admission.future.set_result(result())
    old_task = asyncio.create_task(orch._complete(admission))
    await asyncio.wait_for(entered.wait(), 1)

    newer = {"total_tokens": 625, "input_tokens": 100, "cached_read_tokens": 200,
             "cached_write_tokens": 300, "output_tokens": 25}
    orch._on_runner_update({"kind": "turn_start", "request_id": "new", "turn_id": 2,
                            "generation": 1})
    future = asyncio.get_running_loop().create_future()
    second = ACPAdmission("new", "rpc", None, 1, 2, "prompt", future)
    future.set_result(replace(result(), request_id="new", turn_id=2, text="new answer",
                              usage=newer, end_frame={"acp_turn_usage": newer}))
    await orch._complete(second)
    release.set()
    await old_task
    await finish(queue, tracker)

    assert llm.session_stats["total_input_tokens"] == engine.total_input_tokens == 96521 + 600
    assert llm.session_stats["total_output_tokens"] == engine.total_output_tokens == 143 + 25
    assert llm.session_stats["acp_turn_usage"] == engine.acp_turn_usage == newer


@pytest.mark.asyncio
@pytest.mark.parametrize("second_fails", [False, True])
@pytest.mark.parametrize("first_no_result", [False, True])
async def test_out_of_order_completion_and_replay_settle_once(second_fails, first_no_result):
    orch, llm, engine, events, queue, tracker, first = await rig()
    old_task = asyncio.create_task(orch._complete(first))
    await asyncio.sleep(0)
    future = asyncio.get_running_loop().create_future()
    second = ACPAdmission("second", "rpc", None, 1, 2, "next", future)
    second_usage = {"total_tokens": 625, "input_tokens": 100, "cached_read_tokens": 200,
                    "cached_write_tokens": 300, "output_tokens": 25}
    newer = replace(result(), request_id="second", turn_id=2, text="second answer",
                    usage=second_usage, end_frame={"acp_turn_usage": second_usage})
    future.set_result(newer)
    if second_fails:
        original = orch._record_result
        async def fail_second(value):
            if value.request_id == "second":
                raise OSError("history failure after usage")
            await original(value)
        orch._record_result = fail_second
    orch._on_runner_update({"kind": "turn_start", "request_id": "second", "turn_id": 2,
                            "generation": 1})
    outcome = await orch._complete(second)
    assert outcome["stop_reason"] == ("error" if second_fails else "end_turn")
    assert llm.session_stats["total_input_tokens"] == 600
    assert orch._usage_settled_ranges == [(2, 2)]
    if first_no_result:
        first.future.set_exception(OSError("first turn produced no result"))
    else:
        first.future.set_result(result())
    await old_task
    await orch._complete(second)
    await orch._complete(first)
    await finish(queue, tracker)
    assert llm.session_stats["total_input_tokens"] == engine.total_input_tokens == (600 if first_no_result else 97121)
    assert llm.session_stats["total_output_tokens"] == engine.total_output_tokens == (25 if first_no_result else 168)
    assert llm.session_stats["acp_turn_usage"] == second_usage
    assert orch._usage_settled_through == 2
    assert orch._usage_settled_ranges == []
