"""Route final voice text into the selected Engine conversation."""

from __future__ import annotations

from typing import Any, Protocol

from .voice_runtime_host import EngineRuntimeHost, RuntimeHostError
from .voice_types import (
    AdmissionReceipt,
    CancelReceipt,
    OwnerId,
    SessionId,
    VoiceSessionBinding,
    VoiceTranscriptSnapshot,
    VoiceTurnContext,
)

MAX_FINAL_TRANSCRIPT_BYTES = 16 * 1024


class EngineSessionForVoice(Protocol):
    session_id: str

    async def send_message(
        self,
        message: str,
        *,
        request_id: str,
        thread_id: str | None,
        origin_context: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def cancel_owned_turn(self, request_id: str) -> dict[str, Any]: ...


class VoiceSessionLookup(Protocol):
    """Resolve only sessions the authenticated owner is allowed to control."""

    async def get_owned_session(self, owner_id: OwnerId, session_id: SessionId) -> EngineSessionForVoice | None: ...


class EngineSessionRouter:
    """Submit voice transcripts through the ordinary Engine message pipeline."""

    def __init__(
        self,
        *,
        runtime_host: EngineRuntimeHost,
        session_lookup: VoiceSessionLookup,
    ) -> None:
        self._runtime_host = runtime_host
        self._session_lookup = session_lookup

    async def admit_final(
        self,
        binding: VoiceSessionBinding,
        transcript: VoiceTranscriptSnapshot,
    ) -> AdmissionReceipt:
        self._validate_final(binding, transcript)
        session = await self._get_session(binding)
        context = VoiceTurnContext(
            source="livekit_voice",
            voice_session_id=binding.voice_session_id,
            voice_provider_profile_id=binding.voice_provider_profile_id,
            voice_prompt_revision=binding.voice_prompt_revision,
            voice_tool_policy=binding.voice_tool_policy,
        ).model_dump(mode="json")

        async def submit() -> AdmissionReceipt:
            result = await session.send_message(
                transcript.display_text,
                request_id=transcript.turn_id,
                thread_id=binding.thread_id,
                origin_context=context,
            )
            if not isinstance(result, dict) or result.get("accepted") is not True:
                raise RuntimeHostError("session_busy", 409, "The selected Engine session did not admit the voice turn.")
            accepted_request_id = result.get("request_id") or result.get("turn_id")
            if accepted_request_id is not None and accepted_request_id != transcript.turn_id:
                raise RuntimeHostError("admission_mismatch", 409, "Engine admitted a different request identifier.")
            return AdmissionReceipt(
                accepted=True,
                request_id=transcript.turn_id,
                conversation_session_id=binding.conversation_session_id,
                queued=bool(result.get("queued", True)),
                duplicate=False,
            )

        # RuntimeHost serializes this short enqueue against stop/generation
        # changes and caches the accepted turn ID for idempotent retries.
        return await self._runtime_host.admit_turn(binding, transcript.turn_id, submit)

    async def cancel_turn(
        self,
        binding: VoiceSessionBinding,
        turn_id: str,
        generation: int,
    ) -> CancelReceipt:
        if generation != binding.generation:
            raise RuntimeHostError("stale_generation", 409, "Voice session generation is stale.")
        session = await self._get_session(binding)

        async def cancel() -> CancelReceipt:
            result = await session.cancel_owned_turn(turn_id)
            return CancelReceipt(
                cancelled=bool(result.get("cancelled")) if isinstance(result, dict) else False,
                generation=generation,
            )

        return await self._runtime_host.run_if_current(binding, cancel)

    async def _get_session(self, binding: VoiceSessionBinding) -> EngineSessionForVoice:
        if binding.control_session_id != binding.conversation_session_id:
            raise RuntimeHostError("target_mismatch", 409, "Voice control and conversation sessions must match.")
        session = await self._session_lookup.get_owned_session(binding.owner_id, binding.conversation_session_id)
        if session is None or session.session_id != binding.conversation_session_id:
            raise RuntimeHostError("session_not_found", 404, "The selected Engine session was not found.")
        return session

    @staticmethod
    def _validate_final(
        binding: VoiceSessionBinding,
        transcript: VoiceTranscriptSnapshot,
    ) -> None:
        if transcript.voice_session_id != binding.voice_session_id or transcript.generation != binding.generation:
            raise RuntimeHostError("stale_generation", 409, "Voice transcript belongs to a stale session generation.")
        if transcript.speaker != "user" or not transcript.final or transcript.spoken_text is not None:
            raise RuntimeHostError("invalid_transcript", 422, "Only final user transcripts can enter chat history.")
        if not transcript.display_text.strip():
            raise RuntimeHostError("invalid_transcript", 422, "Voice transcript is empty.")
        if len(transcript.display_text.encode("utf-8")) > MAX_FINAL_TRANSCRIPT_BYTES:
            raise RuntimeHostError("transcript_too_large", 413, "Voice transcript exceeds the size limit.")
