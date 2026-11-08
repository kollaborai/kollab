"""Conformance tests for the isolated local-only #901 admission seam.

These tests use no providers, subprocesses, network, or real session data. They
exercise a state machine without production integration; passing them alone
does not establish cross-process or durable correctness.
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


def test_equal_session_key_other_fence_and_forged_token_rejected(admission):
    key = admission.SessionKey("synthetic", "same-generation")
    first = admission.LocalAdmissionFence(key)
    second = admission.LocalAdmissionFence(key)
    token = first.reserve("native")
    forged = admission.AdmissionToken(token.key, token.op_id, token.kind)
    for fence, candidate in ((second, token), (first, forged)):
        with pytest.raises(admission.AdmissionError):
            fence.mark_written(candidate, record_id="row")
    assert first.state(token) is admission.AdmissionState.PENDING
    assert second.try_snapshot().reason == "coverage_unverified"


@pytest.mark.parametrize("bad", ["", " ", None, 1])
def test_invalid_record_and_history_ids_fail_without_transition(fence, admission, bad):
    token = fence.reserve("acp")
    with pytest.raises(admission.AdmissionError):
        fence.mark_written(token, record_id=bad)
    assert fence.state(token) is admission.AdmissionState.PENDING
    fence.mark_written(token, record_id="row")
    with pytest.raises(admission.AdmissionError):
        fence.commit_acp(token, history_id=bad, record_id="row")
    assert fence.state(token) is admission.AdmissionState.WRITTEN


def test_invalid_kinds_empty_batch_and_unwritten_commit_fail_closed(fence, admission):
    for kind in ("", "steer", None):
        with pytest.raises(admission.AdmissionError):
            fence.reserve(kind)
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((), history_id="history", record_ids=())
    token = fence.reserve("native")
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((token,), history_id="history", record_ids=("row",))
    with pytest.raises(admission.AdmissionError):
        fence.commit_acp(token, history_id="history", record_id="row")
    assert fence.state(token) is admission.AdmissionState.PENDING


def test_rejection_after_durable_append_is_not_an_abort(fence, admission):
    token = fence.reserve("native")
    fence.mark_written(token, record_id="logged-before-enqueue")
    # Queue rejection does not undo the append; a durable resolution is absent.
    fence.mark_indeterminate(token)
    assert fence.state(token) is admission.AdmissionState.INDETERMINATE
    assert fence.try_snapshot().reason == "indeterminate"


def test_history_callback_failure_after_mutation_is_indeterminate(fence, admission):
    token = fence.reserve("native")
    fence.mark_written(token, record_id="row")
    history = []

    def add_then_fail():
        history.append("mutated-history")
        raise RuntimeError("synthetic after mutation")

    try:
        add_then_fail()
    except RuntimeError:
        fence.mark_indeterminate(token)
    assert history == ["mutated-history"]
    assert fence.try_snapshot().reason == "indeterminate"


def test_acp_steering_is_tracked_as_acp_and_fails_closed(fence, admission):
    token = fence.reserve("acp")
    assert fence.try_snapshot().reason == "pending"
    fence.mark_written(token, record_id="steer-row")
    fence.commit_acp(token, history_id="steer-history", record_id="steer-row")
    assert fence.state(token) is admission.AdmissionState.COMMITTED
    assert fence.try_snapshot().reason == "coverage_unverified"


@pytest.mark.parametrize("key,capacity", [(("", "g"), 1), (("s", ""), 1), (("s", "g"), 0)])
def test_invalid_fence_configuration_is_rejected(admission, key, capacity):
    with pytest.raises(admission.AdmissionError):
        admission.LocalAdmissionFence(admission.SessionKey(*key), max_entries=capacity)


def test_concurrent_duplicate_record_has_one_winner(fence, admission):
    from threading import Barrier, Thread

    tokens = (fence.reserve("native"), fence.reserve("native"))
    start = Barrier(3)
    results = []

    def write(token):
        start.wait()
        try:
            fence.mark_written(token, record_id="same-row")
        except admission.AdmissionError:
            results.append("rejected")
        else:
            results.append("written")

    workers = [Thread(target=write, args=(token,)) for token in tokens]
    for worker in workers:
        worker.start()
    start.wait()
    for worker in workers:
        worker.join(timeout=1)
        assert not worker.is_alive()
    assert sorted(results) == ["rejected", "written"]
    assert {fence.state(token) for token in tokens} == {
        admission.AdmissionState.PENDING,
        admission.AdmissionState.WRITTEN,
    }
    assert fence.try_snapshot().reason == "pending"


@pytest.mark.parametrize("origin", ["copy", "other-fence", "forged"])
def test_every_token_method_rejects_foreign_or_fabricated(fence, admission, origin):
    genuine = fence.reserve("native")
    if origin == "copy":
        candidate = admission.AdmissionToken(genuine.key, genuine.op_id, genuine.kind)
    elif origin == "other-fence":
        other = admission.LocalAdmissionFence(genuine.key)
        candidate = other.reserve("native")
    else:
        candidate = admission.AdmissionToken(genuine.key, "fabricated", "native")
    operations = (
        lambda: fence.state(candidate),
        lambda: fence.mark_written(candidate, record_id="row"),
        lambda: fence.abort_before_write(candidate),
        lambda: fence.mark_indeterminate(candidate),
        lambda: fence.commit_acp(candidate, history_id="history", record_id="row"),
        lambda: fence.commit_native_batch((candidate,), history_id="history", record_ids=("row",)),
    )
    for operation in operations:
        with pytest.raises(admission.AdmissionError):
            operation()
        assert fence.state(genuine) is admission.AdmissionState.PENDING


def test_repeated_mark_written_and_wrong_kind_commit_are_rejected(fence, admission):
    native = fence.reserve("native")
    acp = fence.reserve("acp")
    fence.mark_written(native, record_id="native")
    fence.mark_written(acp, record_id="acp")
    with pytest.raises(admission.AdmissionError):
        fence.mark_written(native, record_id="another")
    with pytest.raises(admission.AdmissionError):
        fence.commit_acp(native, history_id="history", record_id="native")
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((acp,), history_id="history", record_ids=("acp",))
    assert fence.state(native) is admission.AdmissionState.WRITTEN
    assert fence.state(acp) is admission.AdmissionState.WRITTEN


def test_native_cannot_reuse_acp_history_id(fence, admission):
    acp = fence.reserve("acp")
    native = fence.reserve("native")
    fence.mark_written(acp, record_id="acp")
    fence.mark_written(native, record_id="native")
    fence.commit_acp(acp, history_id="history", record_id="acp")
    with pytest.raises(admission.AdmissionError):
        fence.commit_native_batch((native,), history_id="history", record_ids=("native",))
    assert fence.state(native) is admission.AdmissionState.WRITTEN


@pytest.mark.parametrize("capacity", [-1, 0, 4097, True, "1", None])
def test_invalid_max_entries_rejected(admission, capacity):
    with pytest.raises(admission.AdmissionError):
        admission.LocalAdmissionFence(admission.SessionKey("s", "g"), max_entries=capacity)


def test_all_methods_refuse_without_waiting_when_lock_is_held(fence, admission):
    from threading import Event, Thread

    token = fence.reserve("native")
    finished = Event()
    outcomes = []

    def call_methods():
        operations = (
            lambda: fence.reserve("native"),
            lambda: fence.state(token),
            lambda: fence.mark_written(token, record_id="row"),
            lambda: fence.abort_before_write(token),
            lambda: fence.mark_indeterminate(token),
            lambda: fence.commit_acp(token, history_id="history", record_id="row"),
            lambda: fence.commit_native_batch((token,), history_id="history", record_ids=("row",)),
        )
        try:
            for operation in operations:
                try:
                    operation()
                except admission.AdmissionError as exc:
                    outcomes.append(str(exc))
                else:
                    outcomes.append("unexpected success")
            outcomes.append(fence.try_snapshot().reason)
        finally:
            finished.set()

    fence._state_lock.acquire()
    worker = Thread(target=call_methods, daemon=True)
    try:
        worker.start()
        completed_while_locked = finished.wait(timeout=1)
    finally:
        fence._state_lock.release()
        worker.join(timeout=1)
    assert completed_while_locked, "an admission method waited on the state lock"
    assert not worker.is_alive()
    assert outcomes == ["admission fence busy"] * 7 + ["busy"]
    assert fence.state(token) is admission.AdmissionState.PENDING


def test_concurrent_reservations_obey_capacity(admission):
    from threading import Barrier, Thread

    fence = admission.LocalAdmissionFence(admission.SessionKey("s", "g"), max_entries=1)
    barrier = Barrier(3)
    results = []

    def reserve():
        barrier.wait()
        try:
            results.append(fence.reserve("native"))
        except admission.AdmissionError:
            results.append(None)

    workers = [Thread(target=reserve) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=1)
        assert not worker.is_alive()
    tokens = [token for token in results if token is not None]
    assert len(tokens) == 1
    assert fence.state(tokens[0]) is admission.AdmissionState.PENDING
    with pytest.raises(admission.AdmissionError):
        fence.reserve("native")


def test_concurrent_commits_cannot_share_history(fence, admission):
    from threading import Barrier, Thread

    tokens = (fence.reserve("native"), fence.reserve("native"))
    fence.mark_written(tokens[0], record_id="one")
    fence.mark_written(tokens[1], record_id="two")
    barrier = Barrier(3)
    results = []

    def commit(token, record):
        barrier.wait()
        try:
            fence.commit_native_batch((token,), history_id="same", record_ids=(record,))
        except admission.AdmissionError:
            results.append("rejected")
        else:
            results.append("committed")

    workers = [Thread(target=commit, args=pair) for pair in zip(tokens, ("one", "two"))]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=1)
        assert not worker.is_alive()
    assert sorted(results) == ["committed", "rejected"]
    assert {fence.state(token) for token in tokens} == {
        admission.AdmissionState.WRITTEN,
        admission.AdmissionState.COMMITTED,
    }


@pytest.mark.parametrize("stage", ["written", "commit", "indeterminate"])
def test_busy_after_side_effect_requires_local_retry_only(fence, admission, stage):
    token = fence.reserve("native")
    effects = ["record-appended-once"]
    if stage == "written":

        def operation():
            fence.mark_written(token, record_id="row")

        before = admission.AdmissionState.PENDING
        after = admission.AdmissionState.WRITTEN
    elif stage == "commit":
        fence.mark_written(token, record_id="row")
        effects.append("history-mutated-once")

        def operation():
            fence.commit_native_batch((token,), history_id="history", record_ids=("row",))

        before = admission.AdmissionState.WRITTEN
        after = admission.AdmissionState.COMMITTED
    else:

        def operation():
            fence.mark_indeterminate(token)

        before = admission.AdmissionState.PENDING
        after = admission.AdmissionState.INDETERMINATE

    fence._state_lock.acquire()
    try:
        with pytest.raises(admission.AdmissionError, match="busy"):
            operation()
        assert fence.try_snapshot().reason == "busy"
    finally:
        fence._state_lock.release()
    assert fence.state(token) is before
    assert fence.try_snapshot().reason == "pending"
    operation()  # retry only the in-memory transition, not the external effect
    assert fence.state(token) is after
    assert effects.count("record-appended-once") == 1
    assert effects.count("history-mutated-once") == (1 if stage == "commit" else 0)
    assert fence.try_snapshot().allowed is False
