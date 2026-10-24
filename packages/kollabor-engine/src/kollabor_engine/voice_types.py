"""Shared Engine DTOs for the WebUI voice session runtime.

These models mirror the frozen contracts in specs/voice-mode/livekit-web.
Keep the Engine as the sole source of truth for session and generation fields.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

OwnerId = Annotated[str, Field(min_length=1)]
SessionId = Annotated[str, Field(min_length=1)]
AgentId = Annotated[str, Field(min_length=1)]
ThreadId = Annotated[str, Field(min_length=1)]
VoiceSessionId = Annotated[
    str,
    Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    ),
]
Generation = Annotated[int, Field(ge=1)]
ServiceLeaseEpoch = Annotated[int, Field(ge=0)]

VoiceSessionState = Literal[
    "idle",
    "starting",
    "ready",
    "requesting_permission",
    "connecting",
    "listening",
    "thinking",
    "speaking",
    "stopping",
    "stopped",
    "disconnected",
    "downloading_models",
    "resource_shortage",
    "degraded",
    "error",
]
RuntimeState = Literal["starting", "ready", "degraded", "error", "stopping"]
RuntimeComponent = Literal["livekit_server", "agent_worker", "speech_service"]
SpeechState = Literal["idle", "starting", "downloading", "ready", "degraded", "error"]
StopReason = Literal["user", "session_switch", "disconnect", "shutdown", "error"]
VoiceToolPolicy = Literal["voice_terminal_hub_v1"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeStatus(_FrozenModel):
    state: RuntimeState
    component: RuntimeComponent
    progress: float | None = None
    detail: str
    retryable: bool = False


class SpeechReadiness(_FrozenModel):
    stt_state: SpeechState
    stt_engine: str
    tts_state: SpeechState
    tts_engine: str
    detail: str | None = None
    progress: float | None = None


class CreateVoiceSessionRequest(_FrozenModel):
    selected_thread_id: ThreadId | None = None


class JoinVoiceSessionRequest(_FrozenModel):
    generation: Generation


class StopVoiceSessionRequest(_FrozenModel):
    generation: Generation
    reason: StopReason


class VoiceSessionView(_FrozenModel):
    voice_session_id: VoiceSessionId
    control_session_id: SessionId
    conversation_session_id: SessionId
    agent_id: AgentId
    thread_id: ThreadId | None
    voice_provider_profile_id: str
    voice_prompt_revision: str
    state: VoiceSessionState
    generation: Generation
    detail: str | None = None
    retryable: bool = False
    # Disclosure: transcript text goes to a non-local LLM endpoint.
    remote_llm: bool = False
    remote_llm_host: str | None = None


class VoiceSessionBinding(_FrozenModel):
    owner_id: OwnerId
    voice_session_id: VoiceSessionId
    control_session_id: SessionId
    conversation_session_id: SessionId
    agent_id: AgentId
    thread_id: ThreadId | None
    voice_provider_profile_id: str
    voice_prompt_revision: str
    voice_tool_policy: VoiceToolPolicy
    generation: Generation
    service_lease_epoch: ServiceLeaseEpoch


class VoiceSessionTicket(_FrozenModel):
    voice_session_id: VoiceSessionId
    generation: Generation
    room_name: str
    livekit_url: str
    participant_token: str
    expires_at_utc_ms: int


TurnId = VoiceSessionId
TranscriptId = VoiceSessionId
TranscriptRevision = Annotated[int, Field(ge=1)]


class VoiceTranscriptSnapshot(_FrozenModel):
    transcript_id: TranscriptId
    voice_session_id: VoiceSessionId
    turn_id: TurnId
    generation: Generation
    speaker: Literal["user", "assistant"]
    revision: TranscriptRevision
    final: bool
    display_text: str
    spoken_text: str | None
    language: str | None
    observed_at_utc_ms: Annotated[int, Field(ge=0)]


# A final user transcript is a validated projection, not a second wire schema.
FinalTranscript = VoiceTranscriptSnapshot


class VoiceTurnRequest(VoiceTranscriptSnapshot):
    """A /turns body: the final transcript, or kind "greeting" (a hidden instruction turn)."""

    kind: Literal["speech", "greeting"] = "speech"


class VoiceTurnContext(_FrozenModel):
    source: Literal["livekit_voice"]
    voice_session_id: VoiceSessionId
    voice_provider_profile_id: str
    voice_prompt_revision: str
    voice_tool_policy: VoiceToolPolicy


class AdmissionReceipt(_FrozenModel):
    accepted: bool
    request_id: TurnId
    conversation_session_id: SessionId
    queued: bool
    duplicate: bool


class CancelReceipt(_FrozenModel):
    cancelled: bool
    generation: Generation
