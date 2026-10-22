"""Engine-owned, generation-fenced lifecycle for WebUI voice sessions.

The runtime backend is injected so lifecycle tests never start processes,
open sockets, or touch audio devices. Concrete LiveKit ownership is added by
an adapter behind this boundary.
"""

from __future__ import annotations

import asyncio
import platform
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Awaitable, Protocol, TypeVar
from uuid import uuid4

from .voice_types import (
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
    VoiceSessionBinding,
    VoiceSessionId,
    VoiceSessionState,
    VoiceSessionTicket,
    VoiceSessionView,
)

_T = TypeVar("_T")


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

    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class EngineRuntimeHost:
    """Manage Engine voice-session generations around an injected runtime backend."""

    def __init__(
        self,
        *,
        session_resolver: SessionTargetResolver,
        runtime: RuntimeBackend,
        platform_supported: Callable[[], bool] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._session_resolver = session_resolver
        self._runtime = runtime
        self._platform_supported = platform_supported or _is_supported_platform
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._lock = asyncio.Lock()
        self._sessions: dict[VoiceSessionId, _SessionRecord] = {}
        self._active_by_key: dict[tuple[str, str, str, str | None, str], VoiceSessionId] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

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
        async with self._lock:
            existing_id = self._active_by_key.get(key)
            if existing_id is not None:
                existing = self._sessions.get(existing_id)
                if existing is not None and existing.state in {
                    "starting",
                    "ready",
                    "stopping",
                    "error",
                }:
                    return _to_view(existing)
                self._active_by_key.pop(key, None)

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

    async def wait_for_background_tasks(self) -> None:
        """Drain lifecycle tasks in tests and during Engine shutdown."""
        while self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks))

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def run_if_current(
        self,
        binding: VoiceSessionBinding,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Serialize a short Engine admission with stop/generation fencing."""
        async with self._lock:
            record = self._sessions.get(binding.voice_session_id)
            if record is None:
                raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
            if record.binding.owner_id != binding.owner_id:
                raise RuntimeHostError("forbidden", 403, "Voice session belongs to another owner.")
            operation_lock = record.operation_lock

        async with operation_lock:
            async with self._lock:
                current = self._sessions.get(binding.voice_session_id)
                if current is not record:
                    raise RuntimeHostError("session_not_found", 404, "Voice session was not found.")
                if current.generation != binding.generation:
                    raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
                if current.state not in {"ready", "listening", "thinking", "speaking"}:
                    raise RuntimeHostError("session_busy", 409, "Voice session is not ready to admit a turn.")
            return await operation()

    async def _start_session(self, binding: VoiceSessionBinding) -> None:
        try:
            handle = await self._runtime.start_session(binding)
        except Exception:
            async with self._lock:
                record = self._sessions.get(binding.voice_session_id)
                if record is None:
                    return
                if record.generation == binding.generation and record.state == "starting":
                    record.state = "error"
                    record.detail = "The voice runtime could not be started."
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
