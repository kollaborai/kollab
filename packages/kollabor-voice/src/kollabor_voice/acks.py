"""Still-working lines voice says while the agent has not answered yet.

Curated and fixed, so each is synthesized once and replayed from the speech
cache (speech_cache.py) without the speech engine.
"""

from __future__ import annotations

import random

LINES = {
    "still_working": (
        "Still working on it.",
        "Almost there, still checking.",
        "Bear with me, still looking into it.",
    ),
}


def pick(kind: str, previous: str | None = None) -> str:
    """One of the kind's lines, never the one said just before."""
    choices = [line for line in LINES[kind] if line != previous] or list(LINES[kind])
    return random.choice(choices)
