"""Owner-fenced ACP batch handoff regressions for #1366."""

from __future__ import annotations

import asyncio
import sys

import pytest

from kollabor.llm.acp_bridge import (
    ACPAdmission,
    ACPError,
    ACPProfile,
    ACPResult,
    ACPRunner,
)


class _Connection:
    def __init__(self):
        self.cancelled = asyncio.Event()
        self.cancel_calls = 0
        self.fail_cancel = False

    async def cancel(self, *, session_id):
        assert session_id == "session-a"
        self.cancel_calls += 1
        if self.fail_cancel:
            raise ConnectionError("cancel response lost")
        self.cancelled.set()

    async def close(self):
        return None


class _PausingGetQueue(asyncio.Queue):
    """Pause after Queue.get removes one admission but before it returns."""

    def __init__(self):
        super().__init__(maxsize=10)
        self.get_entered = asyncio.Event()
        self.item_removed = asyncio.Event()
        self.allow_return = asyncio.Event()

    async def get(self):
        self.get_entered.set()
        item = await super().get()
        if getattr(item, "request_id", None) == "ordinary":
            self.item_removed.set()
            await self.allow_return.wait()
        return item


def _ready_runner() -> ACPRunner:
    runner = ACPRunner(ACPProfile("queue-test", (sys.executable,)))
    runner.session_id = "session-a"
    runner.generation = 3
    runner._connection = _Connection()
    return runner


def _result(admission: ACPAdmission, generation: int, stop_reason: str) -> ACPResult:
    return ACPResult(
        generation=generation,
        session_id="session-a",
        text=admission.request_id,
        stop_reason=stop_reason,
        updates=(),
        end_frame={"stopReason": stop_reason},
        turn_id=admission.turn_id,
        request_id=admission.request_id,
        source=admission.source,
        thread_id=admission.thread_id,
    )


@pytest.mark.asyncio
async def test_forced_batch_reserves_full_capacity_before_stopping_active_turn():
    runner = _ready_runner()
    for index in range(9):
        runner.admit(f"old-{index}", request_id=f"old-{index}", source="web")
    token = await runner.begin_queue_handoff_batch(("forced-b", "forced-c"))

    assert token is None
    assert runner._queue.qsize() == 9
    assert runner._queue_release.is_set()
    assert runner._connection.cancel_calls == 0


