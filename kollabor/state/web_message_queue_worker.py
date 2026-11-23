"""Serial, fail-closed dispatch worker for daemon-owned WebUI queue batches.

This module deliberately accepts an injected admission dispatcher. It does not
register an RPC, bind an Engine/provider, persist queue data, or infer provider
success from a completed model turn. The daemon must retain this worker and its
queue for the lifetime of an active dispatch and close the worker before
shutting down the injected provider.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, TypeAlias

from kollabor.state.web_message_queue import (
    BatchNotFound,
    ClaimNotFound,
    ClaimOutcome,
    ClaimPayload,
    QueueEmpty,
    QueueReceipt,
    WebMessageQueue,
)

_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class ConversationIdentity:
    """Trusted daemon-captured identity for one dispatch run."""

    uid: str
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.uid, str) or not self.uid:
            raise ValueError("conversation uid must be a nonempty string")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise ValueError("conversation revision must be an integer")
        if self.revision < 0:
            raise ValueError("conversation revision must be nonnegative")


class RecordingStatus(StrEnum):
    """Conversation-history result reported separately from admission."""

    RECORDED = "recorded"
    UNKNOWN = "unknown"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AdmissionAccepted:
    """Typed proof that downstream custody accepted this exact message.

    `custody_id` identifies the downstream admission receipt. It says nothing
    about model completion. History recording can fail after ACP custody, so
    `recording_status` is carried independently and defaults to unknown.
    """

    conversation_uid: str
    conversation_revision: int
    request_id: str
    custody_id: str
    recording_status: RecordingStatus = RecordingStatus.UNKNOWN


@dataclass(frozen=True, slots=True)
class AdmissionRejectedBeforeCustody:
    """Typed proof that no downstream custody or side effect occurred."""

    conversation_uid: str
    conversation_revision: int
    request_id: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class DispatchBatchStart:
    """Trusted metadata needed to prepare a whole Send Now batch once."""

    conversation_uid: str
    conversation_revision: int
    queue_scope_id: str
    queue_incarnation: str
    batch_id: str
    claim_id: str
    request_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    """One exact queue item passed to trusted in-process admission code."""

    conversation_uid: str
    conversation_revision: int
    request_id: str
    queue_scope_id: str
    queue_incarnation: str
    queue_revision: int
    batch_id: str
    claim_id: str
    attempt_id: str
    payload: bytes = field(repr=False)
    attachment_refs: tuple[str, ...] = field(default=(), repr=False)
    send_now: bool = False
    batch_lease: object | None = field(default=None, repr=False)


AdmissionResult: TypeAlias = AdmissionAccepted | AdmissionRejectedBeforeCustody
Dispatcher: TypeAlias = Callable[[DispatchRequest], Awaitable[AdmissionResult]]


class WorkerStatus(StrEnum):
    ADMITTED = "admitted"
    EMPTY = "empty"
    BUSY = "busy"
    NOT_SENT = "not_sent"
    IN_DOUBT = "in_doubt"
    STOPPED = "stopped"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class WorkerItemResult:
    request_id: str
    attempt_id: str
    outcome: ClaimOutcome
    custody_id: str | None = None
    recording_status: RecordingStatus | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerBatchResult:
    scope_id: str
    incarnation: str
    batch_id: str
    claim_id: str | None
    status: WorkerStatus
    revision: int
    items: tuple[WorkerItemResult, ...] = ()


@dataclass(slots=True)
class _ActiveAttempt:
    attempt_id: str
    request_id: str
    dispatch_boundary_crossed: bool = False
    settlement_intent: ClaimOutcome | None = None


CurrentIdentityReader: TypeAlias = Callable[
    [], ConversationIdentity | Awaitable[ConversationIdentity]
]


class WebMessageQueueWorker:
    """Dispatch one claimed queue batch serially through an injected adapter.

    The current-identity reader is a preflight guard only. The injected
    dispatcher must atomically revalidate the captured UID and revision at its
    own admission boundary before causing side effects. The worker cannot make
    a check-then-admit sequence atomic across an external provider.

    Calls are serialized per worker. This primitive does not retry uncertain
    dispatches, poll for work, invoke native Stop, or own provider shutdown.
    """

    def __init__(
        self,
        queue: WebMessageQueue,
        identity: ConversationIdentity,
        current_identity: CurrentIdentityReader,
        dispatcher: Dispatcher,
    ) -> None:
        if not isinstance(queue, WebMessageQueue):
            raise TypeError("queue must be a WebMessageQueue")
        if not isinstance(identity, ConversationIdentity):
            raise TypeError("identity must be a ConversationIdentity")
        if not callable(current_identity) or not callable(dispatcher):
            raise TypeError("identity reader and dispatcher must be callable")
        self._queue = queue
        self._identity = identity
        self._current_identity = current_identity
        self._dispatcher = dispatcher
        self._scope_id = queue.scope_id
        self._incarnation = queue.incarnation
        self._run_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._run_requested = False
        self._closing = False
        self._closed = False
        self._forced_in_doubt = False
        self._forced_terminalized = False
        self._forced_worker_status: WorkerStatus | None = None
        self._stop_requested = False
        self._active_task: asyncio.Task[WorkerBatchResult] | None = None
        self._claim_id: str | None = None
        self._batch_id: str | None = None
        self._batch_dispatched = False
        self._accepted_count = 0
        self._active_attempt: _ActiveAttempt | None = None
        self._revision = 0
        self._results: list[WorkerItemResult] = []
        self._dispatch_send_now = False
        self._dispatch_batch_lease: object | None = None
        self._dispatch_batch_finisher: Callable[..., Any] | None = None

    def request_stop(self) -> None:
        """Stop before the next item; an in-flight dispatch is not cancelled.

        Native Stop is global. This method only preserves this queue batch's
        unsubmitted suffix after the active dispatcher returns its own outcome.
        """

        self._stop_requested = True

    async def dispatch_batch(
        self,
        batch_id: str,
        *,
        send_now: bool = False,
    ) -> WorkerBatchResult:
        """Claim and dispatch one batch. No item is automatically retried."""

        if self._run_requested:
            return self._unstarted_result(batch_id, WorkerStatus.BUSY)
        self._run_requested = True
        try:
            async with self._run_lock:
                result = await self._dispatch_batch_locked(
                    batch_id, send_now=send_now
                )
                if (
                    self._dispatch_batch_lease is not None
                    and result.status is not WorkerStatus.IN_DOUBT
                ):
                    # A provider custody prefix may already be accepted, but
                    # the batch finalizer did not prove release of its gate.
                    # Report that cleanup uncertainty instead of success.
                    result = replace(result, status=WorkerStatus.IN_DOUBT)
                return result
        finally:
            self._run_requested = False

    async def _dispatch_batch_locked(
        self,
        batch_id: str,
        *,
        send_now: bool,
    ) -> WorkerBatchResult:
        if self._closing or self._closed:
            return self._unstarted_result(batch_id, WorkerStatus.CLOSED)
        if self._dispatch_batch_lease is not None:
            return self._result(
                self._batch_id or batch_id,
                self._claim_id,
                WorkerStatus.IN_DOUBT,
            )
        if self._active_attempt is not None:
            # A previous run may have crossed admission but failed while
            # recording its terminal receipt. Keep that exact attempt pinned
            # for close() recovery; starting another run would overwrite the
            # only safe settlement intent and could orphan a claimed item.
            return self._result(
                self._batch_id or batch_id,
                self._claim_id,
                WorkerStatus.IN_DOUBT,
            )
        self._active_task = asyncio.current_task()
        self._batch_id = batch_id
        self._claim_id = None
        self._batch_dispatched = False
        self._accepted_count = 0
        self._active_attempt = None
        self._revision = 0
        self._results = []
        self._dispatch_send_now = send_now
        claim: QueueReceipt | None = None
        try:
            if self._forced_in_doubt:
                return self._forced_result(batch_id, None)
            if self._closing or self._closed:
                return self._result(batch_id, None, WorkerStatus.CLOSED)
            if self._stop_requested:
                return self._result(batch_id, None, WorkerStatus.STOPPED)
            identity_matches = await self._identity_matches()
            if self._forced_in_doubt:
                return self._forced_result(batch_id, None)
            if self._closing or self._closed:
                return self._result(batch_id, None, WorkerStatus.CLOSED)
            if self._stop_requested:
                return self._result(batch_id, None, WorkerStatus.STOPPED)
            if not identity_matches:
                return self._result(batch_id, None, WorkerStatus.NOT_SENT)

            operation_id = self._queue.new_operation_id()
            try:
                claim = await self._queue.claim_batch(
                    operation_id, batch_id, send_now=send_now
                )
            except BatchNotFound:
                return self._result(batch_id, None, WorkerStatus.EMPTY)
            self._claim_id = claim.claim_id
            if not self._valid_batch_claim(claim, batch_id, operation_id):
                if claim.claim_id:
                    await self._release_claim(claim.claim_id)
                return self._result(batch_id, claim.claim_id, WorkerStatus.IN_DOUBT)
            self._revision = claim.revision
            if self._forced_in_doubt:
                await self._release_claim(claim.claim_id or "")
                return self._forced_result(batch_id, claim.claim_id)
            if self._closing or self._closed:
                await self._release_claim(claim.claim_id or "")
                return self._result(batch_id, claim.claim_id, WorkerStatus.CLOSED)
            if self._stop_requested:
                await self._release_claim(claim.claim_id or "")
                return self._result(batch_id, claim.claim_id, WorkerStatus.STOPPED)

            expected_ids = claim.request_ids
            if not expected_ids or len(set(expected_ids)) != len(expected_ids):
                await self._release_claim(claim.claim_id or "")
                return self._result(batch_id, claim.claim_id, WorkerStatus.IN_DOUBT)

            if send_now:
                begin_batch = getattr(self._dispatcher, "begin_batch", None)
                finish_batch = getattr(self._dispatcher, "finish_batch", None)
                if not callable(begin_batch) or not callable(finish_batch):
                    await self._release_claim(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)
                batch_start = DispatchBatchStart(
                    conversation_uid=self._identity.uid,
                    conversation_revision=self._identity.revision,
                    queue_scope_id=self._scope_id,
                    queue_incarnation=self._incarnation,
                    batch_id=claim.batch_id or batch_id,
                    claim_id=claim.claim_id or "",
                    request_ids=expected_ids,
                )
                try:
                    batch_lease = begin_batch(batch_start)
                    if inspect.isawaitable(batch_lease):
                        batch_lease = await batch_lease
                except asyncio.CancelledError:
                    # begin_batch owns cleanup if it is cancelled after it
                    # changes provider state. No item dispatcher has run yet.
                    await self._release_claim(claim.claim_id or "")
                    raise
                except Exception:
                    # A failed batch preflight is before any item admission;
                    # the suffix remains queue-owned and may be retried.
                    await self._release_claim(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)
                if batch_lease is None:
                    await self._release_claim(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)
                self._dispatch_batch_lease = batch_lease
                self._dispatch_batch_finisher = finish_batch

            while True:
                if self._forced_in_doubt:
                    return self._forced_result(batch_id, claim.claim_id)
                if self._closing or self._closed:
                    await self._preserve_suffix(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.CLOSED)
                if self._stop_requested:
                    await self._preserve_suffix(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.STOPPED)
                identity_matches = await self._identity_matches()
                if self._forced_in_doubt:
                    return self._forced_result(batch_id, claim.claim_id)
                if self._closing or self._closed:
                    await self._preserve_suffix(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.CLOSED)
                if self._stop_requested:
                    await self._preserve_suffix(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.STOPPED)
                if not identity_matches:
                    await self._preserve_suffix(claim.claim_id or "")
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)

                try:
                    item_claim = await self._queue.claim_next_item(
                        self._queue.new_operation_id(), claim.claim_id or ""
                    )
                except QueueEmpty:
                    terminal = (
                        WorkerStatus.ADMITTED
                        if self._accepted_count == len(expected_ids)
                        else WorkerStatus.IN_DOUBT
                    )
                    return self._result(batch_id, claim.claim_id, terminal)
                except ClaimNotFound:
                    if self._forced_in_doubt:
                        return self._forced_result(batch_id, claim.claim_id)
                    if self._closing or self._closed:
                        return self._result(
                            batch_id, claim.claim_id, WorkerStatus.CLOSED
                        )
                    if self._accepted_count == len(expected_ids):
                        return self._result(
                            batch_id, claim.claim_id, WorkerStatus.ADMITTED
                        )
                    return self._result(batch_id, claim.claim_id, WorkerStatus.IN_DOUBT)
                self._batch_dispatched = True
                expected_request_id = (
                    expected_ids[self._accepted_count]
                    if self._accepted_count < len(expected_ids)
                    else None
                )
                if not self._valid_item_claim(item_claim, claim, expected_request_id):
                    attempt_id = item_claim.attempt_id
                    if isinstance(item_claim.revision, int) and not isinstance(
                        item_claim.revision, bool
                    ):
                        self._revision = max(self._revision, item_claim.revision)
                    request_id = (
                        item_claim.request_ids[0]
                        if len(item_claim.request_ids) == 1
                        else "invalid-item"
                    )
                    if attempt_id:
                        self._active_attempt = _ActiveAttempt(attempt_id, request_id)
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)

                request_id = item_claim.request_ids[0]
                attempt_id = item_claim.attempt_id or ""
                self._revision = item_claim.revision
                active = _ActiveAttempt(attempt_id, request_id)
                self._active_attempt = active

                if self._forced_in_doubt:
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._forced_result(batch_id, claim.claim_id)
                if self._closing or self._closed:
                    await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.CLOSED)
                if self._stop_requested:
                    await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.STOPPED)

                try:
                    payload = await self._queue.item_payload(attempt_id)
                except asyncio.CancelledError:
                    if not self._forced_in_doubt and self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    raise
                except Exception:
                    if self._forced_in_doubt:
                        return self._forced_result(batch_id, claim.claim_id)
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    if self._closing or self._closed:
                        return self._result(
                            batch_id, claim.claim_id, WorkerStatus.CLOSED
                        )
                    if self._stop_requested:
                        return self._result(
                            batch_id, claim.claim_id, WorkerStatus.STOPPED
                        )
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)

                if self._forced_in_doubt:
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._forced_result(batch_id, claim.claim_id)
                if self._closing or self._closed:
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.CLOSED)
                if self._stop_requested:
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.STOPPED)
                if not self._valid_payload(payload, item_claim, claim):
                    await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)

                if self._stop_requested:
                    await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.STOPPED)
                if self._closing or self._closed:
                    await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.CLOSED)
                identity_matches = await self._identity_matches()
                if self._forced_in_doubt:
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._forced_result(batch_id, claim.claim_id)
                if self._closing or self._closed:
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.CLOSED)
                if self._stop_requested:
                    if self._active_attempt is active:
                        await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.STOPPED)
                if not identity_matches:
                    await self._settle(ClaimOutcome.NOT_SENT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)

                message = payload.items[0]
                dispatch_request = DispatchRequest(
                    conversation_uid=self._identity.uid,
                    conversation_revision=self._identity.revision,
                    request_id=request_id,
                    queue_scope_id=self._scope_id,
                    queue_incarnation=self._incarnation,
                    queue_revision=item_claim.revision,
                    batch_id=claim.batch_id or batch_id,
                    claim_id=claim.claim_id or "",
                    attempt_id=attempt_id,
                    payload=message.payload,
                    attachment_refs=message.attachment_refs,
                    send_now=self._dispatch_send_now,
                    batch_lease=self._dispatch_batch_lease,
                )
                active.dispatch_boundary_crossed = True
                try:
                    admission = await self._dispatcher(dispatch_request)
                except asyncio.CancelledError:
                    if not self._forced_in_doubt:
                        await self._settle(ClaimOutcome.IN_DOUBT)
                    raise
                except Exception:
                    await self._settle(ClaimOutcome.IN_DOUBT)
                    return self._result(batch_id, claim.claim_id, WorkerStatus.IN_DOUBT)

                if self._forced_in_doubt:
                    return self._result(batch_id, claim.claim_id, WorkerStatus.IN_DOUBT)
                outcome, accepted_result = self._classify_admission(
                    admission, dispatch_request
                )
                await self._settle(outcome)
                if accepted_result is not None:
                    self._results.append(
                        WorkerItemResult(
                            request_id=request_id,
                            attempt_id=attempt_id,
                            outcome=ClaimOutcome.ACCEPTED,
                            custody_id=accepted_result.custody_id,
                            recording_status=accepted_result.recording_status,
                        )
                    )
                elif outcome is ClaimOutcome.NOT_SENT and isinstance(
                    admission, AdmissionRejectedBeforeCustody
                ):
                    self._results.append(
                        WorkerItemResult(
                            request_id=request_id,
                            attempt_id=attempt_id,
                            outcome=ClaimOutcome.NOT_SENT,
                            reason_code=(
                                admission.reason_code
                                if _CODE.fullmatch(admission.reason_code)
                                else None
                            ),
                        )
                    )
                else:
                    self._results.append(
                        WorkerItemResult(
                            request_id=request_id,
                            attempt_id=attempt_id,
                            outcome=ClaimOutcome.IN_DOUBT,
                        )
                    )
                if outcome is ClaimOutcome.ACCEPTED:
                    self._accepted_count += 1
                    if self._accepted_count == len(expected_ids):
                        return self._result(
                            batch_id, claim.claim_id, WorkerStatus.ADMITTED
                        )
                    continue
                if outcome is ClaimOutcome.NOT_SENT:
                    return self._result(batch_id, claim.claim_id, WorkerStatus.NOT_SENT)
                return self._result(batch_id, claim.claim_id, WorkerStatus.IN_DOUBT)
        except asyncio.CancelledError:
            # Cancellation before the dispatcher boundary is proven not
            # sent. After invocation it is ambiguous and must never replay.
            if claim is not None and not self._forced_in_doubt:
                try:
                    await self._cleanup_claim_after_cancel(claim)
                except Exception:
                    # Keep the original cancellation visible. The queue
                    # remains claimed/in-doubt if cleanup itself failed.
                    pass
            raise
        finally:
            if self._dispatch_batch_lease is not None:
                try:
                    self._finish_dispatch_batch_lease()
                except Exception:
                    # The provider gate is still server-owned. Retain the
                    # lease in the worker instead of silently starting a
                    # competing run over a possibly open batch.
                    self._forced_in_doubt = True
            self._active_task = None
            if self._active_attempt is None and not (
                self._forced_in_doubt and not self._forced_terminalized
            ):
                self._clear_run_state()

    async def close(self, *, timeout: float = 1.0) -> bool:
        """Stop new runs and wait at most `timeout` for the active run.

        On timeout, the worker retries any retained settlement intent; without
        one, an active attempt is settled `in_doubt` if dispatcher invocation
        began, or `not_sent` otherwise. A non-cooperative injected dispatcher
        may remain alive. `False` means the caller has not confirmed
        both worker termination and terminal queue state; it must not register a
        replacement over the attempt, and must keep the provider alive while the
        task can still call it. Caller cancellation is deferred until the
        terminalization attempt and worker cancellation request have completed,
        then propagated; if the worker is still active, its provider must remain
        alive. If terminalization fails, `close()` returns `False` and retains
        the worker settlement state so a later call can retry it.
        """

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a finite nonnegative number")
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be a finite nonnegative number")
        async with self._close_lock:
            self._closing = True
            task = self._active_task
            if task is asyncio.current_task():
                return False
            task_done = task is None or task.done()
            terminalization_pending = (
                self._forced_in_doubt and not self._forced_terminalized
            ) or (task_done and self._active_attempt is not None)
            batch_finalization_pending = self._dispatch_batch_lease is not None
            if (
                task_done
                and not terminalization_pending
                and not batch_finalization_pending
            ):
                self._closed = True
                return True

            cancellation: asyncio.CancelledError | None = None

            def defer_cancellation(exc: asyncio.CancelledError) -> None:
                nonlocal cancellation
                if cancellation is None:
                    cancellation = exc
                current = asyncio.current_task()
                if current is not None:
                    while current.cancelling():
                        current.uncancel()

            if not task_done:
                try:
                    done, _ = await asyncio.wait({task}, timeout=float(timeout))
                except asyncio.CancelledError as exc:
                    defer_cancellation(exc)
                    done = set()
                task_done = bool(done)
                terminalization_pending = (
                    self._forced_in_doubt and not self._forced_terminalized
                ) or (task_done and self._active_attempt is not None)
                batch_finalization_pending = self._dispatch_batch_lease is not None
                if (
                    task_done
                    and not terminalization_pending
                    and not batch_finalization_pending
                ):
                    self._closed = True
                    if cancellation is not None:
                        raise cancellation
                    return True

            self._forced_in_doubt = True
            terminalized = self._forced_terminalized
            if not terminalized:
                terminalized = True
                while True:
                    try:
                        self._forced_worker_status = await self._force_terminal_state()
                        self._forced_terminalized = True
                        break
                    except asyncio.CancelledError as exc:
                        # Queue mutations are atomic under their lock. If
                        # cancellation interrupted the lock wait, retry the
                        # terminalization in this task before propagating it.
                        defer_cancellation(exc)
                    except Exception:
                        terminalized = False
                        active = self._active_attempt
                        self._forced_worker_status = (
                            WorkerStatus.IN_DOUBT
                            if active is not None and active.dispatch_boundary_crossed
                            else WorkerStatus.CLOSED
                        )
                        break

            batch_finalized = True
            if self._dispatch_batch_lease is not None:
                try:
                    self._finish_dispatch_batch_lease()
                except Exception:
                    batch_finalized = False
            self._closed = batch_finalized
            if task is not None:
                if task.done():
                    task_done = True
                else:
                    task.cancel()
                    while True:
                        try:
                            done, _ = await asyncio.wait({task}, timeout=0)
                            break
                        except asyncio.CancelledError as exc:
                            defer_cancellation(exc)
                    task_done = bool(done)
            completed = task_done and terminalized and batch_finalized
            if completed and self._forced_terminalized:
                self._clear_run_state()
            if cancellation is not None:
                raise cancellation
            return completed

    def _clear_run_state(self) -> None:
        self._batch_id = None
        self._claim_id = None
        self._batch_dispatched = False
        self._accepted_count = 0
        self._active_attempt = None
        self._revision = 0
        self._results = []
        self._dispatch_send_now = False
        if self._dispatch_batch_lease is None:
            self._dispatch_batch_finisher = None

    def _finish_dispatch_batch_lease(self) -> bool:
        """Finish the server-owned batch gate or retain it for explicit retry."""
        lease = self._dispatch_batch_lease
        finisher = self._dispatch_batch_finisher
        if lease is None:
            return True
        if finisher is None:
            raise RuntimeError("batch finalizer is unavailable")
        uncertain = any(
            result.outcome is ClaimOutcome.IN_DOUBT for result in self._results
        ) or bool(
            self._active_attempt is not None
            and self._active_attempt.dispatch_boundary_crossed
        )
        finished = finisher(
            lease,
            accepted_count=self._accepted_count,
            uncertain=uncertain,
        )
        if inspect.isawaitable(finished):
            raise TypeError("finish_batch must not yield")
        if finished is not True:
            raise RuntimeError("finish_batch did not prove cleanup")
        self._dispatch_batch_lease = None
        self._dispatch_batch_finisher = None
        return True

    async def _identity_matches(self) -> bool:
        try:
            current = self._current_identity()
            if inspect.isawaitable(current):
                current = await current
        except Exception:
            return False
        return current == self._identity

    def _valid_batch_claim(
        self, receipt: QueueReceipt, batch_id: str, operation_id: str
    ) -> bool:
        if not isinstance(receipt, QueueReceipt):
            return False
        return (
            receipt.scope_id == self._scope_id
            and receipt.incarnation == self._incarnation
            and receipt.operation_id == operation_id
            and receipt.batch_id == batch_id
            and receipt.status == "dispatching"
            and bool(receipt.claim_id)
            and receipt.attempt_id is None
            and isinstance(receipt.revision, int)
            and not isinstance(receipt.revision, bool)
            and receipt.revision > 0
        )

    def _valid_item_claim(
        self,
        receipt: QueueReceipt,
        batch_claim: QueueReceipt,
        expected_request_id: str | None,
    ) -> bool:
        if not isinstance(receipt, QueueReceipt):
            return False
        return (
            receipt.scope_id == self._scope_id
            and receipt.incarnation == self._incarnation
            and receipt.batch_id == batch_claim.batch_id
            and receipt.claim_id == batch_claim.claim_id
            and receipt.status == "dispatching"
            and len(receipt.request_ids) == 1
            and expected_request_id is not None
            and receipt.request_ids[0] == expected_request_id
            and bool(receipt.attempt_id)
            and receipt.attempt_id == receipt.operation_id
            and isinstance(receipt.revision, int)
            and not isinstance(receipt.revision, bool)
            and receipt.revision > self._revision
        )

    def _valid_payload(
        self,
        payload: ClaimPayload,
        item_claim: QueueReceipt,
        batch_claim: QueueReceipt,
    ) -> bool:
        if not isinstance(payload, ClaimPayload):
            return False
        return (
            payload.scope_id == self._scope_id
            and payload.incarnation == self._incarnation
            and payload.batch_id == batch_claim.batch_id
            and payload.claim_id == batch_claim.claim_id
            and payload.attempt_id == item_claim.attempt_id
            and len(payload.items) == 1
            and payload.items[0].request_id == item_claim.request_ids[0]
            and isinstance(payload.items[0].payload, bytes)
        )

    def _classify_admission(
        self,
        admission: object,
        request: DispatchRequest,
    ) -> tuple[ClaimOutcome, AdmissionAccepted | None]:
        if isinstance(admission, AdmissionAccepted):
            if (
                admission.conversation_uid == request.conversation_uid
                and isinstance(admission.conversation_revision, int)
                and not isinstance(admission.conversation_revision, bool)
                and admission.conversation_revision == request.conversation_revision
                and admission.request_id == request.request_id
                and isinstance(admission.custody_id, str)
                and bool(admission.custody_id)
                and len(admission.custody_id) <= 256
                and isinstance(admission.recording_status, RecordingStatus)
            ):
                return ClaimOutcome.ACCEPTED, admission
            return ClaimOutcome.IN_DOUBT, None
        if isinstance(admission, AdmissionRejectedBeforeCustody):
            if (
                admission.conversation_uid == request.conversation_uid
                and isinstance(admission.conversation_revision, int)
                and not isinstance(admission.conversation_revision, bool)
                and admission.conversation_revision == request.conversation_revision
                and admission.request_id == request.request_id
                and isinstance(admission.reason_code, str)
                and _CODE.fullmatch(admission.reason_code) is not None
            ):
                return ClaimOutcome.NOT_SENT, None
            return ClaimOutcome.IN_DOUBT, None
        return ClaimOutcome.IN_DOUBT, None

    async def _settle(self, outcome: ClaimOutcome) -> QueueReceipt:
        active = self._active_attempt
        if active is None:
            raise ClaimNotFound("worker has no active item attempt")
        previous_intent = active.settlement_intent
        active.settlement_intent = outcome
        # Settlement is itself idempotent by exact attempt + outcome. Retry
        # the same definitive intent after a lost receipt; downgrading to
        # in_doubt could conflict with an accepted/not_sent receipt that already
        # committed before its acknowledgement was lost.
        receipt = await self._queue.settle_item_from_worker(
            active.attempt_id, outcome
        )
        expected_status = {
            ClaimOutcome.ACCEPTED: ClaimOutcome.ACCEPTED.value,
            ClaimOutcome.NOT_SENT: "queued",
            ClaimOutcome.IN_DOUBT: "in_doubt",
        }[outcome]
        if (
            receipt.scope_id != self._scope_id
            or receipt.incarnation != self._incarnation
            or receipt.attempt_id != active.attempt_id
            or receipt.request_ids != (active.request_id,)
            or receipt.claim_id != self._claim_id
            or receipt.status != expected_status
            or receipt.revision < self._revision
            or (receipt.revision == self._revision and previous_intent is not outcome)
        ):
            # If the queue mutation returned a mismatched receipt, trusting it
            # could silently lose a prefix or replay a submitted item.
            raise RuntimeError("worker settlement receipt failed validation")
        await self._queue.acknowledge_worker_settlement(
            active.attempt_id, outcome, receipt
        )
        self._revision = receipt.revision
        self._active_attempt = None
        return receipt

    async def _release_claim(self, claim_id: str) -> None:
        if not claim_id:
            return
        receipt = await self._queue.release_claim(
            self._queue.new_operation_id(), claim_id
        )
        if (
            receipt.scope_id != self._scope_id
            or receipt.incarnation != self._incarnation
            or receipt.claim_id != claim_id
            or receipt.batch_id != self._batch_id
            or receipt.status != "queued"
            or receipt.revision <= self._revision
        ):
            raise RuntimeError("worker release receipt failed validation")
        self._revision = receipt.revision

    async def _preserve_suffix(self, claim_id: str) -> None:
        if not self._batch_dispatched:
            await self._release_claim(claim_id)
            return
        try:
            item_claim = await self._queue.claim_next_item(
                self._queue.new_operation_id(), claim_id
            )
        except QueueEmpty, ClaimNotFound:
            return
        if (
            item_claim.scope_id != self._scope_id
            or item_claim.incarnation != self._incarnation
            or item_claim.batch_id != self._batch_id
            or item_claim.claim_id != claim_id
            or item_claim.status != "dispatching"
            or not item_claim.attempt_id
            or item_claim.attempt_id != item_claim.operation_id
            or len(item_claim.request_ids) != 1
            or item_claim.revision <= self._revision
        ):
            raise RuntimeError("worker suffix claim failed validation")
        self._revision = item_claim.revision
        self._active_attempt = _ActiveAttempt(
            item_claim.attempt_id, item_claim.request_ids[0]
        )
        await self._settle(ClaimOutcome.NOT_SENT)

    async def _cleanup_claim_after_cancel(self, claim: QueueReceipt) -> None:
        active = self._active_attempt
        if active is not None:
            outcome = active.settlement_intent or (
                ClaimOutcome.IN_DOUBT
                if active.dispatch_boundary_crossed
                else ClaimOutcome.NOT_SENT
            )
            await self._settle(outcome)
        elif claim.claim_id:
            await self._preserve_suffix(claim.claim_id)

    async def _force_terminal_state(self) -> WorkerStatus:
        active = self._active_attempt
        if active is not None:
            outcome = active.settlement_intent or (
                ClaimOutcome.IN_DOUBT
                if active.dispatch_boundary_crossed
                else ClaimOutcome.NOT_SENT
            )
            await self._settle(outcome)
            if outcome is ClaimOutcome.ACCEPTED:
                await self._preserve_suffix(self._claim_id or "")
                return WorkerStatus.CLOSED
            if outcome is ClaimOutcome.IN_DOUBT:
                return WorkerStatus.IN_DOUBT
            return WorkerStatus.NOT_SENT
        if self._claim_id:
            await self._preserve_suffix(self._claim_id)
        return WorkerStatus.CLOSED

    def _result(
        self,
        batch_id: str,
        claim_id: str | None,
        status: WorkerStatus,
    ) -> WorkerBatchResult:
        return WorkerBatchResult(
            scope_id=self._scope_id,
            incarnation=self._incarnation,
            batch_id=batch_id,
            claim_id=claim_id,
            status=status,
            revision=self._revision,
            items=tuple(self._results),
        )

    def _unstarted_result(
        self, batch_id: str, status: WorkerStatus
    ) -> WorkerBatchResult:
        return WorkerBatchResult(
            scope_id=self._scope_id,
            incarnation=self._incarnation,
            batch_id=batch_id,
            claim_id=None,
            status=status,
            revision=0,
            items=(),
        )

    def _forced_result(self, batch_id: str, claim_id: str | None) -> WorkerBatchResult:
        return self._result(
            batch_id,
            claim_id,
            self._forced_worker_status or WorkerStatus.CLOSED,
        )
