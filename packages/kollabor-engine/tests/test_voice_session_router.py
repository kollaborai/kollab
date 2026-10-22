"""Voice transcript admission uses the selected Engine session and normal history."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from kollabor_engine.routes import messages
from kollabor_engine.voice_runtime_host import EngineRuntimeHost, ResolvedVoiceTarget, RuntimeHostError
from kollabor_engine.voice_session_router import EngineSessionRouter
from kollabor_engine.voice_types import (
    CreateVoiceSessionRequest,
    VoiceSessionBinding,
    VoiceTranscriptSnapshot,
)


class FakeTargetResolver:
    async def resolve(self, owner_id, control_session_id, selected_thread_id):
        return ResolvedVoiceTarget(
            agent_id="agent-7",
            thread_id=selected_thread_id,
            voice_provider_profile_id="voice-profile-1",
            voice_prompt_revision="prompt-r3",
            service_lease_epoch=4,
        )


class FakeRuntimeBackend:
    def __init__(self):
        self.start_calls = []
        self.stop_calls = []

    async def prepare(self):
        return {"state": "ready"}

    async def get_status(self):
        return {"state": "ready"}

    async def start_session(self, binding):
        self.start_calls.append(binding)
        return object()

    async def stop_session(self, binding, handle, reason):
        self.stop_calls.append((binding, handle, reason))


class FakeEngineSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.sent = []
        self.cancelled = []

    async def send_message(self, message, *, request_id, thread_id, origin_context):
        self.sent.append(
            {
                "message": message,
                "request_id": request_id,
                "thread_id": thread_id,
                "origin_context": origin_context,
            }
        )
        return {"accepted": True, "queued": True, "request_id": request_id}

    async def cancel_owned_turn(self, request_id):
        self.cancelled.append(request_id)
        return {"cancelled": True}


class FakeSessionLookup:
    def __init__(self, owner_id, session):
        self.owner_id = owner_id
        self.session = session

    async def get_owned_session(self, owner_id, session_id):
        if owner_id != self.owner_id or session_id != self.session.session_id:
            return None
        return self.session


async def make_router():
    owner_id = "owner-1"
    backend = FakeRuntimeBackend()
    host = EngineRuntimeHost(
        session_resolver=FakeTargetResolver(),
        runtime=backend,
        platform_supported=lambda: True,
    )
    initial = await host.create_session(
        owner_id,
        "conversation-1",
        CreateVoiceSessionRequest(selected_thread_id="thread-1"),
    )
    await host.wait_for_background_tasks()
    binding = backend.start_calls[0]
    assert binding.voice_session_id == initial.voice_session_id
    session = FakeEngineSession("conversation-1")
    router = EngineSessionRouter(
        runtime_host=host,
        session_lookup=FakeSessionLookup(owner_id, session),
    )
    return owner_id, host, router, binding, session


def transcript(binding: VoiceSessionBinding, *, turn_id: str | None = None):
    return VoiceTranscriptSnapshot(
        transcript_id=str(uuid4()),
        voice_session_id=binding.voice_session_id,
        turn_id=turn_id or str(uuid4()),
        generation=binding.generation,
        speaker="user",
        revision=1,
        final=True,
        display_text="Use the normal chat history.",
        spoken_text=None,
        language="en",
        observed_at_utc_ms=123,
    )


@pytest.mark.asyncio
async def test_final_transcript_is_submitted_to_selected_engine_session():
    owner_id, _, router, binding, session = await make_router()
    message = transcript(binding)

    receipt = await router.admit_final(binding, message)

    assert receipt.accepted is True
    assert receipt.request_id == message.turn_id
    assert receipt.conversation_session_id == binding.conversation_session_id
    assert receipt.queued is True
    assert receipt.duplicate is False
    assert session.sent == [
        {
            "message": message.display_text,
            "request_id": message.turn_id,
            "thread_id": binding.thread_id,
            "origin_context": {
                "source": "livekit_voice",
                "voice_session_id": binding.voice_session_id,
                "voice_provider_profile_id": binding.voice_provider_profile_id,
                "voice_prompt_revision": binding.voice_prompt_revision,
                "voice_tool_policy": binding.voice_tool_policy,
            },
        }
    ]


@pytest.mark.asyncio
async def test_duplicate_final_transcript_is_not_admitted_twice():
    _, _, router, binding, session = await make_router()
    message = transcript(binding)

    first = await router.admit_final(binding, message)
    duplicate = await router.admit_final(binding, message)

    assert first.accepted is True
    assert duplicate.duplicate is True
    assert duplicate.request_id == message.turn_id
    assert len(session.sent) == 1


@pytest.mark.asyncio
async def test_nonfinal_or_assistant_transcript_is_rejected_before_admission():
    _, _, router, binding, session = await make_router()
    assistant = transcript(binding).model_copy(update={"speaker": "assistant"})
    partial = transcript(binding).model_copy(update={"final": False})

    for invalid in (assistant, partial):
        with pytest.raises(RuntimeHostError) as error:
            await router.admit_final(binding, invalid)
        assert error.value.code == "invalid_transcript"

    assert session.sent == []


@pytest.mark.asyncio
async def test_stale_transcript_generation_is_rejected():
    _, _, router, binding, session = await make_router()
    stale = transcript(binding).model_copy(update={"generation": binding.generation + 1})

    with pytest.raises(RuntimeHostError) as error:
        await router.admit_final(binding, stale)

    assert error.value.code == "stale_generation"
    assert session.sent == []


@pytest.mark.asyncio
async def test_voice_cancel_requires_an_admitted_turn_id():
    _, _, router, binding, session = await make_router()
    turn_id = str(uuid4())

    with pytest.raises(RuntimeHostError) as error:
        await router.cancel_turn(binding, turn_id, binding.generation)
    assert error.value.code == "target_mismatch"
    assert session.cancelled == []

    message = transcript(binding, turn_id=turn_id)
    admission = await router.admit_final(binding, message)
    assert admission.accepted is True

    receipt = await router.cancel_turn(binding, turn_id, binding.generation)

    assert receipt.cancelled is True
    assert receipt.generation == binding.generation
    assert session.cancelled == [turn_id]


@pytest.mark.asyncio
async def test_public_message_route_cannot_forge_livekit_voice_origin():
    request = messages.MessageRequest(
        content="/hub stop all",
        origin_context={"source": "livekit_voice", "voice_session_id": "forged"},
    )

    with pytest.raises(HTTPException) as error:
        await messages.send_message("conversation-1", request)

    assert error.value.status_code == 403
    assert "voice session router" in error.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        {
            "text": "/hub stop all",
            "origin_context": {"source": "livekit_voice", "voice_session_id": "forged"},
        },
        {
            "parts": [
                {
                    "text": "/hub stop all",
                    "origin_context": {"source": "livekit_voice", "voice_session_id": "forged"},
                }
            ]
        },
    ],
)
async def test_public_message_route_rejects_nested_livekit_origin(content):
    request = messages.MessageRequest(content=content)

    with pytest.raises(HTTPException) as error:
        await messages.send_message("conversation-1", request)

    assert error.value.status_code == 403
    assert "voice session router" in error.value.detail


@pytest.mark.asyncio
async def test_forged_same_owner_binding_never_sends_or_cancels_selected_session():
    _, _, router, binding, session = await make_router()
    forged = binding.model_copy(update={"thread_id": "another-thread"})
    message = transcript(forged)

    with pytest.raises(RuntimeHostError) as admission_error:
        await router.admit_final(forged, message)
    assert admission_error.value.code == "target_mismatch"
    assert session.sent == []

    with pytest.raises(RuntimeHostError) as cancel_error:
        await router.cancel_turn(forged, message.turn_id, forged.generation)
    assert cancel_error.value.code == "target_mismatch"
    assert session.cancelled == []
