"""Engine-owned, generation-fenced lifecycle for WebUI voice sessions.

The runtime backend is injected so lifecycle tests never start processes,
open sockets, or touch audio devices. Concrete LiveKit ownership is added by
an adapter behind this boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import platform
import sys
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Awaitable, Protocol, TypeVar
from uuid import uuid4

from kollabor_agent.voice_admission import (
    revoke_voice_admissions_for_generation,
    revoke_voice_admissions_for_turn,
)

from .voice_types import (
    AdmissionReceipt,
    AgentId,
    CreateVoiceSessionRequest,
    JoinVoiceSessionRequest,
    OwnerId,
    RuntimeStatus,
    ServiceLeaseEpoch,
    SessionId,
    StopReason,
    StopVoiceSessionRequest,
    ThreadId,
    TurnId,
    VoiceSessionBinding,
    VoiceSessionId,
    VoiceSessionState,
    VoiceSessionTicket,
    VoiceSessionView,
)

_T = TypeVar("_T")
JOIN_TICKET_TTL_MS = 300_000
# A page handed a session this recently is still joining: a room seen empty
# then gets a full grace again instead of ending under it.
HANDOUT_GRACE_S = 30.0


class RuntimeHost(Protocol):
    async def prepare(self) -> RuntimeStatus: ...

    async def create_session(
        self,
        owner_id: OwnerId,
        control_session_id: SessionId,
        request: CreateVoiceSessionRequest,
    ) -> VoiceSessionView: ...

    async def issue_join_ticket(
        self,
        owner_id: OwnerId,
        voice_session_id: VoiceSessionId,
        request: JoinVoiceSessionRequest,
    ) -> VoiceSessionTicket: ...

    async def stop_session(
        self,
        owner_id: OwnerId,
        voice_session_id: VoiceSessionId,
        request: StopVoiceSessionRequest,
    ) -> VoiceSessionView: ...

    async def get_status(
        self, owner_id: OwnerId, voice_session_id: VoiceSessionId | None = None
    ) -> RuntimeStatus | VoiceSessionView: ...


@dataclass(frozen=True)
class ResolvedVoiceTarget:
    """Engine-authenticated target; never sourced from a voice request body."""

    agent_id: AgentId
    thread_id: ThreadId | None
    voice_provider_profile_id: str
    voice_prompt_revision: str
    service_lease_epoch: ServiceLeaseEpoch


class SessionTargetResolver(Protocol):
    async def resolve(
        self,
        owner_id: OwnerId,
        control_session_id: SessionId,
        selected_thread_id: ThreadId | None,
    ) -> ResolvedVoiceTarget: ...


class RuntimeBackend(Protocol):
    async def prepare(self) -> RuntimeStatus: ...

    async def get_status(self) -> RuntimeStatus: ...

    async def start_session(self, binding: VoiceSessionBinding) -> object: ...

    async def stop_session(self, binding: VoiceSessionBinding, handle: object, reason: StopReason) -> None: ...


class JoinTicketSigner(Protocol):
    async def sign(
        self,
        binding: VoiceSessionBinding,
        *,
        room_name: str,
        expires_at_utc_ms: int,
    ) -> str: ...


class RuntimeHostError(RuntimeError):
    def __init__(
        self,
        code: str,
        status_code: int,
        detail: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable


@dataclass
class _SessionRecord:
    binding: VoiceSessionBinding
    idempotency_key: tuple[str, str, str, str | None, str]
    generation: int = 1
    state: VoiceSessionState = "starting"
    detail: str | None = None
    retryable: bool = False
    handle: object | None = None
    start_task: asyncio.Task[None] | None = None
    stop_reason: StopReason | None = None
    handed_out_at: float = 0.0  # monotonic: create returned it or a join ticket was issued
    listeners: int = 0  # open events streams: a session someone listens to is in use

    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    admitted_turns: dict[TurnId, AdmissionReceipt] = field(default_factory=dict, repr=False)


class EngineRuntimeHost:
    """Manage Engine voice-session generations around an injected runtime backend."""

    def __init__(
        self,
        *,
        session_resolver: SessionTargetResolver,
        runtime: RuntimeBackend,
        platform_supported: Callable[[], bool] | None = None,
        id_factory: Callable[[], str] | None = None,
        join_ticket_signer: JoinTicketSigner | None = None,
        livekit_url: str | None = None,
        utc_now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._session_resolver = session_resolver
        self._runtime = runtime
        self._platform_supported = platform_supported or _is_supported_platform
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._join_ticket_signer = join_ticket_signer
        self._livekit_url = livekit_url
        self._utc_now_ms = utc_now_ms or (lambda: time.time_ns() // 1_000_000)
        self._lock = asyncio.Lock()
        self._sessions: dict[VoiceSessionId, _SessionRecord] = {}
        self._active_by_key: dict[tuple[str, str, str, str | None, str], VoiceSessionId] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        # A running worker's exit and its room's user; not background tasks, since
        # they last as long as it does.
        self._worker_watches: set[asyncio.Task[None]] = set()
        self._shutting_down = False

    async def prepare(self) -> RuntimeStatus:
        if not self._platform_supported():
            return _unsupported_platform_status()
        return await self._runtime.prepare()

    async def create_session(
        self,
        owner_id: OwnerId,
        control_session_id: SessionId,
        request: CreateVoiceSessionRequest,
    ) -> VoiceSessionView:
        # Fail before resolver/model/runtime activity on unsupported hosts.
        if not self._platform_supported():
            raise RuntimeHostError(
                "unsupported_platform",
                422,
                "Voice mode requires an Apple Silicon macOS host.",
            )

        target = await self._session_resolver.resolve(owner_id, control_session_id, request.selected_thread_id)
        if request.selected_thread_id is not None and target.thread_id != request.selected_thread_id:
            raise RuntimeHostError("target_mismatch", 409, "The selected thread is not active in this session.")

        key = (
            owner_id,
            control_session_id,
            target.agent_id,
            target.thread_id,
            target.voice_provider_profile_id,
        )
        active_states = {"starting", "ready", "stopping", "error"}
        while True:
            async with self._lock:
                self._raise_if_shutting_down()
                existing_id = self._active_by_key.get(key)
                existing = self._sessions.get(existing_id) if existing_id is not None else None
                if existing is not None and target.service_lease_epoch < existing.binding.service_lease_epoch:
                    if existing.state in active_states:
                        # A slower resolver response must never regress an
                        # already-published service lease.
                        existing.handed_out_at = time.monotonic()
                        return _to_view(existing)
                    raise RuntimeHostError(
                        "target_mismatch",
                        409,
                        "Resolved voice target is older than the current service lease; retry creation.",
                        retryable=True,
                    )
                if existing is None or existing.state not in active_states:
                    if existing_id is not None and self._active_by_key.get(key) == existing_id:
                        self._active_by_key.pop(key, None)
                    return self._create_session_locked(owner_id, control_session_id, target, key)
                if existing.binding.service_lease_epoch == target.service_lease_epoch:
                    existing.handed_out_at = time.monotonic()
                    return _to_view(existing)
                operation_lock = existing.operation_lock

            # A replacement lease is a new runtime identity. Serialize the
            # fence against admission and stop before replacing the idempotent
            # mapping, but never hold the host lock while waiting for it.
            async with operation_lock:
                async with self._lock:
                    self._raise_if_shutting_down()
                    if self._active_by_key.get(key) != existing_id:
                        continue
                    current = self._sessions.get(existing_id)
                    if current is None:
                        self._active_by_key.pop(key, None)
                        return self._create_session_locked(owner_id, control_session_id, target, key)
                    if target.service_lease_epoch < current.binding.service_lease_epoch:
                        if current.state in active_states:
                            return _to_view(current)
                        raise RuntimeHostError(
                            "target_mismatch",
                            409,
                            "Resolved voice target is older than the current service lease; retry creation.",
                            retryable=True,
                        )
                    if current.state not in active_states:
                        self._active_by_key.pop(key, None)
                        return self._create_session_locked(owner_id, control_session_id, target, key)
                    if current.binding.service_lease_epoch == target.service_lease_epoch:
                        return _to_view(current)

                    self._fence_record_locked(current, reason="session_switch")
                    self._active_by_key.pop(key, None)
                    return self._create_session_locked(owner_id, control_session_id, target, key)

    async def issue_join_ticket(
        self,
        owner_id: OwnerId,
        voice_session_id: VoiceSessionId,
        request: JoinVoiceSessionRequest,
    ) -> VoiceSessionTicket:
        async with self._lock:
            record = self._sessions.get(voice_session_id)
            if record is None:
                raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
            if record.binding.owner_id != owner_id:
                raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
            if request.generation != record.generation:
                raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
            operation_lock = record.operation_lock

        # Stop/switch and admission share this per-session lock. Recheck after
        # acquiring it so a queued stale join never invokes the signer.
        async with operation_lock:
            async with self._lock:
                current = self._sessions.get(voice_session_id)
                if current is None:
                    raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
                if current.binding.owner_id != owner_id:
                    raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
                if request.generation != current.generation:
                    raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
                self._raise_if_shutting_down()
                if current.state != "ready":
                    raise RuntimeHostError(
                        "models_not_ready",
                        409,
                        "The voice runtime is not ready to issue a join ticket.",
                        retryable=True,
                    )
                signer = self._join_ticket_signer
                livekit_url = self._livekit_url
                if signer is None or not livekit_url or not livekit_url.strip():
                    raise RuntimeHostError(
                        "runtime_unavailable",
                        503,
                        "LiveKit join ticket signing is not configured.",
                    )
                binding = current.binding.model_copy(update={"generation": current.generation})
                current.handed_out_at = time.monotonic()
                room_name = f"kollab-{voice_session_id}-g{current.generation}"
                expires_at_utc_ms = self._utc_now_ms() + JOIN_TICKET_TTL_MS

            try:
                participant_token = await signer.sign(
                    binding,
                    room_name=room_name,
                    expires_at_utc_ms=expires_at_utc_ms,
                )
            except Exception as exc:
                raise RuntimeHostError(
                    "runtime_unavailable",
                    503,
                    "The LiveKit join ticket could not be signed.",
                    retryable=True,
                ) from exc
            if not isinstance(participant_token, str) or not participant_token:
                raise RuntimeHostError(
                    "runtime_unavailable",
                    503,
                    "The LiveKit join ticket signer returned an empty token.",
                    retryable=True,
                )

            async with self._lock:
                current = self._sessions.get(voice_session_id)
                if current is not record or current.generation != request.generation or current.state != "ready":
                    raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
                if self._shutting_down:
                    raise RuntimeHostError("runtime_unavailable", 503, "Voice runtime is shutting down.")

            return VoiceSessionTicket(
                voice_session_id=voice_session_id,
                generation=request.generation,
                room_name=room_name,
                livekit_url=livekit_url,
                participant_token=participant_token,
                expires_at_utc_ms=expires_at_utc_ms,
            )

    async def stop_session(
        self,
        owner_id: OwnerId,
        voice_session_id: VoiceSessionId,
        request: StopVoiceSessionRequest,
    ) -> VoiceSessionView:
        async with self._lock:
            record = self._sessions.get(voice_session_id)
            if record is None:
                raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
            if record.binding.owner_id != owner_id:
                raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
            operation_lock = record.operation_lock

        # A transcript admission holds this per-session lock until the ordinary
        # Engine message queue has accepted it, so stop cannot fence halfway
        # through an admission transaction.
        async with operation_lock:
            async with self._lock:
                record = self._sessions.get(voice_session_id)
                if record is None:
                    raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
                if record.binding.owner_id != owner_id:
                    raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
                if request.generation != record.generation:
                    raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
                if record.state in {"stopping", "stopped"}:
                    return _to_view(record)

                old_binding = record.binding
                old_handle = record.handle
                old_state = record.state
                revoke_voice_admissions_for_generation(
                    old_binding.voice_session_id,
                    record.generation,
                )
                record.generation += 1
                record.state = "stopping"
                record.detail = None
                record.retryable = False
                record.stop_reason = request.reason
                record.handle = None
                if old_handle is not None:
                    self._spawn(
                        self._stop_handle(
                            old_binding,
                            old_handle,
                            expected_generation=record.generation,
                            reason=request.reason,
                        )
                    )
                elif old_state != "starting" and (record.start_task is None or record.start_task.done()):
                    record.state = "stopped"
                    if self._active_by_key.get(record.idempotency_key) == voice_session_id:
                        self._active_by_key.pop(record.idempotency_key, None)
                # A start still in flight observes the new generation and cleans
                # up its late handle; the stop acknowledgement does not await it.
                return _to_view(record)

    async def get_status(
        self,
        owner_id: OwnerId,
        voice_session_id: VoiceSessionId | None = None,
    ) -> RuntimeStatus | VoiceSessionView:
        if voice_session_id is None:
            if not self._platform_supported():
                return _unsupported_platform_status()
            return await self._runtime.get_status()

        async with self._lock:
            record = self._sessions.get(voice_session_id)
            if record is None:
                raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
            if record.binding.owner_id != owner_id:
                raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
            return _to_view(record)

    async def get_binding(self, owner_id: OwnerId, voice_session_id: VoiceSessionId) -> VoiceSessionBinding:
        """The stored binding for an owned session (what admit_turn compares against)."""
        async with self._lock:
            record = self._sessions.get(voice_session_id)
            if record is None or record.binding.owner_id != owner_id:
                # Never reveal another owner's session.
                raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
            return record.binding

    async def listener_opened(self, voice_session_id: VoiceSessionId) -> None:
        """An events stream opened: while anyone listens, the session is in use."""
        async with self._lock:
            record = self._sessions.get(voice_session_id)
            if record is not None:
                record.listeners += 1

    async def listener_closed(self, voice_session_id: VoiceSessionId) -> None:
        """An events stream closed: the abandonment grace starts at the disconnect."""
        async with self._lock:
            record = self._sessions.get(voice_session_id)
            if record is None:
                return
            record.listeners = max(record.listeners - 1, 0)
            record.handed_out_at = time.monotonic()

    async def shutdown(self) -> None:
        """Fence all session generations, stop ready handles, and drain starts/stops."""
        async with self._lock:
            self._shutting_down = True
            records = tuple(self._sessions.values())

        # Use the per-session operation locks shared by stop and admission.
        # Late starts observe the fence and clean up any handle they create.
        for record in records:
            async with record.operation_lock:
                async with self._lock:
                    current = self._sessions.get(record.binding.voice_session_id)
                    if current is not record or current.state in {"stopping", "stopped"}:
                        continue
                    self._fence_record_locked(current, reason="shutdown")

        await self.wait_for_background_tasks()
        for task in tuple(self._worker_watches):
            task.cancel()  # only a worker whose stop failed is still running
        async with self._lock:
            failed_stops = tuple(
                record
                for record in self._sessions.values()
                if record.state == "error" and record.handle is not None
            )
        if failed_stops:
            raise RuntimeHostError(
                "runtime_unavailable",
                503,
                f"Voice runtime shutdown could not stop {len(failed_stops)} session(s).",
                retryable=True,
            )

    async def wait_for_background_tasks(self) -> None:
        """Drain lifecycle tasks in tests and during Engine shutdown."""
        while self._background_tasks:
            tasks = tuple(self._background_tasks)
            await asyncio.gather(*tasks)
            # The task callbacks remove entries on the next loop tick. Discard
            # completed tasks here too, avoiding a busy loop on finished tasks.
            self._background_tasks.difference_update(task for task in tasks if task.done())

    def _raise_if_shutting_down(self) -> None:
        if self._shutting_down:
            raise RuntimeHostError("runtime_unavailable", 503, "Voice runtime is shutting down.")

    def _create_session_locked(
        self,
        owner_id: OwnerId,
        control_session_id: SessionId,
        target: ResolvedVoiceTarget,
        key: tuple[str, str, str, str | None, str],
    ) -> VoiceSessionView:
        voice_session_id = self._id_factory()
        binding = VoiceSessionBinding(
            owner_id=owner_id,
            voice_session_id=voice_session_id,
            control_session_id=control_session_id,
            conversation_session_id=control_session_id,
            agent_id=target.agent_id,
            thread_id=target.thread_id,
            voice_provider_profile_id=target.voice_provider_profile_id,
            voice_prompt_revision=target.voice_prompt_revision,
            voice_tool_policy="voice_terminal_hub_v1",
            generation=1,
            service_lease_epoch=target.service_lease_epoch,
        )
        record = _SessionRecord(binding=binding, idempotency_key=key)
        self._sessions[voice_session_id] = record
        self._active_by_key[key] = voice_session_id
        record.start_task = self._spawn(self._start_session(binding))
        return _to_view(record)

    def _fence_record_locked(self, record: _SessionRecord, *, reason: StopReason) -> None:
        if record.state in {"stopping", "stopped"}:
            return

        old_binding = record.binding
        old_handle = record.handle
        old_state = record.state
        revoke_voice_admissions_for_generation(
            old_binding.voice_session_id,
            record.generation,
        )
        record.generation += 1
        record.state = "stopping"
        record.detail = None
        record.retryable = False
        record.stop_reason = reason
        record.handle = None
        if old_handle is not None:
            self._spawn(
                self._stop_handle(
                    old_binding,
                    old_handle,
                    expected_generation=record.generation,
                    reason=reason,
                )
            )
        elif old_state != "starting" or record.start_task is None or record.start_task.done():
            record.state = "stopped"
            if self._active_by_key.get(record.idempotency_key) == old_binding.voice_session_id:
                self._active_by_key.pop(record.idempotency_key, None)

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def admit_turn(
        self,
        binding: VoiceSessionBinding,
        turn_id: TurnId,
        operation: Callable[[], Awaitable[AdmissionReceipt]],
    ) -> AdmissionReceipt:
        """Atomically deduplicate a turn with the current generation fence."""
        async with self._lock:
            record = self._sessions.get(binding.voice_session_id)
            if record is None:
                raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
            if record.binding.owner_id != binding.owner_id:
                raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
            if record.binding != binding:
                raise RuntimeHostError("target_mismatch", 409, "Voice binding does not match the host record.")
            operation_lock = record.operation_lock

        async with operation_lock:
            async with self._lock:
                current = self._sessions.get(binding.voice_session_id)
                if current is not record:
                    raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
                if current.binding != binding:
                    raise RuntimeHostError("target_mismatch", 409, "Voice binding does not match the host record.")
                if current.generation != binding.generation:
                    raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
                self._raise_if_shutting_down()
                if current.state not in {"ready", "listening", "thinking", "speaking"}:
                    raise RuntimeHostError("session_busy", 409, "Voice session is not ready to admit a turn.")
                previous = current.admitted_turns.get(turn_id)
                if previous is not None:
                    return previous.model_copy(update={"duplicate": True})

            try:
                receipt = await operation()
                if not receipt.accepted:
                    revoke_voice_admissions_for_turn(
                        binding.voice_session_id,
                        binding.generation,
                        turn_id,
                    )
                    return receipt
                async with self._lock:
                    current = self._sessions.get(binding.voice_session_id)
                    if current is not record:
                        raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
                    if current.binding != binding:
                        raise RuntimeHostError("target_mismatch", 409, "Voice binding does not match the host record.")
                    if current.generation != binding.generation:
                        raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
                    current.admitted_turns[turn_id] = receipt
                return receipt
            except BaseException:
                revoke_voice_admissions_for_turn(
                    binding.voice_session_id,
                    binding.generation,
                    turn_id,
                )
                raise

    async def run_if_current(
        self,
        binding: VoiceSessionBinding,
        operation: Callable[[], Awaitable[_T]],
        *,
        admitted_turn_id: TurnId | None = None,
    ) -> _T:
        """Serialize an Engine operation against stop/generation changes."""
        async with self._lock:
            record = self._sessions.get(binding.voice_session_id)
            if record is None:
                raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
            if record.binding.owner_id != binding.owner_id:
                raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
            if record.binding != binding:
                raise RuntimeHostError("target_mismatch", 409, "Voice binding does not match the host record.")
            operation_lock = record.operation_lock

        async with operation_lock:
            async with self._lock:
                current = self._sessions.get(binding.voice_session_id)
                if current is not record:
                    raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
                if current.binding != binding:
                    raise RuntimeHostError("target_mismatch", 409, "Voice binding does not match the host record.")
                if current.generation != binding.generation:
                    raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
                self._raise_if_shutting_down()
                if admitted_turn_id is not None and admitted_turn_id not in current.admitted_turns:
                    raise RuntimeHostError("target_mismatch", 409, "Voice turn was not admitted by this session.")
                if current.state not in {"ready", "listening", "thinking", "speaking"}:
                    raise RuntimeHostError("session_busy", 409, "Voice session is not ready to admit a turn.")
            if admitted_turn_id is not None:
                # Revoke before cancellation can fail or race with tool execution.
                revoke_voice_admissions_for_turn(
                    binding.voice_session_id,
                    binding.generation,
                    admitted_turn_id,
                )
            return await operation()

    def _starting(self, binding: VoiceSessionBinding) -> _SessionRecord | None:
        record = self._sessions.get(binding.voice_session_id)
        if record is not None and record.generation == binding.generation and record.state == "starting":
            return record
        return None

    def _setup_detail(self, binding: VoiceSessionBinding, detail: str) -> None:
        record = self._starting(binding)
        if record is not None:
            record.detail = detail

    async def _start_session(self, binding: VoiceSessionBinding) -> None:
        try:
            ensure_runtime = getattr(self._runtime, "ensure_runtime", None)
            if ensure_runtime is not None:
                # The first start installs voice support (minutes): show each step.
                await ensure_runtime(lambda detail: self._setup_detail(binding, detail))
                if self._starting(binding) is None:
                    raise RuntimeHostError("stopped", 409, "Voice stopped during setup.")
            handle = await self._runtime.start_session(binding)
        except Exception as exc:
            async with self._lock:
                record = self._sessions.get(binding.voice_session_id)
                if record is None:
                    return
                if record.generation == binding.generation and record.state == "starting":
                    # a model that ran out of memory keeps its own state (Low On Resources)
                    shortage = (
                        isinstance(exc, RuntimeHostError)
                        and exc.code == "resource_shortage"
                    )
                    record.state = "resource_shortage" if shortage else "error"
                    record.detail = (
                        exc.detail
                        if isinstance(exc, RuntimeHostError)
                        else "The voice runtime could not be started."
                    )
                    record.retryable = True
                    if self._active_by_key.get(record.idempotency_key) == binding.voice_session_id:
                        self._active_by_key.pop(record.idempotency_key, None)
                elif record.generation > binding.generation and record.state == "stopping":
                    record.state = "stopped"
                    if self._active_by_key.get(record.idempotency_key) == binding.voice_session_id:
                        self._active_by_key.pop(record.idempotency_key, None)
            return

        async with self._lock:
            record = self._sessions.get(binding.voice_session_id)
            if record is not None and record.generation == binding.generation and record.state == "starting":
                record.handle = handle
                record.state = "ready"
                record.detail = None
                record.retryable = False
                wait_exited = getattr(self._runtime, "wait_exited", None)
                if wait_exited is not None:
                    self._watch_worker(self._end_on_exit(binding, handle, wait_exited))
                wait_abandoned = getattr(self._runtime, "wait_abandoned", None)
                if wait_abandoned is not None:
                    self._watch_worker(self._end_when_abandoned(binding, handle, wait_abandoned))
                return
            expected_generation = record.generation if record is not None else binding.generation + 1
            reason = record.stop_reason if record is not None else "shutdown"

        # Stop/switch fenced this create while the backend was starting. Never
        # publish ready or retain its handle after the generation changed.
        await self._stop_handle(
            binding,
            handle,
            expected_generation=expected_generation,
            reason=reason or "session_switch",
        )

    async def _end_on_exit(
        self,
        binding: VoiceSessionBinding,
        handle: object,
        wait_exited: Callable[[object], Awaitable[None]],
    ) -> None:
        """A worker that exits while its session still holds it ends that session.

        Without this the session stays ready with no worker in the room: the
        browser keeps its mic on and hears nothing, and create keeps handing
        the same dead session back.
        """
        await wait_exited(handle)
        record = self._sessions.get(binding.voice_session_id)
        if record is None:
            return
        async with record.operation_lock:
            async with self._lock:
                if record.handle is not handle:
                    return  # a stop or switch took it first: that exit was ours
                revoke_voice_admissions_for_generation(binding.voice_session_id, record.generation)
                record.state = "error"
                record.detail = "The voice worker stopped."
                record.retryable = True
                record.handle = None
                if self._active_by_key.get(record.idempotency_key) == binding.voice_session_id:
                    self._active_by_key.pop(record.idempotency_key, None)
        with contextlib.suppress(Exception):
            await self._runtime.stop_session(binding, handle, "error")  # the backend lets go of it

    async def _end_when_abandoned(
        self,
        binding: VoiceSessionBinding,
        handle: object,
        wait_abandoned: Callable[[VoiceSessionBinding, object], Awaitable[bool]],
    ) -> None:
        """A room its user left, and did not come back to in time, ends as a stop does.

        A closed or reloaded page sends no stop, and its worker would keep running
        and keep the voice service's lease. Until then create hands this session
        back, so a reload that starts voice again rejoins the room.
        """
        while True:
            if not await wait_abandoned(binding, handle):
                return  # the worker exited first: _end_on_exit has it
            record = self._sessions.get(binding.voice_session_id)
            if record is None:
                return
            if record.listeners > 0:
                # Someone is still listening to this session's events (a phone
                # page over HTTP, never in the room): it is in use, so keep
                # waiting, exactly as for a page that is still joining.
                continue
            if time.monotonic() - record.handed_out_at >= HANDOUT_GRACE_S:
                break
            # A page was just given this session (a reload near the end of the
            # grace): it is still joining, so wait a full grace again.
        async with record.operation_lock:
            async with self._lock:
                if record.handle is handle:  # else a stop, switch or exit took it first
                    self._fence_record_locked(record, reason="disconnect")

    def _watch_worker(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._worker_watches.add(task)
        task.add_done_callback(self._worker_watches.discard)

    async def _stop_handle(
        self,
        binding: VoiceSessionBinding,
        handle: object,
        *,
        expected_generation: int,
        reason: StopReason,
    ) -> None:
        try:
            await self._runtime.stop_session(binding, handle, reason)
        except Exception:
            async with self._lock:
                record = self._sessions.get(binding.voice_session_id)
                if record is not None and record.generation == expected_generation and record.state == "stopping":
                    record.state = "error"
                    record.detail = "The voice runtime could not be stopped cleanly."
                    record.retryable = True
                    record.handle = handle
            return

        async with self._lock:
            record = self._sessions.get(binding.voice_session_id)
            if record is not None and record.generation == expected_generation and record.state == "stopping":
                record.state = "stopped"
                record.detail = None
                record.retryable = False
                if self._active_by_key.get(record.idempotency_key) == binding.voice_session_id:
                    self._active_by_key.pop(record.idempotency_key, None)


def _to_view(record: _SessionRecord) -> VoiceSessionView:
    binding = record.binding
    return VoiceSessionView(
        voice_session_id=binding.voice_session_id,
        control_session_id=binding.control_session_id,
        conversation_session_id=binding.conversation_session_id,
        agent_id=binding.agent_id,
        thread_id=binding.thread_id,
        voice_provider_profile_id=binding.voice_provider_profile_id,
        voice_prompt_revision=binding.voice_prompt_revision,
        state=record.state,
        generation=record.generation,
        detail=record.detail,
        retryable=record.retryable,
    )


def _is_supported_platform() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}


def _unsupported_platform_status() -> RuntimeStatus:
    return RuntimeStatus(
        state="error",
        component="livekit_server",
        progress=None,
        detail="Voice mode requires an Apple Silicon macOS host.",
        retryable=False,
    )
