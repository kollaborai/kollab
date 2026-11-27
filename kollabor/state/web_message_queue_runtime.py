"""Compose the daemon-owned WebUI queue, dispatcher, and bounded supervisor."""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from kollabor_rpc import RpcCallerContext, current_rpc_caller_context

from .web_message_attachments import (
    AttachmentAuthorizationError,
    AttachmentScopeBinding,
    WebMessageAttachmentRegistry,
    verified_image_mime,
)
from .web_message_queue import (
    BatchStatus,
    ItemConflict,
    ItemNotFound,
    ItemNotQueued,
    ItemStatus,
    WebMessage,
)
from .web_message_queue_dispatch import WebMessageQueueDispatcher
from .web_message_queue_rpc import (
    QueueRpcAuthorizationError,
    QueueScopeBinding,
    ScopeResolver,
    WebMessageQueueRegistry,
    _KnownQueueScope,
    daemon_conversation_scope_resolver,
    register_web_message_queue_handlers,
)
from .web_message_queue_worker import (
    ConversationIdentity,
    WebMessageQueueWorker,
    WorkerBatchResult,
    WorkerStatus,
)

logger = logging.getLogger(__name__)
_TURN_IDLE_RETRY_SECONDS = 2.0
_TURN_IDLE_RETRY_INTERVAL_SECONDS = 0.025
# ACP emits turn_complete before awaiting its Hub completion callback, which is
# bounded to 10 seconds by the orchestrator. Keep the queue wake attached to
# that task's settlement, with a small scheduling margin.
_ACP_COMPLETION_SETTLE_SECONDS = 10.5


def _attachment_resolver(
    registry: WebMessageQueueRegistry,
    resolve_scope: ScopeResolver,
):
    async def resolve(
        params: dict[str, Any], caller_context: object
    ) -> AttachmentScopeBinding:
        if (
            not isinstance(caller_context, RpcCallerContext)
            or not caller_context.is_local_operator
            or not isinstance(caller_context.principal_id, str)
            or not caller_context.principal_id
        ):
            raise AttachmentAuthorizationError("attachment access denied")
        try:
            value = resolve_scope(params, caller_context)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            raise AttachmentAuthorizationError("attachment access denied") from None
        if isinstance(value, QueueScopeBinding):
            scope_id = value.scope_id
            conversation_uid = value.conversation_uid
        elif isinstance(value, str):
            scope_id = value
            conversation_uid = None
        else:
            raise AttachmentAuthorizationError("attachment access denied")
        try:
            async with registry.acquire(
                scope_id,
                principal_id=caller_context.principal_id,
                conversation_uid=conversation_uid,
            ) as queue:
                return AttachmentScopeBinding(scope_id, queue.incarnation)
        except Exception:
            raise AttachmentAuthorizationError("attachment access denied") from None

    return resolve


