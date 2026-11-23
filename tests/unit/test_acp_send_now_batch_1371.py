"""#1371: Send Now batches are admitted as one priority group."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.llm_coordinator import LLMService


class _BatchACP:
    active = True
    supports_image_input = False
    voice_rules_epochs: set[object] = set()

    def __init__(self, runner: ACPRunner, *, refuse_cancel: bool = False):
        self.runner = runner
        self.refuse_cancel = refuse_cancel
        self.calls: list[str] = []

    async def steer(self, *_args, **_kwargs):
        self.calls.append("steer")
        return False

    def begin_send_now_batch(self, batch_id, expected_count, **owner):
        self.calls.append("begin_batch")
        return self.runner.begin_send_now_batch(
            batch_id, expected_count, **owner
        )

    def admit(self, message, **kwargs):
        self.calls.append(f"admit:{kwargs['request_id']}")
        return self.runner.admit(message, **kwargs)

    def prioritize_queued(self, admission):
        self.calls.append("prioritize_one")
        return self.runner.prioritize_queued(admission)

    def finish_send_now_batch(self, batch_id):
        self.calls.append("finish_batch")
        return self.runner.finish_send_now_batch(batch_id)

    def prepare_cancel(self, *, session_id, generation):
        self.calls.append("prepare_cancel")
        if self.refuse_cancel:
            return False
        return self.runner.prepare_cancel(
            expected_session_id=session_id,
            expected_generation=generation,
        )

    def schedule_prepared_cancel(self, *, session_id, generation):
        self.calls.append("schedule_cancel")
        return True


def _active_runner() -> tuple[ACPRunner, asyncio.Task, asyncio.Event]:
    runner = ACPRunner(ACPProfile("send-now-batch", ("unused",)))
    runner.session_id = "session-A"
    runner.generation = 7
    runner._connection = object()
    runner._next_turn_id = 12
    runner._active_context = SimpleNamespace(request_id="active-A")
    runner._active_turn_id = 12
    runner._active_generation = 7
    release_active = asyncio.Event()
    runner._queue.put_nowait(
        SimpleNamespace(request_id="older-D", future=None, priority_batch_id=None)
    )

    async def active_turn():
        await release_active.wait()

    active_task = asyncio.create_task(active_turn())
    runner._active_rpc_task = active_task
    runner._active_future = asyncio.get_running_loop().create_future()
    return runner, active_task, release_active


async def _send_batch_message(service, acp, request_id, text, index, *, first=False):
    return await service._admit_acp(
        acp,
        {
            "message": text,
            "request_id": request_id,
            "send_now": first,
            "send_now_batch_id": "batch-BC",
            "send_now_batch_size": 2,
            "send_now_batch_index": index,
        },
        "state_rpc",
    )


def _cleanup_runner(runner: ACPRunner, active_task: asyncio.Task) -> None:
    runner._cancel_hold = False
    runner._cancel_target = None
    runner._cancel_active_task = None
    getattr(runner, "_send_now_batches", {}).clear()
    runner._send_now_reserved = 0
    runner._queue_release.set()
    active_task.cancel()


@pytest.mark.asyncio
async def test_send_now_batch_admits_b_and_c_contiguously_before_older_d_once():
    runner, active_task, release_active = _active_runner()
    service = object.__new__(LLMService)
    acp = _BatchACP(runner)
    try:
        first = await _send_batch_message(service, acp, "client-B", "message B", 0, first=True)
        assert first["status"] == "queued"
        assert acp.calls.count("schedule_cancel") == 0, "do not stop the owner between B and C"

        second = await _send_batch_message(service, acp, "client-C", "message C", 1)
        assert second["status"] == "queued"
        finished = service.finish_send_now_batch(acp, "batch-BC")

        assert finished is True
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "client-C", "older-D",
        ]
        assert [item.request_id for item in runner._queue._queue].count("client-B") == 1
        assert [item.request_id for item in runner._queue._queue].count("client-C") == 1
        assert acp.calls.count("admit:client-B") == 1
        assert acp.calls.count("admit:client-C") == 1
        assert acp.calls.count("schedule_cancel") == 1
        assert runner._cancel_hold is True
    finally:
        release_active.set()
        _cleanup_runner(runner, active_task)
        await asyncio.gather(active_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_send_now_batch_partial_admission_is_not_resent_when_cancel_fence_refuses():
    runner, active_task, release_active = _active_runner()
    service = object.__new__(LLMService)
    acp = _BatchACP(runner, refuse_cancel=True)
    try:
        first = await _send_batch_message(service, acp, "client-B", "message B", 0, first=True)
        assert first["status"] == "queued"

        # C is rejected before ACP admission. Finalization must still release B
        # ahead of D and must not retry B when the active-owner fence refuses.
        finished = service.finish_send_now_batch(acp, "batch-BC")

        assert finished is True
        queued = [item.request_id for item in runner._queue._queue]
        assert queued == ["client-B", "older-D"]
        assert queued.count("client-B") == 1
        assert acp.calls.count("admit:client-B") == 1
        assert acp.calls.count("schedule_cancel") == 0
        assert runner._cancel_hold is False
    finally:
        release_active.set()
        _cleanup_runner(runner, active_task)
        await asyncio.gather(active_task, return_exceptions=True)
