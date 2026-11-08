"""Red conformance tests for the proposed local-only #901 admission seam.

These tests use no providers, subprocesses, network, or real session data. They
intentionally fail until the proposed module and API exist; passing them alone
would not establish cross-process or durable correctness.
"""

import asyncio
import importlib

import pytest


@pytest.fixture
def admission():
    return importlib.import_module("kollabor_ai.session_admission")


@pytest.fixture
def fence(admission):
    return admission.LocalAdmissionFence(admission.SessionKey("synthetic", "incarnation-a"))


def test_unknown_coverage_fails_closed_even_when_empty(fence):
    decision = fence.try_snapshot()
    assert decision.allowed is False
    assert decision.reason == "coverage_unverified"


def test_native_batch_stays_pending_until_exact_ordered_membership(fence, admission):
    first = fence.reserve("native")
    second = fence.reserve("native")
    fence.mark_written(first, record_id="row-a")
    fence.mark_written(second, record_id="row-b")
    assert fence.try_snapshot().reason == "pending"
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((first, second), history_id="batch", record_ids=("row-b", "row-a"))
    assert fence.state(first) is admission.AdmissionState.WRITTEN
    assert fence.state(second) is admission.AdmissionState.WRITTEN
    fence.commit_native_batch((first, second), history_id="batch", record_ids=("row-a", "row-b"))
    assert fence.state(first) is admission.AdmissionState.COMMITTED
    assert fence.state(second) is admission.AdmissionState.COMMITTED
    assert fence.try_snapshot().reason == "coverage_unverified"
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((first, second), history_id="batch", record_ids=("row-a", "row-b"))


def test_rejected_enqueue_before_write_aborts_but_after_write_cannot(fence, admission):
    rejected = fence.reserve("native")
    fence.abort_before_write(rejected)
    assert fence.state(rejected) is admission.AdmissionState.ABORTED
    logged = fence.reserve("native")
    fence.mark_written(logged, record_id="row")
    with pytest.raises(admission.AdmissionError):
        fence.abort_before_write(logged)
    assert fence.state(logged) is admission.AdmissionState.WRITTEN
    fence.mark_indeterminate(logged)
    assert fence.try_snapshot().reason == "indeterminate"


def test_ambiguous_append_and_cancellation_cannot_become_success(fence, admission):
    token = fence.reserve("native")
    fence.mark_indeterminate(token)
    assert fence.state(token) is admission.AdmissionState.INDETERMINATE
    with pytest.raises(admission.AdmissionError):
        fence.mark_written(token, record_id="maybe-row")
    assert fence.try_snapshot().allowed is False
    assert fence.try_snapshot().reason == "indeterminate"


@pytest.mark.asyncio
async def test_acp_pending_append_does_not_block_synchronous_snapshot(fence, admission):
    started = asyncio.Event()
    release = asyncio.Event()
    token = fence.reserve("acp")

    async def synthetic_append():
        started.set()
        await release.wait()
        fence.mark_written(token, record_id="acp-row")
        fence.commit_acp(token, history_id="acp-history", record_id="acp-row")

    task = asyncio.create_task(synthetic_append())
    await started.wait()
    decision = fence.try_snapshot()
    assert decision.allowed is False and decision.reason == "pending"
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert fence.state(token) is admission.AdmissionState.COMMITTED
    assert fence.try_snapshot().reason == "coverage_unverified"


def test_same_id_new_incarnation_rejects_old_token(admission):
    old = admission.LocalAdmissionFence(admission.SessionKey("synthetic", "old"))
    token = old.reserve("acp")
    new = admission.LocalAdmissionFence(admission.SessionKey("synthetic", "new"))
    with pytest.raises(admission.AdmissionError):
        new.mark_written(token, record_id="late-row")
    assert old.state(token) is admission.AdmissionState.PENDING
    assert old.try_snapshot().reason == "pending"
    assert new.try_snapshot().reason == "coverage_unverified"


def test_duplicate_batch_tokens_are_rejected_without_partial_commit(fence, admission):
    token = fence.reserve("native")
    fence.mark_written(token, record_id="row")
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((token, token), history_id="batch", record_ids=("row", "row"))
    assert fence.state(token) is admission.AdmissionState.WRITTEN


def test_distinct_tokens_cannot_share_record_id(fence, admission):
    first = fence.reserve("native")
    second = fence.reserve("native")
    fence.mark_written(first, record_id="same-row")
    with pytest.raises(admission.AdmissionError):
        fence.mark_written(second, record_id="same-row")
    assert fence.state(first) is admission.AdmissionState.WRITTEN
    assert fence.state(second) is admission.AdmissionState.PENDING
    assert fence.try_snapshot().reason == "pending"


