"""Voice settings for the model calls of one LiveKit voice turn, and no other turn.

The engine puts its voice settings in the turn's origin context (prompt overlay,
reasoning, voice provider profile). This applies them around that turn's model
calls only; the stored conversation, the system prompt and normal chat are never
edited. Anything that is not a validated voice-origin context gets nothing.
"""

from __future__ import annotations

import dataclasses
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from kollabor_agent.voice_admission import validate_voice_admission_context

logger = logging.getLogger(__name__)


def voice_turn_settings(origin: Any) -> dict[str, Any] | None:
    """The turn's voice settings, or None unless it is a validated voice turn."""
    return dict(origin) if validate_voice_admission_context(origin) else None


def with_overlay(history: list[Any], overlay: str | None) -> list[Any]:
    """History whose system prompt ends with the voice overlay; `history` is untouched."""
    if not overlay:
        return history
    shaped = list(history)
    for index, message in enumerate(shaped):
        is_dict = isinstance(message, dict)
        role = message.get("role") if is_dict else getattr(message, "role", None)
        content = message.get("content") if is_dict else getattr(message, "content", None)
        if role == "system" and isinstance(content, str):
            merged = f"{content}\n\n{overlay}"
            shaped[index] = (
                {**message, "content": merged}
                if is_dict
                else dataclasses.replace(message, content=merged)
            )
            return shaped
    return [{"role": "system", "content": overlay}, *shaped]


@asynccontextmanager
async def voice_provider(
    api_service: Any, profile_manager: Any, settings: dict[str, Any]
) -> AsyncIterator[None]:
    """Run model calls on the voice profile with the voice reasoning, then put chat's back.

    The profile switches only when the voice profile differs from the active one.
    Reasoning runs at the lowest level the model takes.
    """
    active = getattr(api_service, "_profile", None)
    wanted = settings.get("voice_provider_profile_id")
    voice_profile = None
    if wanted and wanted != getattr(active, "name", None) and profile_manager is not None:
        voice_profile = profile_manager.get_profile(wanted)
        if voice_profile is None:
            logger.warning("voice profile %r not found; using the active profile", wanted)
    if voice_profile is not None:
        if not await api_service.reinitialize_provider(voice_profile):
            await api_service.reinitialize_provider(active)
            raise RuntimeError(f"voice profile {wanted!r} could not start")
    config = getattr(getattr(api_service, "_provider", None), "config", None)
    has_effort = config is not None and hasattr(config, "effort")
    previous = config.effort if has_effort else None
    if has_effort:
        # No effort field means the model's own default (often medium), not off,
        # so "off" asks for the lowest level whenever chat already sends an effort
        # (the model takes one) and sends none otherwise.
        # ponytail: providers read config.effort per request; a second concurrent
        # call (curator, dreaming) during a voice call would see it too.
        low = settings.get("voice_reasoning") == "low" or previous
        config.effort = "low" if low else None
    try:
        yield
    finally:
        if has_effort:
            config.effort = previous
        if voice_profile is not None and not await api_service.reinitialize_provider(active):
            logger.error("could not restore the active profile after a voice turn")
