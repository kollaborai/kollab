"""Process-local capabilities for admitted voice turns (LiveKit rooms and /voice).

A `source` string is not authority. The engine issues a random capability only
inside a binding-checked turn admission operation; consumers validate it against
this in-memory registry. Capabilities are never persisted and are revoked when
the turn or voice-session generation ends.

The engine and the daemon are separate processes, each with its own registry.
The engine mirrors a capability into the daemon (`register`, over the
`state.voice_admission` RPC) before the turn is sent, and revoke listeners tell
the engine when to mirror a revoke so a running daemon turn stops too.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

RevokeListener = Callable[[str, int, "str | None"], None]
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
        self._revoke_listeners: list[RevokeListener] = []

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

    def register(self, context: Any) -> bool:
        """Mirror a capability another process issued; False when it is malformed."""
        if not isinstance(context, Mapping) or context.get("source") != "livekit_voice":
            return False
        token, turn_id = context.get("admission_capability"), context.get("turn_id")
        if not isinstance(token, str) or not token or not isinstance(turn_id, str) or not turn_id:
            return False
        identity = tuple(context.get(field) for field in _BINDING_FIELDS) + (turn_id,)
        if any(identity[index] is None for index in (0, 1, 2, 3, 4, 6, 7, 8, 9, 10)):
            return False
        with self._lock:
            self._active[token] = _ActiveAdmission(
                identity=identity, expires_at=self._clock() + self._ttl_seconds
            )
        return True

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

    def add_revoke_listener(self, listener: RevokeListener) -> Callable[[], None]:
        """Call `listener(voice_session_id, generation, turn_id | None)` after a revoke."""
        with self._lock:
            self._revoke_listeners.append(listener)

        def remove() -> None:
            with self._lock:
                if listener in self._revoke_listeners:
                    self._revoke_listeners.remove(listener)

        return remove

    def _notify(self, voice_session_id: str, generation: int, turn_id: str | None) -> None:
        with self._lock:
            listeners = tuple(self._revoke_listeners)
        for listener in listeners:
            try:
                listener(voice_session_id, generation, turn_id)
            except Exception:  # a mirror failing must never undo a local revoke
                logger.warning("voice admission revoke listener failed", exc_info=True)

    def revoke_turn(self, voice_session_id: str, generation: int, turn_id: str) -> None:
        self._revoke_matching(
            lambda identity: identity[1] == voice_session_id
            and identity[9] == generation
            and identity[-1] == turn_id
        )
        self._notify(voice_session_id, generation, turn_id)

    def revoke_generation(self, voice_session_id: str, generation: int) -> None:
        self._revoke_matching(
            lambda identity: identity[1] == voice_session_id and identity[9] == generation
        )
        self._notify(voice_session_id, generation, None)

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


def register_voice_admission_context(context: Any) -> bool:
    return VOICE_ADMISSION_CAPABILITIES.register(context)


def add_voice_admission_revoke_listener(listener: RevokeListener) -> Callable[[], None]:
    return VOICE_ADMISSION_CAPABILITIES.add_revoke_listener(listener)


def validate_voice_admission_context(context: Any) -> bool:
    return VOICE_ADMISSION_CAPABILITIES.validate(context)


def revoke_voice_admission_context(context: Any) -> None:
    """End a turn's grant, a LiveKit room's or the /voice plugin's."""
    VOICE_ADMISSION_CAPABILITIES.revoke(context)
    DEVICE_VOICE_ADMISSIONS.revoke(context)


# The /voice plugin's turns (plugins/voice_plugin.py): the same voice tool policy
# as a LiveKit room's, anchored on the device voice admission instead.
DEVICE_VOICE_SOURCE = "voice"
VOICE_ORIGIN_SOURCES = frozenset({"livekit_voice", DEVICE_VOICE_SOURCE})
_DEVICE_FIELDS = ("voice_session_id", "generation", "turn_id", "voice_tool_policy")


def is_voice_origin(context: Any) -> bool:
    """A turn the voice tool policy governs: a LiveKit room's or the /voice plugin's."""
    return isinstance(context, Mapping) and context.get("source") in VOICE_ORIGIN_SOURCES


class DeviceVoiceAdmissions:
    """Capabilities for the turns the /voice plugin admits.

    ``VoicePlugin.admit`` issues one in the process that runs the turn, once the
    turn's lease epoch checks out, and nothing else does: a context that only
    says ``source: "voice"`` holds no authority, and the voice guard refuses its
    tools. Never persisted; revoked when the turn ends.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = VOICE_ADMISSION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[str, _ActiveAdmission] = {}

    def issue(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        identity = tuple(fields.get(name) for name in _DEVICE_FIELDS)
        if any(value is None or value == "" for value in identity):
            raise ValueError("device voice admission is incomplete")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._active[token] = _ActiveAdmission(identity, self._clock() + self._ttl_seconds)
        return {**fields, "source": DEVICE_VOICE_SOURCE, "admission_capability": token}

    def validate(self, context: Any) -> bool:
        if not isinstance(context, Mapping) or context.get("source") != DEVICE_VOICE_SOURCE:
            return False
        token = context.get("admission_capability")
        if not isinstance(token, str) or not token:
            return False
        with self._lock:
            active = self._active.get(token)
            if active is None:
                return False
            if active.expires_at <= self._clock():
                self._active.pop(token, None)
                return False
            return active.identity == tuple(context.get(name) for name in _DEVICE_FIELDS)

    def revoke(self, context: Any) -> None:
        token = context.get("admission_capability") if isinstance(context, Mapping) else None
        if isinstance(token, str):
            with self._lock:
                self._active.pop(token, None)


DEVICE_VOICE_ADMISSIONS = DeviceVoiceAdmissions()


def issue_device_voice_context(fields: Mapping[str, Any]) -> dict[str, Any]:
    return DEVICE_VOICE_ADMISSIONS.issue(fields)


def validate_voice_origin(context: Any) -> bool:
    """A voice turn's grant holds: the engine's capability, or the /voice plugin's."""
    if isinstance(context, Mapping) and context.get("source") == DEVICE_VOICE_SOURCE:
        return DEVICE_VOICE_ADMISSIONS.validate(context)
    return VOICE_ADMISSION_CAPABILITIES.validate(context)


def revoke_voice_admissions_for_turn(
    voice_session_id: str,
    generation: int,
    turn_id: str,
) -> None:
    VOICE_ADMISSION_CAPABILITIES.revoke_turn(voice_session_id, generation, turn_id)


def revoke_voice_admissions_for_generation(voice_session_id: str, generation: int) -> None:
    VOICE_ADMISSION_CAPABILITIES.revoke_generation(voice_session_id, generation)
