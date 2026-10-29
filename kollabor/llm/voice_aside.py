"""Words said while the agent's turn runs (#388): an answer now, the turn keeps going.

The Quick Model (Settings → Voice → Quick Model) answers from what the user can see
of the running turn, on a provider of its own: the running turn never rides it. An
answer is that voice turn's whole reply, published on the display tap with the
voice turn's id, so the Engine says it like any reply and nothing is queued.
FORWARD, or no Quick Model, a timeout or a reply that does not parse, says one line
now; the words then wait in the queue for their own turn, as they always have.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ASIDE_TIMEOUT_S = 2.5
ASIDE_CALLS = 10  # Quick Model calls in one window; past that, the local line
ASIDE_WINDOW_S = 300.0


class VoiceAside:
    """One Quick Model call at a time, ASIDE_CALLS of them per ASIDE_WINDOW_S."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._busy = False
        self._calls: deque[float] = deque()

    async def answer(
        self,
        text: str,
        turn_id: str,
        events: list[dict],
        ask_model: Callable[[str, str], Awaitable[Optional[str]]],
        publish: Callable[..., None],
    ) -> bool:
        """True when the Quick Model answered; False: a line was said, queue the words."""
        from kollabor_voice.responder import aside_snapshot, local_line, parse_aside

        now = time.time()  # the display tap's clock
        verdict = None
        if self._take_slot():
            try:
                reply = await asyncio.wait_for(
                    ask_model(aside_snapshot(events, now), text), ASIDE_TIMEOUT_S
                )
                verdict = parse_aside(reply)
            except Exception as exc:  # a timeout or a provider error
                logger.info(
                    "voice aside fell back to the local line (%s)", type(exc).__name__
                )
            finally:
                self._busy = False
        kind, line = verdict or ("FORWARD", local_line(events, now))
        if kind == "ANSWER":
            publish("response", text=line, request_id=turn_id, voice_aside=True)
            publish(
                "turn_complete",
                request_id=turn_id,
                voice_aside=True,
                input_tokens=0,
                output_tokens=0,
                tool_calls=0,
                stop_reason="end_turn",
            )
            return True
        publish("voice_aside", text=line, request_id=turn_id)
        return False

    def _take_slot(self) -> bool:
        now = self._clock()
        while self._calls and now - self._calls[0] > ASIDE_WINDOW_S:
            self._calls.popleft()
        if self._busy or len(self._calls) >= ASIDE_CALLS:
            return False
        self._busy = True
        self._calls.append(now)
        return True


async def quick_model_reply(
    api_service: Any, profile_manager: Any, snapshot: str, text: str
) -> Optional[str]:
    """One call to the Quick Model on a provider of its own; None when none is set."""
    from kollabor_voice.responder import ASIDE_PROMPT

    from kollabor_ai.providers.registry import (
        ProviderRegistry,
        create_config_from_profile,
    )

    from .voice_turn import _quick_loadout

    quick = _quick_loadout(api_service, profile_manager)
    profile = profile_manager.get_profile(quick.provider_profile) if quick else None
    if profile is None or profile.get_provider() == "acp":
        return None  # no Quick Model, or an ACP agent, which cannot answer one call
    data = dict(profile.to_dict())
    data["model"] = quick.model
    data["max_tokens"] = 400  # one spoken line, with room for a model that thinks first
    if data.get("effort"):
        data["effort"] = "low"  # sent only where chat already sends an effort
    provider = await ProviderRegistry.get_provider(create_config_from_profile(data))
    response = await provider.call(
        messages=[
            {"role": "system", "content": ASIDE_PROMPT},
            {"role": "user", "content": f"{snapshot}\n\nThe user said: {text}"},
        ]
    )
    if hasattr(response, "get_text_content"):
        return response.get_text_content()
    return response if isinstance(response, str) else None
