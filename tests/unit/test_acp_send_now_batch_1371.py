"""#1371: Send Now batches are admitted as one priority group."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPResult, ACPRunner
from kollabor.llm.llm_coordinator import LLMService


class _BatchACP:
    active = True
    supports_image_input = False
    voice_rules_epochs: set[object] = set()

    def __init__(
        self,
        runner: ACPRunner,
        *,
        refuse_cancel: bool = False,
        steer_results: list[object] | None = None,
    ):
        self.runner = runner
        self.refuse_cancel = refuse_cancel
        self.steer_results = list(steer_results or [])
        self.calls: list[str] = []

    async def steer(self, *_args, **_kwargs):
        self.calls.append("steer")
        return self.steer_results.pop(0) if self.steer_results else False

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

    def has_send_now_batch(self, batch_id):
        return self.runner.has_send_now_batch(batch_id)

    def send_now_batch_has_admissions(self, batch_id):
        return self.runner.send_now_batch_has_admissions(batch_id)

    def record_send_now_batch_injected(self, batch_id):
        return self.runner.record_send_now_batch_injected(batch_id)

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
    for batch in getattr(runner, "_send_now_batches", {}).values():
        if batch.expiry_handle is not None:
            batch.expiry_handle.cancel()
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
        finished = service.finish_send_now_batch("batch-BC", acp=acp)

        assert finished is True
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "client-C", "older-D",
        ]
        assert [item.request_id for item in runner._queue._queue].count("client-B") == 1
        assert [item.request_id for item in runner._queue._queue].count("client-C") == 1
        assert acp.calls.count("admit:client-B") == 1
        assert acp.calls.count("admit:client-C") == 1
        assert acp.calls.count("steer") == 1, "after B queues, C must not be injected into the owner being stopped"
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
        finished = service.finish_send_now_batch("batch-BC", acp=acp)

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


@pytest.mark.asyncio
async def test_runner_holds_older_work_if_original_owner_ends_between_batch_messages():
    runner = ACPRunner(ACPProfile("send-now-batch-gate", ("unused",)))
    runner.session_id = "session-A"
    runner.generation = 7
    runner._connection = object()
    started: asyncio.Queue[str] = asyncio.Queue()
    release = {name: asyncio.Event() for name in ("active-A", "client-B", "client-C", "older-D")}

    async def fake_run_prompt(admission, generation):
        runner._active_turn_id = admission.turn_id
        runner._active_generation = generation
        started.put_nowait(admission.request_id)
        await release[admission.request_id].wait()
        return ACPResult(
            generation=generation,
            session_id="session-A",
            text="",
            stop_reason="end_turn",
            updates=(),
            end_frame={},
            turn_id=admission.turn_id,
            request_id=admission.request_id,
            source=admission.source,
        )

    runner._run_prompt = fake_run_prompt
    runner.admit("active", request_id="active-A", source="rpc")
    runner.admit("older", request_id="older-D", source="rpc")
    worker = asyncio.create_task(runner._drain_queue())
    try:
        assert await asyncio.wait_for(started.get(), timeout=1) == "active-A"
        owner = runner._active_context
        assert runner.begin_send_now_batch(
            "batch-BC", 2,
            session_id="session-A",
            generation=runner._active_generation,
            active_context=owner,
            active_turn_id=runner._active_turn_id,
        )
        runner.admit(
            "message B", request_id="client-B", source="rpc",
            send_now_batch_id="batch-BC",
        )

        # A can finish before the engine submits C. The worker must park here;
        # D cannot slip between the two messages while C is still in flight.
        release["active-A"].set()
        await runner._active_done.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert started.empty()
        assert runner._queue_release.is_set() is False

        runner.admit(
            "message C", request_id="client-C", source="rpc",
            send_now_batch_id="batch-BC",
        )
        batch = runner.finish_send_now_batch("batch-BC")
        assert batch is not None
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "client-C", "older-D",
        ]

        for expected in ("client-B", "client-C", "older-D"):
            assert await asyncio.wait_for(started.get(), timeout=1) == expected
            release[expected].set()
        assert runner._queue.empty()
    finally:
        for event in release.values():
            event.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_send_now_batch_expiry_releases_accepted_members_and_rejects_late_member():
    runner, active_task, release_active = _active_runner()
    runner.send_now_batch_timeout_seconds = 0.05
    service = object.__new__(LLMService)
    acp = _BatchACP(runner)
    try:
        first = await _send_batch_message(
            service, acp, "client-B", "message B", 0, first=True
        )
        assert first["status"] == "queued"

        # A lost finish RPC cannot hold the queue gate forever. Expiry releases
        # the admitted prefix as one priority group ahead of older D.
        await asyncio.wait_for(runner._queue_release.wait(), timeout=1)
        assert not runner.has_send_now_batch("batch-BC")
        assert runner._send_now_reserved == 0
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "older-D",
        ]

        late = await _send_batch_message(
            service, acp, "client-C", "message C", 1
        )
        assert late["status"] == "rejected"
        assert late["reason"] == "invalid_send_now_batch"
        assert "admit:client-C" not in acp.calls
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "older-D",
        ]
    finally:
        release_active.set()
        _cleanup_runner(runner, active_task)
        await asyncio.gather(active_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_send_now_batch_renews_timeout_when_later_member_is_admitted():
    runner, active_task, release_active = _active_runner()
    runner.send_now_batch_timeout_seconds = 0.2
    service = object.__new__(LLMService)
    acp = _BatchACP(runner)
    try:
        first = await _send_batch_message(
            service, acp, "client-B", "message B", 0, first=True
        )
        assert first["status"] == "queued"
        await asyncio.sleep(0.11)

        second = await _send_batch_message(
            service, acp, "client-C", "message C", 1
        )
        assert second["status"] == "queued"
        # This extends beyond the original timeout, but the active batch still
        # owns the gate because C arrived before the renewed lease expired.
        await asyncio.sleep(0.11)
        assert runner.has_send_now_batch("batch-BC")
        assert not runner._queue_release.is_set()

        assert service.finish_send_now_batch("batch-BC", acp=acp)
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "client-C", "older-D",
        ]
    finally:
        release_active.set()
        _cleanup_runner(runner, active_task)
        await asyncio.gather(active_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_send_now_lease_stops_captured_owner_before_running_batch():
    runner = ACPRunner(ACPProfile("send-now-expiry-cancel", ("unused",)))
    runner.session_id = "session-A"
    runner.generation = 7
    runner.send_now_batch_timeout_seconds = 0.02
    started: asyncio.Queue[str] = asyncio.Queue()
    release = {name: asyncio.Event() for name in ("active-A", "client-B", "older-D")}
    cancelled: list[str] = []

    class Connection:
        async def cancel(self, *, session_id: str) -> None:
            cancelled.append(session_id)
            release["active-A"].set()

    runner._connection = Connection()

    async def fake_run_prompt(admission, generation):
        runner._active_turn_id = admission.turn_id
        runner._active_generation = generation
        started.put_nowait(admission.request_id)
        await release[admission.request_id].wait()
        stop_reason = (
            "cancelled"
            if admission.request_id == "active-A" and cancelled
            else "end_turn"
        )
        return ACPResult(
            generation=generation,
            session_id="session-A",
            text="",
            stop_reason=stop_reason,
            updates=(),
            end_frame={},
            turn_id=admission.turn_id,
            request_id=admission.request_id,
            source=admission.source,
        )

    runner._run_prompt = fake_run_prompt
    runner.admit("active", request_id="active-A", source="rpc")
    runner.admit("older", request_id="older-D", source="rpc")
    worker = asyncio.create_task(runner._drain_queue())
    try:
        assert await asyncio.wait_for(started.get(), timeout=1) == "active-A"
        owner = runner._active_context
        assert runner.begin_send_now_batch(
            "batch-BC",
            2,
            session_id="session-A",
            generation=runner._active_generation,
            active_context=owner,
            active_turn_id=runner._active_turn_id,
        )
        runner.admit(
            "message B",
            request_id="client-B",
            source="rpc",
            send_now_batch_id="batch-BC",
        )

        assert await asyncio.wait_for(started.get(), timeout=1) == "client-B"
        assert cancelled == ["session-A"]
        assert [item.request_id for item in runner._queue._queue] == ["older-D"]

        release["client-B"].set()
        assert await asyncio.wait_for(started.get(), timeout=1) == "older-D"
        assert cancelled == ["session-A"], "expiry must not stop a later queued turn"
    finally:
        for event in release.values():
            event.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_send_now_lease_does_not_stop_replacement_owner():
    runner, active_task, release_active = _active_runner()
    runner.send_now_batch_timeout_seconds = 0.02
    cancelled: list[str] = []

    class Connection:
        async def cancel(self, *, session_id: str) -> None:
            cancelled.append(session_id)

    runner._connection = Connection()
    replacement = asyncio.create_task(asyncio.Event().wait())
    try:
        assert runner.begin_send_now_batch(
            "batch-BC",
            2,
            session_id=runner.session_id,
            generation=runner.generation,
            active_context=runner._active_context,
            active_turn_id=runner._active_turn_id,
        )
        runner.admit(
            "message B",
            request_id="client-B",
            source="rpc",
            send_now_batch_id="batch-BC",
        )
        runner._active_context = SimpleNamespace(request_id="replacement-E")
        runner._active_turn_id = 13
        runner._active_rpc_task = replacement

        await asyncio.wait_for(runner._queue_release.wait(), timeout=1)
        assert cancelled == []
        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "older-D",
        ]
    finally:
        replacement.cancel()
        release_active.set()
        _cleanup_runner(runner, active_task)
        await asyncio.gather(replacement, active_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_send_now_batch_does_not_cancel_owner_after_one_member_was_injected():
    runner, active_task, release_active = _active_runner()
    service = object.__new__(LLMService)
    acp = _BatchACP(runner, steer_results=[{
        "joined_request_id": "active-A",
        "joined_turn_id": 12,
        "joined_generation": 7,
    }, False])
    try:
        injected = await _send_batch_message(
            service, acp, "client-B", "message B", 0, first=True
        )
        assert injected["status"] == "injected"
        queued = await _send_batch_message(service, acp, "client-C", "message C", 1)
        assert queued["status"] == "queued"

        finished = service.finish_send_now_batch("batch-BC", acp=acp)

        assert finished is True
        assert [item.request_id for item in runner._queue._queue] == [
            "client-C", "older-D",
        ]
        assert acp.calls.count("schedule_cancel") == 0
        assert runner._cancel_hold is False
    finally:
        release_active.set()
        _cleanup_runner(runner, active_task)
        await asyncio.gather(active_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_send_now_batch_does_not_cancel_a_replacement_owner():
    runner, active_task, release_active = _active_runner()
    service = object.__new__(LLMService)
    acp = _BatchACP(runner)
    try:
        await _send_batch_message(service, acp, "client-B", "message B", 0, first=True)
        await _send_batch_message(service, acp, "client-C", "message C", 1)
        runner._active_context = SimpleNamespace(request_id="replacement-owner")
        runner._active_turn_id = 14
        runner._active_generation = 8

        assert service.finish_send_now_batch("batch-BC", acp=acp) is True

        assert [item.request_id for item in runner._queue._queue] == [
            "client-B", "client-C", "older-D",
        ]
        assert acp.calls.count("schedule_cancel") == 0
        assert runner._cancel_hold is False
    finally:
        release_active.set()
        _cleanup_runner(runner, active_task)
        await asyncio.gather(active_task, return_exceptions=True)