def test_distinct_committed_batches_cannot_share_history_id(fence, admission):
    first = fence.reserve("native")
    second = fence.reserve("native")
    fence.mark_written(first, record_id="row-a")
    fence.mark_written(second, record_id="row-b")
    fence.commit_native_batch((first,), history_id="same-history", record_ids=("row-a",))
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((second,), history_id="same-history", record_ids=("row-b",))
    assert fence.state(first) is admission.AdmissionState.COMMITTED
    assert fence.state(second) is admission.AdmissionState.WRITTEN


@pytest.mark.parametrize("terminal", ["aborted", "committed", "indeterminate"])
def test_terminal_states_reject_late_transitions(fence, admission, terminal):
    token = fence.reserve("acp")
    if terminal == "aborted":
        fence.abort_before_write(token)
        expected = admission.AdmissionState.ABORTED
    elif terminal == "committed":
        fence.mark_written(token, record_id="row")
        fence.commit_acp(token, history_id="history", record_id="row")
        expected = admission.AdmissionState.COMMITTED
    else:
        fence.mark_indeterminate(token)
        expected = admission.AdmissionState.INDETERMINATE
    for operation in (
        lambda: fence.mark_written(token, record_id="late"),
        lambda: fence.abort_before_write(token),
        lambda: fence.mark_indeterminate(token),
        lambda: fence.commit_acp(token, history_id="late-history", record_id="late"),
    ):
        with pytest.raises(admission.AdmissionError):
            operation()
        assert fence.state(token) is expected


def test_snapshot_is_nonblocking_when_state_lock_is_contended(fence):
    from threading import Event, Thread

    finished = Event()
    result = []

    def capture():
        result.append(fence.try_snapshot())
        finished.set()

    fence._state_lock.acquire()
    worker = Thread(target=capture, daemon=True)
    try:
        worker.start()
        completed_while_locked = finished.wait(timeout=1)
    finally:
        fence._state_lock.release()
        worker.join(timeout=1)
    assert completed_while_locked, "snapshot waited on a contended state lock"
    assert not worker.is_alive()
    assert result[0].allowed is False and result[0].reason == "busy"


def test_capacity_refuses_new_tokens_without_forgetting_terminal_ids(admission):
    fence = admission.LocalAdmissionFence(admission.SessionKey("synthetic", "owner"), max_entries=2)
    first = fence.reserve("native")
    fence.mark_written(first, record_id="row")
    fence.commit_native_batch((first,), history_id="history", record_ids=("row",))
    second = fence.reserve("acp")
    fence.abort_before_write(second)
    with pytest.raises(admission.AdmissionError):
        fence.reserve("native")
    assert fence.state(first) is admission.AdmissionState.COMMITTED
    assert fence.state(second) is admission.AdmissionState.ABORTED
    assert fence.try_snapshot().reason == "coverage_unverified"


@pytest.mark.asyncio
async def test_cancelled_inflight_append_remains_indeterminate(fence, admission):
    started = asyncio.Event()
    token = fence.reserve("acp")

    async def simulated_append_with_unknown_outcome():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # The caller cannot prove whether an append crossed its boundary.
            fence.mark_indeterminate(token)
            raise

    task = asyncio.create_task(simulated_append_with_unknown_outcome())
    await started.wait()
    assert fence.try_snapshot().reason == "pending"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fence.state(token) is admission.AdmissionState.INDETERMINATE
    assert fence.try_snapshot().reason == "indeterminate"


@pytest.mark.parametrize("record_ids", [(), ("row-a",), ("row-a", "row-b", "extra")])
def test_incomplete_or_extra_batch_membership_is_atomic(fence, admission, record_ids):
    first = fence.reserve("native")
    second = fence.reserve("native")
    fence.mark_written(first, record_id="row-a")
    fence.mark_written(second, record_id="row-b")
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((first, second), history_id="batch", record_ids=record_ids)
    assert fence.state(first) is admission.AdmissionState.WRITTEN
    assert fence.state(second) is admission.AdmissionState.WRITTEN
    assert fence.try_snapshot().reason == "pending"


def test_acp_cannot_reuse_native_batch_history_id(fence, admission):
    native = fence.reserve("native")
    acp = fence.reserve("acp")
    fence.mark_written(native, record_id="native-row")
    fence.mark_written(acp, record_id="acp-row")
    fence.commit_native_batch((native,), history_id="history", record_ids=("native-row",))
    with pytest.raises(admission.AdmissionError):
        fence.commit_acp(acp, history_id="history", record_id="acp-row")
    assert fence.state(native) is admission.AdmissionState.COMMITTED
    assert fence.state(acp) is admission.AdmissionState.WRITTEN
