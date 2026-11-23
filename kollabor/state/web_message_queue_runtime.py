"""Compose the daemon-owned WebUI queue, dispatcher, and bounded supervisor."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from kollabor_rpc import RpcCallerContext, current_rpc_caller_context

from .web_message_attachments import (
    AttachmentAuthorizationError,
    AttachmentScopeBinding,
    WebMessageAttachmentRegistry,
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
    _active_workers: set[WebMessageQueueWorker] = field(
        default_factory=set, repr=False
    )
    _worker_close_tasks: dict[
        WebMessageQueueWorker, asyncio.Task[bool]
    ] = field(default_factory=dict, repr=False)
    _supervisor_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _turn_idle_retry_task: asyncio.Task[None] | None = field(default=None, repr=False)
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

    async def _retry_until_idle(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _TURN_IDLE_RETRY_SECONDS
        try:
            while (
                not self._closing
                and self._is_processing()
                and loop.time() < deadline
            ):
                await asyncio.sleep(_TURN_IDLE_RETRY_INTERVAL_SECONDS)
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
                except (asyncio.CancelledError, Exception):
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
            await asyncio.wait(
                close_tasks, timeout=max(0.0, deadline - loop.time())
            )

        workers_ok = True
        for worker, task in tuple(self._worker_close_tasks.items()):
            if not task.done():
                workers_ok = False
                continue
            try:
                settled = task.result() is True
            except (asyncio.CancelledError, Exception):
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
                logger.debug("WebUI queue supervisor failed during close", exc_info=True)

        return (
            retry_done
            and workers_ok
            and supervisor_done
            and not self._active_workers
            and not self._worker_close_tasks
        )

    async def capabilities(self, params: dict[str, Any]) -> dict[str, bool]:
        self._ensure_supervisor()
        await self._resolve_trusted_scope(params)
        return {"dispatch_available": self._send_now_available()}

    async def send_now(self, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_supervisor()
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
            value = getattr(getattr(self.state_service, "_llm_service", None), "is_processing", False)
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
                batches = [batch for batch in snapshot.batches if batch.status.value == "queued"]
                if batch_id is not None:
                    batches = [batch for batch in batches if batch.batch_id == batch_id]
                    if not batches:
                        existing = next(
                            (batch for batch in snapshot.batches if batch.batch_id == batch_id),
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
                        last_result = await worker.dispatch_batch(
                            batch.batch_id,
                            send_now=send_now,
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
    register_web_message_queue_handlers(
        rpc_server,
        registry,
        resolve_scope=resolver,
        on_change=runtime._changes.put_nowait,
        attachment_registry=attachments,
        include_worker_methods=False,
    )

    rpc_server.register("state.web_queue.capabilities", runtime.capabilities)
    rpc_server.register("state.web_queue.send_now", runtime.send_now)
    runtime.wake_all()

    event_bus = getattr(state_service, "_event_bus", None)
    register_service = getattr(event_bus, "register_service", None)
    if callable(register_service):
        register_service("web_message_queue_runtime", runtime)
    return runtime