@dataclass(slots=True)
class WebMessageQueueRuntime:
    """Daemon-lifetime queue state and one bounded dispatcher supervisor.

    The supervisor consumes only metadata invalidations. It scans the bounded
    registry for authoritative state, and never receives message bytes through
    the notification channel. Each provider dispatch is serialized per trusted
    principal/conversation scope by the worker and admission adapter.
    """

    registry: WebMessageQueueRegistry = field(repr=False)
    attachments: WebMessageAttachmentRegistry = field(repr=False)
    state_service: Any = field(repr=False)
    resolve_scope: ScopeResolver = field(repr=False)
    dispatch_available: bool = False
    _changes: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1), repr=False
    )
    _scope_locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)
    _active_workers: set[WebMessageQueueWorker] = field(default_factory=set, repr=False)
    _worker_close_tasks: dict[WebMessageQueueWorker, asyncio.Task[bool]] = field(
        default_factory=dict, repr=False
    )
    _supervisor_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _turn_idle_retry_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _turn_complete_generation: int = field(default=0, repr=False)
    _acp_completion_wake_callbacks: dict[
        asyncio.Future[Any], Callable[[asyncio.Future[Any]], None]
    ] = field(default_factory=dict, repr=False)
    _close_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _event_loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _display_tap: Any = field(default=None, repr=False)
    _display_tap_listener: Any = field(default=None, repr=False)
    _closing: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.dispatch_available = self._has_dispatch_adapter()
        self._ensure_supervisor()

    def _has_dispatch_adapter(self) -> bool:
        return all(
            callable(getattr(self.state_service, name, None))
            for name in (
                "get_conversation_identity",
                "submit_queue_message",
                "begin_queue_handoff_batch",
                "finish_queue_handoff_batch",
            )
        )

    def _send_now_available(self) -> bool:
        if not self.dispatch_available or self._closing:
            return False
        llm = getattr(self.state_service, "_llm_service", None)
        if llm is None:
            return True
        try:
            active = getattr(llm, "_active_acp", None)
            selected = getattr(llm, "_profile_selects_acp", None)
            selected_acp, unknown = selected() if callable(selected) else (False, True)
            active_acp = active() if callable(active) else None
            if selected is None and active is None:
                # Small native adapters may have no ACP selection surface at
                # all. The real LLM service exposes both methods; if either is
                # present but cannot resolve the provider, keep failing closed.
                return True
            if unknown:
                return False
            if selected_acp or active_acp is not None:
                # ACP must own a live runner and the LLM layer must expose its
                # atomic batch gate. The LocalStateService adapter alone is
                # not proof that the selected provider can reserve a batch.
                return (
                    active_acp is not None
                    and callable(getattr(llm, "begin_queue_handoff_batch", None))
                    and callable(getattr(llm, "finish_queue_handoff_batch", None))
                )
            return True
        except Exception:
            return False

    def _ensure_supervisor(self) -> None:
        if self._closing:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._event_loop = loop
        self._subscribe_turn_complete(loop)
        if self._supervisor_task and not self._supervisor_task.done():
            return
        self._supervisor_task = loop.create_task(
            self._supervise(), name="web_message_queue_supervisor"
        )

    def _subscribe_turn_complete(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._display_tap_listener is not None:
            return
        try:
            from kollabor_tui.display_tap import resolve_tap

            tap = resolve_tap(getattr(self.state_service, "_event_bus", None))
        except Exception:
            logger.debug("WebUI queue could not resolve DisplayTap", exc_info=True)
            return
        add_listener = getattr(tap, "add_listener", None)
        if not callable(add_listener):
            return

        def listener(event: dict[str, Any]) -> None:
            if not isinstance(event, dict) or event.get("type") != "turn_complete":
                return
            try:
                loop.call_soon_threadsafe(self._on_turn_complete)
            except RuntimeError:
                logger.debug("WebUI queue turn-complete wake arrived after loop close")

        try:
            add_listener(listener)
        except Exception:
            logger.debug("WebUI queue DisplayTap subscription failed", exc_info=True)
            return
        self._display_tap = tap
        self._display_tap_listener = listener

    def _on_turn_complete(self) -> None:
        if self._closing:
            return
        self._turn_complete_generation += 1
        self._watch_acp_completion_tasks(self._active_acp_completion_tasks())
        self.wake_all()
        if not self._is_processing():
            return
        task = self._turn_idle_retry_task
        if task is None or task.done():
            loop = self._event_loop
            if loop is not None and loop.is_running():
                self._turn_idle_retry_task = loop.create_task(
                    self._retry_until_idle(), name="web_message_queue_idle_retry"
                )

    def _active_acp_completion_tasks(self) -> tuple[asyncio.Future[Any], ...]:
        llm = getattr(self.state_service, "_llm_service", None)
        active = getattr(llm, "_active_acp", None)
        try:
            orchestrator = active() if callable(active) else None
            if orchestrator is None:
                # _active_acp() deliberately hides a closing/replacing
                # orchestrator, but its completion tasks still own the busy
                # state until shutdown settles. Observe the registered service
                # so the queue wake follows that lifecycle boundary too.
                resolve_service = getattr(llm, "_acp_service", None)
                orchestrator = resolve_service() if callable(resolve_service) else None
            tasks = getattr(orchestrator, "_completion_tasks", ())
            return tuple(
                task
                for task in tasks
                if isinstance(task, asyncio.Future) and not task.done()
            )
        except Exception:
            logger.debug(
                "WebUI queue could not inspect ACP completion tasks", exc_info=True
            )
            return ()

    def _watch_acp_completion_tasks(
        self, tasks: tuple[asyncio.Future[Any], ...]
    ) -> bool:
        loop = self._event_loop
        if loop is None or not loop.is_running() or self._closing:
            return False
        added = False
        for task in tasks:
            if task in self._acp_completion_wake_callbacks:
                continue

            def settled(
                completed: asyncio.Future[Any],
                *,
                target_loop: asyncio.AbstractEventLoop = loop,
            ) -> None:
                try:
                    target_loop.call_soon_threadsafe(
                        self._on_acp_completion_settled, completed
                    )
                except RuntimeError:
                    logger.debug("WebUI queue ACP completion settled after loop close")

            self._acp_completion_wake_callbacks[task] = settled
            task.add_done_callback(settled)
            added = True
        return added

    def _on_acp_completion_settled(self, task: asyncio.Future[Any]) -> None:
        self._acp_completion_wake_callbacks.pop(task, None)
        if not self._closing:
            self.wake_all()

    def _unsubscribe_acp_completion_wakes(self) -> None:
        callbacks = self._acp_completion_wake_callbacks
        self._acp_completion_wake_callbacks = {}
        for task, callback in callbacks.items():
            task.remove_done_callback(callback)

    async def _retry_until_idle(self) -> None:
        loop = asyncio.get_running_loop()
        generation = self._turn_complete_generation
        pending = self._active_acp_completion_tasks()
        self._watch_acp_completion_tasks(pending)
        deadline = loop.time() + (
            _ACP_COMPLETION_SETTLE_SECONDS if pending else _TURN_IDLE_RETRY_SECONDS
        )
        try:
            while not self._closing and self._is_processing():
                pending = self._active_acp_completion_tasks()
                if self._watch_acp_completion_tasks(pending):
                    deadline = max(
                        deadline, loop.time() + _ACP_COMPLETION_SETTLE_SECONDS
                    )
                if generation != self._turn_complete_generation:
                    generation = self._turn_complete_generation
                    deadline = max(
                        deadline,
                        loop.time()
                        + (
                            _ACP_COMPLETION_SETTLE_SECONDS
                            if pending
                            else _TURN_IDLE_RETRY_SECONDS
                        ),
                    )
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                if pending:
                    await asyncio.wait(
                        pending,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    await asyncio.sleep(
                        min(_TURN_IDLE_RETRY_INTERVAL_SECONDS, remaining)
                    )
            if not self._closing and not self._is_processing():
                self.wake_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("WebUI queue idle retry failed", exc_info=True)

    def _unsubscribe_turn_complete(self) -> None:
        tap = self._display_tap
        listener = self._display_tap_listener
        self._display_tap = None
        self._display_tap_listener = None
        remove_listener = getattr(tap, "remove_listener", None)
        if callable(remove_listener) and listener is not None:
            try:
                remove_listener(listener)
            except Exception:
                logger.debug("WebUI queue DisplayTap unsubscribe failed", exc_info=True)

    def wake_all(self) -> None:
        """Coalesce a bounded wake hint; the registry remains authoritative."""
        if self._closing:
            return
        self._ensure_supervisor()
        if self._changes.full():
            return
        self._changes.put_nowait({"kind": "scan"})

    async def close(self, *, timeout: float = 1.0) -> bool:
        """Bound shutdown while retaining unresolved worker ownership for retry."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a finite nonnegative number")
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be a finite nonnegative number")

        self._closing = True
        self._unsubscribe_turn_complete()
        self._unsubscribe_acp_completion_wakes()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        remaining = max(0.0, deadline - loop.time())
        if self._close_lock.locked():
            if remaining == 0.0:
                return False
            try:
                await asyncio.wait_for(self._close_lock.acquire(), timeout=remaining)
            except asyncio.TimeoutError:
                return False
        else:
            await self._close_lock.acquire()
        try:
            return await self._close_locked(loop, deadline)
        finally:
            self._close_lock.release()

    async def _close_locked(
        self, loop: asyncio.AbstractEventLoop, deadline: float
    ) -> bool:
        retry_task = self._turn_idle_retry_task
        retry_done = retry_task is None or retry_task.done()
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
            done, _ = await asyncio.wait(
                {retry_task}, timeout=max(0.0, deadline - loop.time())
            )
            retry_done = bool(done)

        workers = set(self._active_workers) | set(self._worker_close_tasks)
        close_tasks: set[asyncio.Task[bool]] = set()
        for worker in workers:
            task = self._worker_close_tasks.get(worker)
            if task is not None and task.done():
                try:
                    settled = task.result() is True
                except asyncio.CancelledError, Exception:
                    settled = False
                if settled:
                    self._worker_close_tasks.pop(worker, None)
                    self._active_workers.discard(worker)
                    continue
                task = None
            if task is None:
                remaining = max(0.0, deadline - loop.time())
                task = loop.create_task(
                    worker.close(timeout=remaining),
                    name="web_message_queue_worker_close",
                )
                self._worker_close_tasks[worker] = task
            close_tasks.add(task)

        if close_tasks:
            await asyncio.wait(close_tasks, timeout=max(0.0, deadline - loop.time()))

        workers_ok = True
        for worker, task in tuple(self._worker_close_tasks.items()):
            if not task.done():
                workers_ok = False
                continue
            try:
                settled = task.result() is True
            except asyncio.CancelledError, Exception:
                settled = False
            if settled:
                self._worker_close_tasks.pop(worker, None)
                self._active_workers.discard(worker)
            else:
                workers_ok = False

        supervisor = self._supervisor_task
        if supervisor is not None and not supervisor.done():
            supervisor.cancel()
            done, _ = await asyncio.wait(
                {supervisor}, timeout=max(0.0, deadline - loop.time())
            )
            supervisor_done = bool(done)
        else:
            supervisor_done = True
        if supervisor is not None and supervisor.done():
            try:
                supervisor.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "WebUI queue supervisor failed during close", exc_info=True
                )

        return (
            retry_done
            and workers_ok
            and supervisor_done
            and not self._active_workers
            and not self._worker_close_tasks
        )

    async def input_scope(self, conversation_uid: str) -> tuple[str, str, str]:
        """Authenticate and bind a chat input to its principal and conversation."""
        binding = await self._resolve_trusted_scope(
            {"expected_conversation_uid": conversation_uid}
        )
        caller = current_rpc_caller_context()
        assert isinstance(caller, RpcCallerContext)
        return binding.scope_id, binding.conversation_uid, caller.principal_id

    async def capabilities(self, params: dict[str, Any]) -> dict[str, bool]:
        self._ensure_supervisor()
        await self._resolve_trusted_scope(params)
        return {"dispatch_available": self._send_now_available()}

    async def add_input(self, params: dict[str, Any]) -> dict[str, Any]:
        """Admit one client-identified input into its conversation queue."""
        self._ensure_supervisor()
        input_id = params.get("id")
        text = params.get("text")
        attachments = params.get("attachments", [])
        goal_reply_id = params.get("goal_reply_id")
        if (
            not isinstance(input_id, str)
            or not input_id
            or not isinstance(text, str)
            or not isinstance(attachments, list)
            or len(attachments) > 20
        ):
            return {"accepted": False, "reason": "invalid_input"}
        if not text.strip() and not attachments:
            return {"accepted": False, "reason": "empty_input"}

        from .goal_replies import GOAL_REPLY_PATTERN, is_goal_input

        if is_goal_input(text):
            if (
                not isinstance(goal_reply_id, str)
                or GOAL_REPLY_PATTERN.fullmatch(goal_reply_id) is None
            ):
                return {"accepted": False, "reason": "invalid_goal_reply"}
        elif goal_reply_id is not None:
            return {"accepted": False, "reason": "invalid_goal_reply"}

        identity = await self._current_identity()
        if identity is None:
            return {"accepted": False, "reason": "conversation_unavailable"}
        scoped_params = {"expected_conversation_uid": identity.uid}
        binding = await self._resolve_trusted_scope(scoped_params)
        caller = current_rpc_caller_context()
        assert isinstance(caller, RpcCallerContext)
        envelope = {"text": text}
        if goal_reply_id is not None:
            envelope["goal_reply_id"] = goal_reply_id
        payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )

        async with self.registry.acquire(
            binding.scope_id,
            principal_id=caller.principal_id,
            conversation_uid=binding.conversation_uid,
        ) as queue:
            snapshot = await queue.snapshot()
            existing = next(
                (
                    item
                    for batch in snapshot.batches
                    for item in batch.items
                    if item.request_id == input_id
                ),
                None,
            )
            if existing is not None:
                try:
                    existing_payload = json.loads(existing.payload.decode("utf-8"))
                except UnicodeDecodeError, json.JSONDecodeError:
                    existing_payload = None
                if existing_payload != envelope or len(
                    existing.attachment_refs
                ) != len(attachments):
                    return {"accepted": False, "reason": "input_id_conflict"}
                return {
                    "accepted": True,
                    "id": input_id,
                    "state": "queued",
                    "attachment_refs": list(existing.attachment_refs),
                }

            attachment_refs: list[str] = []
            if attachments:
                attachment_scope = await self.attachments.bind_scope(
                    scoped_params, caller
                )
                for attachment in attachments:
                    if not isinstance(attachment, dict) or any(
                        not isinstance(attachment.get(key), str) or not attachment[key]
                        for key in ("name", "content_type", "data_url")
                    ):
                        return {"accepted": False, "reason": "invalid_attachment"}
                    data_url = attachment["data_url"]
                    header, separator, encoded = data_url.partition(",")
                    prefix = "data:"
                    marker = ";base64"
                    if (
                        not separator
                        or not header.startswith(prefix)
                        or not header.endswith(marker)
                        or header[len(prefix) : -len(marker)].lower()
                        != attachment["content_type"].lower()
                    ):
                        return {"accepted": False, "reason": "invalid_attachment"}
                    try:
                        image = base64.b64decode(encoded, validate=True)
                        mime = verified_image_mime(attachment["content_type"], image)
                        staged = await attachment_scope.stage(
                            attachment_scope.new_operation_id(),
                            mime,
                            image,
                            filename=attachment["name"],
                        )
                    except (
                        AttachmentAuthorizationError,
                        ValueError,
                        binascii.Error,
                    ):
                        return {"accepted": False, "reason": "invalid_attachment"}
                    attachment_refs.append(staged.ref)

            batch_id = queue.new_batch_id()
            operation_id = queue.new_operation_id()
            message = WebMessage(input_id, payload, tuple(attachment_refs))
            try:
                if attachment_refs:
                    receipt = await queue.enqueue_batch_with_attachments(
                        operation_id,
                        batch_id,
                        (message,),
                        attachment_scope=attachment_scope,
                    )
                else:
                    receipt = await queue.enqueue_batch(
                        operation_id, batch_id, (message,)
                    )
            except ItemConflict:
                return {"accepted": False, "reason": "input_id_conflict"}

        self.wake_all()
        return {
            "accepted": True,
            "id": input_id,
            "state": "queued",
            "batch_id": receipt.batch_id,
            "attachment_refs": attachment_refs,
        }

    async def cancel_queued_input(self, params: dict[str, Any]) -> dict[str, Any]:
        """Remove one queued input while preserving its item identity tombstone."""
        self._ensure_supervisor()
        input_id = params.get("id")
        if not isinstance(input_id, str) or not input_id:
            return {"found": False, "cancelled": False}
        identity = await self._current_identity()
        if identity is None:
            return {"found": False, "cancelled": False}
        expected_uid = params.get("expected_conversation_uid")
        if expected_uid is not None and expected_uid != identity.uid:
            return {"found": False, "cancelled": False}
        scoped_params = {"expected_conversation_uid": identity.uid}
        binding = await self._resolve_trusted_scope(scoped_params)
        caller = current_rpc_caller_context()
        if not isinstance(caller, RpcCallerContext):
            return {"found": False, "cancelled": False}
        scopes = await self.registry.known_scopes()
        matches = [
            scope
            for scope in scopes
            if scope.scope_id == binding.scope_id
            and scope.conversation_uid == binding.conversation_uid
            and scope.principal_id == caller.principal_id
        ]
        if len(matches) != 1:
            return {"found": False, "cancelled": False}

        async with self.registry.acquire_existing_scope(matches[0]) as queue:
            if queue is None:
                return {"found": False, "cancelled": False}
            snapshot = await queue.snapshot()
            located = next(
                (
                    (batch, item)
                    for batch in snapshot.batches
                    for item in batch.items
                    if item.request_id == input_id
                ),
                None,
            )
            if located is None:
                return {"found": False, "cancelled": False}
            batch, item = located
            if (
                batch.status is not BatchStatus.QUEUED
                or item.status is not ItemStatus.QUEUED
            ):
                return {"found": True, "cancelled": False}
            operation_id = queue.new_operation_id()
            try:
                if item.attachment_refs:
                    attachment_scope = await self.attachments.bind_scope(
                        scoped_params, caller
                    )
                    receipt = await queue.remove_item_with_attachments(
                        operation_id,
                        input_id,
                        attachment_scope=attachment_scope,
                    )
                else:
                    receipt = await queue.remove_item(operation_id, input_id)
            except ItemNotFound, ItemNotQueued:
                return {"found": True, "cancelled": False}
        self.wake_all()
        return {"found": True, "cancelled": receipt.status == "removed"}

    async def send_now(self, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_supervisor()
        input_id = params.get("id")
        if isinstance(input_id, str) and input_id:
            identity = await self._current_identity()
            if identity is None:
                return {"found": False}
            scoped_params = {"expected_conversation_uid": identity.uid}
            binding = await self._resolve_trusted_scope(scoped_params)
            caller = current_rpc_caller_context()
            if not isinstance(caller, RpcCallerContext):
                return {"found": False}
            known = await self.registry.known_scopes()
            matches = [
                scope
                for scope in known
                if scope.scope_id == binding.scope_id
                and scope.conversation_uid == binding.conversation_uid
                and scope.principal_id == caller.principal_id
            ]
            if len(matches) != 1:
                return {"found": False}
            async with self.registry.acquire_existing_scope(matches[0]) as queue:
                if queue is None:
                    return {"found": False}
                snapshot = await queue.snapshot()
                batch = next(
                    (
                        batch
                        for batch in snapshot.batches
                        if batch.status is BatchStatus.QUEUED
                        and input_id in batch.request_ids
                    ),
                    None,
                )
                if batch is None:
                    found = any(
                        input_id in batch.request_ids for batch in snapshot.batches
                    )
                    return {
                        "found": found,
                        "accepted": False,
                        "state": "queued",
                    }
            if not self._send_now_available():
                return {
                    "found": True,
                    "accepted": False,
                    "state": "queued",
                    "reason": "dispatch_unavailable",
                }
            result = await self._dispatch_scope(
                matches[0], batch_id=batch.batch_id, send_now=True, request_id=input_id
            )
            return {
                "found": result.get("status") != WorkerStatus.EMPTY.value,
                "accepted": result.get("accepted") is True,
                "state": "queued",
            }

        binding = await self._resolve_trusted_scope(params)
        batch_id = params.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("batch_id is required")
        if not self._send_now_available():
            return {"status": WorkerStatus.NOT_SENT.value, "accepted": False}
        known = await self.registry.known_scopes()
        matches = [
            scope
            for scope in known
            if scope.scope_id == binding.scope_id
            and scope.conversation_uid == binding.conversation_uid
            and scope.principal_id == current_rpc_caller_context().principal_id
        ]
        if len(matches) != 1:
            return {"status": WorkerStatus.EMPTY.value, "accepted": False}
        return await self._dispatch_scope(matches[0], batch_id=batch_id, send_now=True)

    async def _resolve_trusted_scope(self, params: dict[str, Any]) -> QueueScopeBinding:
        caller_context = current_rpc_caller_context()
        if (
            not isinstance(caller_context, RpcCallerContext)
            or not caller_context.is_local_operator
            or not isinstance(caller_context.principal_id, str)
            or not caller_context.principal_id
        ):
            raise QueueRpcAuthorizationError("queue access denied")
        try:
            value = self.resolve_scope(params, caller_context)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            raise QueueRpcAuthorizationError("queue access denied") from None
        if (
            not isinstance(value, QueueScopeBinding)
            or not value.scope_id
            or not value.conversation_uid
        ):
            raise QueueRpcAuthorizationError("queue access denied")
        return value

    def _is_processing(self) -> bool:
        value = getattr(self.state_service, "is_processing", None)
        if value is None:
            value = getattr(
                getattr(self.state_service, "_llm_service", None),
                "is_processing",
                False,
            )
        return value is True

    async def _current_identity(self) -> ConversationIdentity | None:
        reader = getattr(self.state_service, "get_conversation_identity", None)
        if not callable(reader):
            return None
        try:
            value = reader()
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            return None
        return value if isinstance(value, ConversationIdentity) else None

    async def _supervise(self) -> None:
        while not self._closing:
            try:
                await self._changes.get()
                await self._dispatch_known_scopes()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("WebUI queue supervisor pass failed closed")

    async def _dispatch_known_scopes(self) -> None:
        if not self.dispatch_available or self._is_processing():
            return
        for binding in await self.registry.known_scopes():
            if self._closing or self._is_processing():
                return
            await self._dispatch_scope(binding, batch_id=None, send_now=False)

    async def _dispatch_scope(
        self,
        binding: _KnownQueueScope,
        *,
        batch_id: str | None,
        send_now: bool,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if self._closing:
            return {"status": WorkerStatus.CLOSED.value, "accepted": False}
        lock = self._scope_locks.setdefault(binding.scope_id, asyncio.Lock())
        async with lock:
            identity = await self._current_identity()
            if identity is None or identity.uid != binding.conversation_uid:
                return {"status": WorkerStatus.NOT_SENT.value, "accepted": False}
            if not send_now and self._is_processing():
                return {"status": WorkerStatus.BUSY.value, "accepted": False}
            async with self.registry.acquire_existing_scope(binding) as queue:
                if queue is None:
                    return {"status": WorkerStatus.EMPTY.value, "accepted": False}
                snapshot = await queue.snapshot()
                batches = [
                    batch
                    for batch in snapshot.batches
                    if batch.status.value == "queued"
                ]
                if batch_id is not None:
                    batches = [batch for batch in batches if batch.batch_id == batch_id]
                    if not batches:
                        existing = next(
                            (
                                batch
                                for batch in snapshot.batches
                                if batch.batch_id == batch_id
                            ),
                            None,
                        )
                        if existing is not None:
                            return {
                                "status": existing.status.value,
                                "accepted": False,
                                "batch_id": batch_id,
                            }
                        return {"status": WorkerStatus.EMPTY.value, "accepted": False}
                if not batches:
                    return {"status": WorkerStatus.EMPTY.value, "accepted": False}
                worker = WebMessageQueueWorker(
                    queue,
                    identity,
                    self.state_service.get_conversation_identity,
                    WebMessageQueueDispatcher(self.state_service, self.attachments),
                )
                self._active_workers.add(worker)
                try:
                    last_result: WorkerBatchResult | None = None
                    for batch in batches:
                        if not send_now and self._is_processing():
                            break
                        bus = getattr(self.state_service, "_event_bus", None)
                        get_service = getattr(bus, "get_service", None)
                        stream = (
                            get_service("chat_stream")
                            if callable(get_service)
                            else None
                        )
                        bind_scope = getattr(stream, "bind_queued_input_scopes", None)
                        if callable(bind_scope):
                            scope = (
                                binding.scope_id,
                                binding.conversation_uid,
                                binding.principal_id,
                            )
                            selected_ids = [
                                item.request_id
                                for item in batch.items
                                if request_id is None or item.request_id == request_id
                            ]
                            if not await bind_scope(selected_ids, scope):
                                return {
                                    "status": WorkerStatus.NOT_SENT.value,
                                    "accepted": False,
                                }
                        last_result = await worker.dispatch_batch(
                            batch.batch_id,
                            send_now=send_now,
                            request_id=request_id,
                        )
                        if last_result.status is not WorkerStatus.ADMITTED:
                            return self._worker_result_to_wire(last_result)
                        if send_now:
                            return self._worker_result_to_wire(last_result)
                    if last_result is not None:
                        return self._worker_result_to_wire(last_result)
                    return {"status": WorkerStatus.BUSY.value, "accepted": False}
                finally:
                    self._active_workers.discard(worker)

    @staticmethod
    def _worker_result_to_wire(result: WorkerBatchResult) -> dict[str, Any]:
        accepted_count = sum(item.outcome.value == "accepted" for item in result.items)
        uncertain_count = sum(item.outcome.value == "in_doubt" for item in result.items)
        return {
            "status": result.status.value,
            "accepted": result.status is WorkerStatus.ADMITTED,
            "accepted_count": accepted_count,
            "uncertain_count": uncertain_count,
            "scope_id": result.scope_id,
            "incarnation": result.incarnation,
            "batch_id": result.batch_id,
            "claim_id": result.claim_id,
            "revision": result.revision,
            "request_ids": [item.request_id for item in result.items],
        }


def register_web_message_queue_runtime(
    rpc_server: Any,
    state_service: Any,
    *,
    queue_registry: WebMessageQueueRegistry | None = None,
    resolve_scope: ScopeResolver | None = None,
) -> WebMessageQueueRuntime:
    """Register authenticated queue RPCs and the daemon-owned dispatch worker."""
    if state_service is None:
        raise ValueError("daemon state service is required")
    registry = queue_registry or WebMessageQueueRegistry()
    if not isinstance(registry, WebMessageQueueRegistry):
        raise TypeError("queue_registry must be a WebMessageQueueRegistry")
    resolver = resolve_scope or daemon_conversation_scope_resolver(state_service)
    if not callable(resolver):
        raise TypeError("resolve_scope must be callable")

    attachments = WebMessageAttachmentRegistry(
        resolve_scope=_attachment_resolver(registry, resolver),
        validate_owner_id=lambda binding, request_id: registry.validates_attachment_owner_id(
            binding.scope_id,
            binding.queue_incarnation,
            request_id,
        ),
    )
    runtime = WebMessageQueueRuntime(
        registry=registry,
        attachments=attachments,
        state_service=state_service,
        resolve_scope=resolver,
    )

    async def admit_batch(resolved, items, enqueue):
        bus = getattr(state_service, "_event_bus", None)
        get_service = getattr(bus, "get_service", None)
        stream = get_service("chat_stream") if callable(get_service) else None
        admit = getattr(stream, "admit_queued_batch", None)
        if not callable(admit):
            return await enqueue()
        scope = (resolved.scope_id, resolved.conversation_uid, resolved.principal_id)
        attachment_scope = None
        if any(item.attachment_refs for item in items):
            attachment_scope = await attachments.bind_scope(
                {"expected_conversation_uid": resolved.conversation_uid},
                current_rpc_caller_context(),
            )
        has_input = getattr(stream, "has_admitted_input", None)
        get_input = getattr(stream, "get_admitted_input", None)
        stream_inputs = []
        for item in items:
            try:
                payload = json.loads(item.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            text = payload.get("text") if isinstance(payload, dict) else None
            text = text if isinstance(text, str) else ""
            item_attachments = []
            already_projected = callable(has_input) and has_input(
                item.request_id, scope
            )
            stored_input = (
                get_input(item.request_id, scope)
                if already_projected and callable(get_input)
                else None
            )
            if isinstance(stored_input, dict):
                stored_text = stored_input.get("text")
                stored_attachments = stored_input.get("attachments")
                if isinstance(stored_text, str):
                    text = stored_text
                if isinstance(stored_attachments, list):
                    item_attachments = stored_attachments
            elif attachment_scope is not None and not already_projected:
                for ref in item.attachment_refs:
                    staged = await attachment_scope.open_for_admission(
                        item.request_id, ref
                    )
                    item_attachments.append(
                        {
                            "name": staged.filename,
                            "content_type": staged.mime,
                            "source_ref": ref,
                        }
                    )
            stream_inputs.append(
                {
                    "id": item.request_id,
                    "text": text,
                    "attachments": item_attachments,
                }
            )
        return await admit(stream_inputs, scope, enqueue)

    register_web_message_queue_handlers(
        rpc_server,
        registry,
        resolve_scope=resolver,
        on_change=runtime._changes.put_nowait,
        attachment_registry=attachments,
        include_worker_methods=False,
        admit_batch=admit_batch,
    )

    rpc_server.register("state.web_queue.capabilities", runtime.capabilities)
    rpc_server.register("state.web_queue.send_now", runtime.send_now)
    runtime.wake_all()

    event_bus = getattr(state_service, "_event_bus", None)
    register_service = getattr(event_bus, "register_service", None)
    if callable(register_service):
        register_service("web_message_queue_runtime", runtime)
    return runtime
