"""Process-local capabilities for admitted LiveKit voice turns.

A `source` string is not authority. The engine issues a random capability only
inside a binding-checked turn admission operation; consumers validate it against
this in-memory registry. Capabilities are never persisted and are revoked when
the turn or voice-session generation ends.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

VOICE_ADMISSION_TTL_SECONDS = 10 * 60
_BINDING_FIELDS = (
    "owner_id",
    "voice_session_id",
    "control_session_id",
    "conversation_session_id",
    "agent_id",
    "thread_id",
    "voice_provider_profile_id",
    "voice_prompt_revision",
    "voice_tool_policy",
    "generation",
    "service_lease_epoch",
)


@dataclass(frozen=True, slots=True)
class _ActiveAdmission:
    identity: tuple[Any, ...]
    expires_at: float


class VoiceAdmissionCapabilityRegistry:
    """Tracks server-issued, short-lived capabilities for admitted turns."""

    def __init__(
        self,
        *,
        ttl_seconds: float = VOICE_ADMISSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._active: dict[str, _ActiveAdmission] = {}

    def issue(
        self,
        binding: Mapping[str, Any] | Any,
        turn_id: str,
        *,
        base_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("turn_id is required")
        values = _binding_values(binding)
        identity = tuple(values.get(field) for field in _BINDING_FIELDS) + (turn_id,)
        if any(identity[index] is None for index in (0, 1, 2, 3, 4, 6, 7, 8, 9, 10)):
            raise ValueError("voice admission binding is incomplete")
        token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl_seconds
        with self._lock:
            self._active[token] = _ActiveAdmission(identity=identity, expires_at=expires_at)

        context = dict(base_context or {})
        context.update(values)
        context.update(
            {
                "source": "livekit_voice",
                "turn_id": turn_id,
                "admission_capability": token,
            }
        )
        return context

    def validate(self, context: Any) -> bool:
        if not isinstance(context, Mapping) or context.get("source") != "livekit_voice":
            return False
        token = context.get("admission_capability")
        turn_id = context.get("turn_id")
        if not isinstance(token, str) or not token or not isinstance(turn_id, str):
            return False
        identity = tuple(context.get(field) for field in _BINDING_FIELDS) + (turn_id,)
        with self._lock:
            active = self._active.get(token)
            if active is None:
                return False
            if active.expires_at <= self._clock():
                self._active.pop(token, None)
                return False
            return active.identity == identity

    def revoke(self, context: Any) -> None:
        if not isinstance(context, Mapping):
            return
        token = context.get("admission_capability")
        if isinstance(token, str):
            with self._lock:
                self._active.pop(token, None)

    def revoke_turn(self, voice_session_id: str, generation: int, turn_id: str) -> None:
        self._revoke_matching(
            lambda identity: identity[1] == voice_session_id
            and identity[9] == generation
            and identity[-1] == turn_id
        )

    def revoke_generation(self, voice_session_id: str, generation: int) -> None:
        self._revoke_matching(
            lambda identity: identity[1] == voice_session_id and identity[9] == generation
        )

    def _revoke_matching(self, predicate: Callable[[tuple[Any, ...]], bool]) -> None:
        with self._lock:
            expired = [
                token
                for token, admission in self._active.items()
                if admission.expires_at <= self._clock()
            ]
            for token in expired:
                self._active.pop(token, None)
            for token, admission in tuple(self._active.items()):
                if predicate(admission.identity):
                    self._active.pop(token, None)


def _binding_values(binding: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(binding, Mapping):
        return dict(binding)
    model_dump = getattr(binding, "model_dump", None)
    if callable(model_dump):
        values = model_dump(mode="python")
        if isinstance(values, dict):
            return values
    raise TypeError("binding must be a mapping or model with model_dump")


VOICE_ADMISSION_CAPABILITIES = VoiceAdmissionCapabilityRegistry()


def issue_voice_admission_context(
    binding: Mapping[str, Any] | Any,
    turn_id: str,
    *,
    base_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return VOICE_ADMISSION_CAPABILITIES.issue(binding, turn_id, base_context=base_context)


def validate_voice_admission_context(context: Any) -> bool:
    return VOICE_ADMISSION_CAPABILITIES.validate(context)


def revoke_voice_admission_context(context: Any) -> None:
    VOICE_ADMISSION_CAPABILITIES.revoke(context)


def revoke_voice_admissions_for_turn(
    voice_session_id: str,
    generation: int,
    turn_id: str,
) -> None:
    VOICE_ADMISSION_CAPABILITIES.revoke_turn(voice_session_id, generation, turn_id)


def revoke_voice_admissions_for_generation(voice_session_id: str, generation: int) -> None:
    VOICE_ADMISSION_CAPABILITIES.revoke_generation(voice_session_id, generation)