@pytest.mark.asyncio
async def test_forced_batch_cancels_one_owner_then_precedes_older_fifo_without_gap():
    runner = _ready_runner()
    started: list[str] = []
    active_started = asyncio.Event()
    finish_active = asyncio.Event()

    async def run_prompt(admission: ACPAdmission, generation: int) -> ACPResult:
        started.append(admission.request_id)
        if admission.request_id == "active":
            active_started.set()
            await finish_active.wait()
            return _result(admission, generation, "cancelled")
        return _result(admission, generation, "end_turn")

    runner._run_prompt = run_prompt
    worker = asyncio.create_task(runner._drain_queue())
    active = runner.admit("active", request_id="active", source="web")
    await asyncio.wait_for(active_started.wait(), timeout=1)
    old_b = runner.admit("old B", request_id="old-b", source="web")
    old_c = runner.admit("old C", request_id="old-c", source="web")

    begin = asyncio.create_task(
        runner.begin_queue_handoff_batch(("forced-b", "forced-c"))
    )
    try:
        await asyncio.wait_for(runner._connection.cancelled.wait(), timeout=1)
        assert runner._queue_release.is_set() is False
        assert started == ["active"]
        assert runner._queue.qsize() == 2
        with pytest.raises(ACPError, match="forced batch"):
            runner.admit("ordinary", request_id="ordinary", source="web")
        assert runner._queue.qsize() == 2

        finish_active.set()
        token = await asyncio.wait_for(begin, timeout=1)
        assert token is not None
        assert started == ["active"]
        assert runner._queue_release.is_set() is False

        forced_b = runner.admit(
            "forced B",
            request_id="forced-b",
            source="web",
            queue_handoff_batch=token,
        )
        forced_c = runner.admit(
            "forced C",
            request_id="forced-c",
            source="web",
            queue_handoff_batch=token,
        )
        assert started == ["active"]
        assert [item.request_id for item in runner._queue._queue] == [
            "forced-b",
            "forced-c",
            "old-b",
            "old-c",
        ]

        assert runner.finish_queue_handoff_batch(
            token, accepted_count=2, uncertain=False
        ) is True
        assert [
            result.request_id
            for result in await asyncio.gather(
                forced_b.future,
                forced_c.future,
                old_b.future,
                old_c.future,
            )
        ] == ["forced-b", "forced-c", "old-b", "old-c"]
        assert started == ["active", "forced-b", "forced-c", "old-b", "old-c"]
        assert runner._connection.cancel_calls == 1
    finally:
        finish_active.set()
        if not begin.done():
            begin.cancel()
        await asyncio.gather(begin, return_exceptions=True)
        runner._closing = True
        runner._queue_release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_batch_gate_serializes_idle_waiter_with_dequeue_and_normal_admit():
    runner = _ready_runner()
    started: list[str] = []

    async def run_prompt(admission: ACPAdmission, generation: int) -> ACPResult:
        started.append(admission.request_id)
        return _result(admission, generation, "end_turn")

    runner._run_prompt = run_prompt
    worker = asyncio.create_task(runner._drain_queue())
    try:
        if hasattr(runner, "_dequeue_waiting_for_input"):
            # The gated dequeue implementation must never park inside Queue.get.
            await asyncio.wait_for(
                runner._dequeue_waiting_for_input.wait(), timeout=1
            )
            token = await runner.begin_queue_handoff_batch(("forced",))
            assert token is not None
            with pytest.raises(ACPError, match="forced batch"):
                runner.admit("ordinary", request_id="ordinary", source="web")
        else:
            # Reproduce the old race: the getter removes an ordinary item, but
            # the worker has not yet recorded it as the active owner.
            paused = _PausingGetQueue()
            runner._queue = paused
            await asyncio.wait_for(paused.get_entered.wait(), timeout=1)
            runner.admit("ordinary", request_id="ordinary", source="web")
            await asyncio.wait_for(paused.item_removed.wait(), timeout=1)
            begin = asyncio.create_task(
                runner.begin_queue_handoff_batch(("forced",))
            )
            await asyncio.sleep(0)
            paused.allow_return.set()
            token = await asyncio.wait_for(begin, timeout=1)
            assert token is not None

        forced = runner.admit(
            "forced", request_id="forced", source="web", queue_handoff_batch=token
        )
        assert started == []
        assert runner.finish_queue_handoff_batch(
            token, accepted_count=1, uncertain=False
        ) is True
        assert (await forced.future).request_id == "forced"
        assert started == ["forced"]
    finally:
        runner._closing = True
        runner._queue_release.set()
        runner._queue_changed.set() if hasattr(runner, "_queue_changed") else None
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_ambiguous_cancel_keeps_token_and_queue_held():
    runner = _ready_runner()
    runner._connection.fail_cancel = True
    active_started = asyncio.Event()
    finish_active = asyncio.Event()

    async def run_prompt(admission: ACPAdmission, generation: int) -> ACPResult:
        active_started.set()
        await finish_active.wait()
        return _result(admission, generation, "cancelled")

    runner._run_prompt = run_prompt
    worker = asyncio.create_task(runner._drain_queue())
    active = runner.admit("active", request_id="active", source="web")
    await asyncio.wait_for(active_started.wait(), timeout=1)
    old = runner.admit("old", request_id="old", source="web")

    with pytest.raises(ACPError, match="outcome is uncertain"):
        await runner.begin_queue_handoff_batch(("forced-b", "forced-c"))

    batch = runner._queue_handoff_batch
    assert batch is not None and batch.in_doubt
    assert runner._queue_release.is_set() is False
    assert [item.request_id for item in runner._queue._queue] == ["old"]
    with pytest.raises(ACPError, match="forced batch"):
        runner.admit("next", request_id="next", source="web")
    with pytest.raises(ACPError, match="invalid forced queue batch"):
        runner.admit(
            "forced", request_id="forced-b", source="web", queue_handoff_batch=batch
        )
    assert runner.finish_queue_handoff_batch(
        batch, accepted_count=0, uncertain=False
    ) is False
    assert runner._queue_release.is_set() is False
    finish_active.set()
    try:
        await asyncio.wait_for(active.future, timeout=1)
    except ACPError:
        pass
    finally:
        runner._closing = True
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        if not old.future.done():
            old.future.cancel()


@pytest.mark.asyncio
async def test_batch_rejects_duplicate_ids_and_foreign_runner_token():
    runner = _ready_runner()
    duplicate = await runner.begin_queue_handoff_batch(("same", "same"))
    assert duplicate is None
    assert runner._queue_release.is_set()
    assert runner._connection.cancel_calls == 0

    token = await runner.begin_queue_handoff_batch(("forced",))
    assert token is not None
    foreign = _ready_runner()
    with pytest.raises(ACPError, match="invalid forced queue batch"):
        foreign.admit(
            "forced", request_id="forced", source="web", queue_handoff_batch=token
        )
    assert foreign.finish_queue_handoff_batch(
        token, accepted_count=0, uncertain=False
    ) is False
    assert foreign._queue_release.is_set()
    assert runner.finish_queue_handoff_batch(
        token, accepted_count=0, uncertain=False
    ) is True
