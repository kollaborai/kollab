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
