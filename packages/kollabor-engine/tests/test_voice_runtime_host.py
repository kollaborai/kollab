"""Fake-only tests for Engine voice runtime session lifecycle fencing."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from kollabor_engine.voice_runtime_host import (
    EngineRuntimeHost,
    ResolvedVoiceTarget,
    RuntimeHostError,
)
from kollabor_engine.voice_types import (
    AdmissionReceipt,
    CreateVoiceSessionRequest,
    JoinVoiceSessionRequest,
    RuntimeStatus,
    StopVoiceSessionRequest,
)


class FakeSessionResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.service_lease_epoch = 7

    async def resolve(
        self, owner_id: str, control_session_id: str, selected_thread_id: str | None
    ) -> ResolvedVoiceTarget:
        self.calls.append((owner_id, control_session_id, selected_thread_id))
        return ResolvedVoiceTarget(
            agent_id="agent-from-engine",
            thread_id=selected_thread_id,
            voice_provider_profile_id="voice-profile-1",
            voice_prompt_revision="voice-prompt-r1",
            service_lease_epoch=self.service_lease_epoch,
        )


class FakeRuntimeBackend:
    def __init__(
        self,
        *,
        block_start: bool = False,
        block_stop: bool = False,
        fail_stop_count: int = 0,
    ) -> None:
        self.start_entered = asyncio.Event()
        self.release_start = asyncio.Event()
        self.stop_entered = asyncio.Event()
        self.release_stop = asyncio.Event()
        if not block_start:
            self.release_start.set()
        if not block_stop:
            self.release_stop.set()
        self.start_calls = []
        self.stop_calls = []
        self.fail_stop_count = fail_stop_count

    async def prepare(self) -> RuntimeStatus:
        return RuntimeStatus(
            state="ready",
            component="livekit_server",
            progress=1.0,
            detail="ready",
            retryable=False,
        )

    async def get_status(self) -> RuntimeStatus:
        return await self.prepare()

    async def start_session(self, binding):
        self.start_calls.append(binding)
        self.start_entered.set()
        await self.release_start.wait()
        return {"room": f"room-{binding.voice_session_id}-{binding.generation}"}

    async def stop_session(self, binding, handle, reason) -> None:
        self.stop_calls.append((binding, handle, reason))
        self.stop_entered.set()
        if self.fail_stop_count:
            self.fail_stop_count -= 1
            raise RuntimeError("fake teardown failure")
        await self.release_stop.wait()


class FakeJoinTicketSigner:
    def __init__(self) -> None:
        self.calls = []

    async def sign(self, binding, *, room_name: str, expires_at_utc_ms: int) -> str:
        self.calls.append((binding, room_name, expires_at_utc_ms))
        return f"signed:{room_name}"


def _request(thread_id: str | None = "thread-1") -> CreateVoiceSessionRequest:
    return CreateVoiceSessionRequest(selected_thread_id=thread_id)


def _host(
    backend: FakeRuntimeBackend,
    resolver: FakeSessionResolver | None = None,
    *,
    platform_supported=lambda: True,
    join_ticket_signer=None,
    livekit_url: str | None = None,
    utc_now_ms=None,
) -> EngineRuntimeHost:
    return EngineRuntimeHost(
        session_resolver=resolver or FakeSessionResolver(),
        runtime=backend,
        platform_supported=platform_supported,
        join_ticket_signer=join_ticket_signer,
        livekit_url=livekit_url,
        utc_now_ms=utc_now_ms,
    )


@pytest.mark.asyncio
async def test_create_is_idempotent_while_starting_and_ready():
    backend = FakeRuntimeBackend(block_start=True)
    resolver = FakeSessionResolver()
    host = _host(backend, resolver)

    starting = await host.create_session("owner-1", "session-1", _request())
    duplicate = await host.create_session("owner-1", "session-1", _request())

    assert starting.state == "starting"
    assert duplicate.voice_session_id == starting.voice_session_id
    assert duplicate.generation == 1
    assert resolver.calls == [
        ("owner-1", "session-1", "thread-1"),
        ("owner-1", "session-1", "thread-1"),
    ]
    await backend.start_entered.wait()
    assert len(backend.start_calls) == 1
    binding = backend.start_calls[0]
    assert binding.owner_id == "owner-1"
    assert binding.control_session_id == binding.conversation_session_id == "session-1"
    assert binding.agent_id == "agent-from-engine"
    assert binding.thread_id == "thread-1"
    assert binding.generation == 1

    backend.release_start.set()
    await host.wait_for_background_tasks()
    ready = await host.get_status("owner-1", starting.voice_session_id)
    assert ready.state == "ready"
    repeated = await host.create_session("owner-1", "session-1", _request())
    assert repeated.voice_session_id == starting.voice_session_id
    assert len(backend.start_calls) == 1


@pytest.mark.asyncio
async def test_stop_fences_a_late_start_before_backend_teardown():
    backend = FakeRuntimeBackend(block_start=True)
    host = _host(backend)
    starting = await host.create_session("owner-1", "session-1", _request())
    await backend.start_entered.wait()

    stopping = await asyncio.wait_for(
        host.stop_session(
            "owner-1",
            starting.voice_session_id,
            StopVoiceSessionRequest(generation=1, reason="session_switch"),
        ),
        timeout=0.1,
    )
    assert stopping.state == "stopping"
    assert stopping.generation == 2
    assert backend.stop_calls == []

    backend.release_start.set()
    await host.wait_for_background_tasks()
    stopped = await host.get_status("owner-1", starting.voice_session_id)
    assert stopped.state == "stopped"
    assert stopped.generation == 2
    assert len(backend.stop_calls) == 1
    assert backend.stop_calls[0][0].generation == 1


@pytest.mark.asyncio
async def test_stop_acknowledges_before_slow_teardown_and_rejects_stale_generation():
    backend = FakeRuntimeBackend()
    host = _host(backend)
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()
    assert (await host.get_status("owner-1", ready.voice_session_id)).state == "ready"

    backend.release_stop.clear()
    stopping = await asyncio.wait_for(
        host.stop_session(
            "owner-1",
            ready.voice_session_id,
            StopVoiceSessionRequest(generation=1, reason="user"),
        ),
        timeout=0.1,
    )
    assert stopping.state == "stopping"
    assert stopping.generation == 2
    await backend.stop_entered.wait()

    with pytest.raises(RuntimeHostError) as error:
        await host.stop_session(
            "owner-1",
            ready.voice_session_id,
            StopVoiceSessionRequest(generation=1, reason="user"),
        )
    assert error.value.code == "stale_generation"

    backend.release_stop.set()
    await host.wait_for_background_tasks()
    stopped = await host.get_status("owner-1", ready.voice_session_id)
    assert stopped.state == "stopped"
    assert stopped.generation == 2
    assert len(backend.stop_calls) == 1


@pytest.mark.asyncio
async def test_generation_is_scoped_to_voice_session_id_and_owner_is_enforced():
    backend = FakeRuntimeBackend()
    host = _host(backend)
    first = await host.create_session("owner-1", "session-1", _request())
    second = await host.create_session("owner-1", "session-2", _request())
    await host.wait_for_background_tasks()

    assert first.voice_session_id != second.voice_session_id
    assert first.generation == second.generation == 1
    with pytest.raises(RuntimeHostError) as error:
        await host.stop_session(
            "owner-2",
            first.voice_session_id,
            StopVoiceSessionRequest(generation=1, reason="user"),
        )
    assert error.value.code == "forbidden"

    stopped = await host.stop_session(
        "owner-1",
        first.voice_session_id,
        StopVoiceSessionRequest(generation=1, reason="user"),
    )
    await host.wait_for_background_tasks()
    assert stopped.generation == 2
    assert (await host.get_status("owner-1", second.voice_session_id)).generation == 1
    assert (await host.get_status("owner-1", second.voice_session_id)).state == "ready"


@pytest.mark.asyncio
async def test_unsupported_platform_fails_before_runtime_start():
    backend = FakeRuntimeBackend()
    resolver = FakeSessionResolver()
    host = _host(backend, resolver, platform_supported=lambda: False)

    with pytest.raises(RuntimeHostError) as error:
        await host.create_session("owner-1", "session-1", _request())

    assert error.value.code == "unsupported_platform"
    assert error.value.status_code == 422
    assert resolver.calls == []
    assert backend.start_calls == []


@pytest.mark.asyncio
async def test_failed_teardown_keeps_idempotency_and_handle_for_retry():
    backend = FakeRuntimeBackend(fail_stop_count=1)
    host = _host(backend)
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()

    await host.stop_session(
        "owner-1",
        ready.voice_session_id,
        StopVoiceSessionRequest(generation=1, reason="user"),
    )
    await host.wait_for_background_tasks()
    failed = await host.get_status("owner-1", ready.voice_session_id)
    assert failed.state == "error"
    assert failed.generation == 2
    assert failed.retryable is True

    retry_create = await host.create_session("owner-1", "session-1", _request())
    assert retry_create.voice_session_id == ready.voice_session_id
    assert retry_create.state == "error"
    assert len(backend.start_calls) == 1

    stopping = await host.stop_session(
        "owner-1",
        ready.voice_session_id,
        StopVoiceSessionRequest(generation=2, reason="user"),
    )
    assert stopping.generation == 3
    await host.wait_for_background_tasks()
    stopped = await host.get_status("owner-1", ready.voice_session_id)
    assert stopped.state == "stopped"
    assert stopped.generation == 3
    assert len(backend.stop_calls) == 2


@pytest.mark.asyncio
async def test_admission_generation_guard_serializes_stop():
    backend = FakeRuntimeBackend()
    host = _host(backend)
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()
    binding = backend.start_calls[0]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def enqueue_message():
        entered.set()
        await release.wait()
        return "accepted"

    admission_task = asyncio.create_task(host.run_if_current(binding, enqueue_message))
    await entered.wait()
    stop_task = asyncio.create_task(
        host.stop_session(
            "owner-1",
            ready.voice_session_id,
            StopVoiceSessionRequest(generation=1, reason="session_switch"),
        )
    )
    done, _ = await asyncio.wait({stop_task}, timeout=0.05)
    assert stop_task not in done

    release.set()
    assert await admission_task == "accepted"
    stopping = await stop_task
    assert stopping.state == "stopping"
    assert stopping.generation == 2


@pytest.mark.asyncio
async def test_join_ticket_binds_generation_room_and_300_second_expiry():
    backend = FakeRuntimeBackend()
    signer = FakeJoinTicketSigner()
    host = _host(
        backend,
        join_ticket_signer=signer,
        livekit_url="wss://livekit.example.test",
        utc_now_ms=lambda: 1_800_000_000_000,
    )
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()

    ticket = await host.issue_join_ticket(
        "owner-1",
        ready.voice_session_id,
        JoinVoiceSessionRequest(generation=ready.generation),
    )

    expected_room = f"kollab-{ready.voice_session_id}-g{ready.generation}"
    assert ticket.voice_session_id == ready.voice_session_id
    assert ticket.generation == ready.generation
    assert ticket.room_name == expected_room
    assert ticket.livekit_url == "wss://livekit.example.test"
    assert ticket.participant_token == f"signed:{expected_room}"
    assert ticket.expires_at_utc_ms == 1_800_000_300_000
    assert signer.calls == [(backend.start_calls[0], expected_room, 1_800_000_300_000)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_id", "generation", "error_code", "stop_session_first"),
    [
        ("owner-2", 1, "forbidden", False),
        ("owner-1", 1, "stale_generation", True),
    ],
)
async def test_invalid_owner_or_generation_never_calls_ticket_signer(
    owner_id,
    generation,
    error_code,
    stop_session_first,
):
    backend = FakeRuntimeBackend()
    signer = FakeJoinTicketSigner()
    host = _host(backend, join_ticket_signer=signer, livekit_url="wss://livekit.example.test")
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()
    if stop_session_first:
        await host.stop_session(
            "owner-1",
            ready.voice_session_id,
            StopVoiceSessionRequest(generation=ready.generation, reason="user"),
        )
        await host.wait_for_background_tasks()

    with pytest.raises(RuntimeHostError) as error:
        await host.issue_join_ticket(
            owner_id,
            ready.voice_session_id,
            JoinVoiceSessionRequest(generation=generation),
        )

    assert error.value.code == error_code
    assert signer.calls == []


@pytest.mark.asyncio
async def test_create_replaces_idempotent_binding_when_service_lease_changes():
    backend = FakeRuntimeBackend()
    resolver = FakeSessionResolver()
    host = _host(backend, resolver)
    first = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()

    resolver.service_lease_epoch = 8
    replacement = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()

    assert replacement.voice_session_id != first.voice_session_id
    assert replacement.generation == 1
    assert len(backend.start_calls) == 2
    assert backend.start_calls[0].service_lease_epoch == 7
    assert backend.start_calls[1].service_lease_epoch == 8
    assert len(backend.stop_calls) == 1
    assert backend.stop_calls[0][0].voice_session_id == first.voice_session_id
    assert backend.stop_calls[0][2] == "session_switch"

    duplicate = await host.create_session("owner-1", "session-1", _request())
    assert duplicate.voice_session_id == replacement.voice_session_id
    assert duplicate.agent_id == "agent-from-engine"
    assert len(backend.start_calls) == 2


@pytest.mark.asyncio
async def test_shutdown_fences_and_stops_ready_sessions_idempotently():
    backend = FakeRuntimeBackend()
    host = _host(backend)
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()

    await host.shutdown()

    stopped = await host.get_status("owner-1", ready.voice_session_id)
    assert stopped.state == "stopped"
    assert stopped.generation == ready.generation + 1
    assert len(backend.stop_calls) == 1
    assert backend.stop_calls[0][0].generation == ready.generation
    assert backend.stop_calls[0][2] == "shutdown"

    await host.shutdown()
    assert len(backend.stop_calls) == 1
    with pytest.raises(RuntimeHostError) as error:
        await host.create_session("owner-1", "session-2", _request())
    assert error.value.code == "runtime_unavailable"


@pytest.mark.asyncio
async def test_shutdown_waits_for_pending_start_and_stops_late_handle():
    backend = FakeRuntimeBackend(block_start=True)
    host = _host(backend)
    starting = await host.create_session("owner-1", "session-1", _request())
    await backend.start_entered.wait()

    shutdown_task = asyncio.create_task(host.shutdown())
    await asyncio.sleep(0)
    assert not shutdown_task.done()
    stopping = await host.get_status("owner-1", starting.voice_session_id)
    assert stopping.state == "stopping"
    assert stopping.generation == starting.generation + 1
    assert backend.stop_calls == []

    backend.release_start.set()
    await asyncio.wait_for(shutdown_task, timeout=1)

    stopped = await host.get_status("owner-1", starting.voice_session_id)
    assert stopped.state == "stopped"
    assert stopped.generation == starting.generation + 1
    assert len(backend.stop_calls) == 1
    assert backend.stop_calls[0][0].generation == starting.generation
    assert backend.stop_calls[0][2] == "shutdown"


@pytest.mark.asyncio
async def test_admit_and_run_if_current_reject_forged_binding_identity():
    backend = FakeRuntimeBackend()
    host = _host(backend)
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()
    binding = backend.start_calls[0]
    forged_binding = binding.model_copy(update={"thread_id": "another-thread"})
    operation_called = False

    async def submit_turn():
        nonlocal operation_called
        operation_called = True
        return AdmissionReceipt(
            accepted=True,
            request_id=str(uuid4()),
            conversation_session_id=binding.conversation_session_id,
            queued=True,
            duplicate=False,
        )

    with pytest.raises(RuntimeHostError) as admit_error:
        await host.admit_turn(forged_binding, str(uuid4()), submit_turn)
    assert admit_error.value.code == "target_mismatch"
    assert operation_called is False

    async def cancel_turn():
        nonlocal operation_called
        operation_called = True

    with pytest.raises(RuntimeHostError) as run_error:
        await host.run_if_current(forged_binding, cancel_turn)
    assert run_error.value.code == "target_mismatch"
    assert operation_called is False
    assert ready.voice_session_id == binding.voice_session_id


@pytest.mark.asyncio
async def test_slower_stale_resolver_cannot_replace_newer_service_lease():
    class RacingResolver(FakeSessionResolver):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()
            self.call_count = 0

        async def resolve(self, owner_id, control_session_id, selected_thread_id):
            self.calls.append((owner_id, control_session_id, selected_thread_id))
            self.call_count += 1
            resolved_epoch = self.service_lease_epoch
            if self.call_count == 1:
                self.first_started.set()
                await self.release_first.wait()
            return ResolvedVoiceTarget(
                agent_id="agent-from-engine",
                thread_id=selected_thread_id,
                voice_provider_profile_id="voice-profile-1",
                voice_prompt_revision="voice-prompt-r1",
                service_lease_epoch=resolved_epoch,
            )

    backend = FakeRuntimeBackend()
    resolver = RacingResolver()
    host = _host(backend, resolver)
    stale_create = asyncio.create_task(host.create_session("owner-1", "session-1", _request()))
    await asyncio.wait_for(resolver.first_started.wait(), timeout=5)

    resolver.service_lease_epoch = 8
    current = await asyncio.wait_for(
        host.create_session("owner-1", "session-1", _request()), timeout=5
    )
    await asyncio.wait_for(host.wait_for_background_tasks(), timeout=5)
    resolver.release_first.set()
    stale_result = await asyncio.wait_for(stale_create, timeout=5)
    await asyncio.wait_for(host.wait_for_background_tasks(), timeout=5)

    assert stale_result.voice_session_id == current.voice_session_id
    assert [binding.service_lease_epoch for binding in backend.start_calls] == [8]
    assert backend.stop_calls == []


@pytest.mark.asyncio
async def test_shutdown_surfaces_failed_backend_stop_and_allows_retry():
    backend = FakeRuntimeBackend(fail_stop_count=1)
    host = _host(backend)
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()

    with pytest.raises(RuntimeHostError) as error:
        await host.shutdown()

    assert error.value.code == "runtime_unavailable"
    failed = await host.get_status("owner-1", ready.voice_session_id)
    assert failed.state == "error"
    assert len(backend.stop_calls) == 1
    assert backend.stop_calls[0][1] is not None

    await host.shutdown()

    stopped = await host.get_status("owner-1", ready.voice_session_id)
    assert stopped.state == "stopped"
    assert len(backend.stop_calls) == 2


@pytest.mark.asyncio
async def test_host_generation_fence_revokes_voice_admission_capability():
    from kollabor_agent.voice_admission import (
        issue_voice_admission_context,
        validate_voice_admission_context,
    )

    backend = FakeRuntimeBackend()
    host = _host(backend)
    ready = await host.create_session("owner-1", "session-1", _request())
    await host.wait_for_background_tasks()
    binding = backend.start_calls[0]
    context = issue_voice_admission_context(binding, str(uuid4()))
    assert validate_voice_admission_context(context) is True

    await host.stop_session(
        "owner-1",
        ready.voice_session_id,
        StopVoiceSessionRequest(generation=ready.generation, reason="user"),
    )
    await host.wait_for_background_tasks()

    assert validate_voice_admission_context(context) is False
