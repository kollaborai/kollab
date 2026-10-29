"""Voice settings for the model calls of one LiveKit voice turn, and no other turn.

The engine puts its voice settings in the turn's origin context (prompt overlay,
reasoning, voice provider profile). The quick model (Settings → Voice → Quick
Model) comes from config instead, and names a loadout so its provider's own
credentials carry the call. This applies them around that turn's model calls
only; the stored conversation, the system prompt and normal chat are never
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
        content = (
            message.get("content") if is_dict else getattr(message, "content", None)
        )
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

    The quick model (kollabor.voice.responder_model, a loadout name) wins: it
    may name another provider, whose own profile and credentials carry the
    call (an OpenRouter fast model while the chat runs elsewhere). Without
    one, the profile switches only when the turn's voice profile differs from
    the active one. Reasoning runs at the lowest level the model takes.
    """
    active = getattr(api_service, "_profile", None)
    quick = _quick_loadout(api_service, profile_manager)
    switched = False
    if quick is not None:
        profile = (
            profile_manager.get_profile(quick.provider_profile)
            if profile_manager is not None
            else None
        )
        if profile is None or profile.get_provider() == "acp":
            # A missing profile has no connection and an agent CLI (ACP) cannot
            # answer one model call: the turn runs as if no quick model were set.
            logger.warning(
                "quick model %r cannot run on provider profile %r; ignoring it",
                quick.name,
                quick.provider_profile,
            )
            quick = None
        elif getattr(profile, "name", None) != getattr(active, "name", None):
            if not await api_service.reinitialize_provider(profile):
                await api_service.reinitialize_provider(active)
                raise RuntimeError(
                    f"quick model provider {quick.provider_profile!r} could not start"
                )
            switched = True
    if quick is None:
        wanted = settings.get("voice_provider_profile_id")
        voice_profile = None
        if (
            wanted
            and wanted != getattr(active, "name", None)
            and profile_manager is not None
        ):
            voice_profile = profile_manager.get_profile(wanted)
            if voice_profile is None:
                logger.warning(
                    "voice profile %r not found; using the active profile", wanted
                )
        if voice_profile is not None:
            if not await api_service.reinitialize_provider(voice_profile):
                await api_service.reinitialize_provider(active)
                raise RuntimeError(f"voice profile {wanted!r} could not start")
            switched = True
    previous_provider = getattr(api_service, "_provider", None)
    swapped_model = False
    config = None
    has_effort = False
    previous = None
    # ponytail: the turn swaps the one shared api_service (profile, provider,
    # effort), as the voice profile switch always has, so any other call on this
    # service while the turn runs rides the voice model too. Give the turn its own
    # provider object if that ever bites.
    try:
        if quick is not None:
            from kollabor_ai.fast_model import with_model

            provider = with_model(previous_provider, quick.model)
            swapped_model = provider is not previous_provider
            if swapped_model:
                api_service._provider = provider
            logger.info(
                "voice turn on the quick model %r (%s via %s)",
                quick.model,
                quick.name,
                quick.provider_profile,
            )
        config = getattr(getattr(api_service, "_provider", None), "config", None)
        has_effort = config is not None and hasattr(config, "effort")
        previous = config.effort if has_effort else None
        if has_effort:
            # No effort field means the model's own default (often medium), not
            # off, so "off" asks for the lowest level whenever chat already sends
            # an effort (the model takes one) and sends none otherwise.
            low = settings.get("voice_reasoning") == "low" or previous
            config.effort = "low" if low else None
        yield
    finally:
        if has_effort:
            config.effort = previous
        if swapped_model:
            api_service._provider = previous_provider
        if switched and not await api_service.reinitialize_provider(active):
            logger.error("could not restore the active profile after a voice turn")


def _quick_loadout(api_service: Any, profile_manager: Any) -> Any:
    """The configured quick model as a Loadout, or None. Exact names only."""
    from kollabor_voice.responder import responder_model

    from kollabor_ai.loadout_manager import LoadoutManager

    config = getattr(api_service, "config", None)
    name = responder_model(config)
    if not name or profile_manager is None:
        return None
    return LoadoutManager(profile_manager, config).get(name)
