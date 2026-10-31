"""Does a failed turn's error mean the model itself cannot serve right now?

Used by the model fallback (``plugins/model_fallback_plugin.py``) for native
and ACP turns alike. ``acp_bridge.ACPBridge._request_error`` still keeps its
own copy of the refusal hints.
"""

from __future__ import annotations

import re
from typing import Optional

_USAGE_HINTS = (
    "usage limit", "weekly limit", "daily limit", "monthly limit", "hour limit",
    "quota exceeded", "quota exhausted", "quota limit", "insufficient quota",
    "exceeded your current quota", "insufficient balance", "no resource package",
    "credit balance is too low", "out of credits",
)
_RATE_HINTS = ("rate limit", "too many requests")
_LOGIN_HINTS = (
    "authenticate", "authentication", "oauth", "session expired", "not logged in",
    "please log in", "please sign in", "invalid credentials", "unauthorized",
    "unauthenticated", "invalid api key", "api key is missing", "credentials missing",
    "requires login", "token expired",
)
_CONTEXT = re.compile(r"context (window|length)|prompt is too long|too many tokens")

# What the fallback notice says happened, per kind.
REASONS = {
    "usage": "hit its usage limit",
    "rate": "is rate limited",
    "login": "needs a new login",
}


def failure_kind(message: str) -> Optional[str]:
    """``"usage"``, ``"rate"`` or ``"login"`` when the model can't serve; else None.

    A context-window overflow is not one: the conversation is too big for any
    model of that size, and the compaction plugin owns that case.
    """
    text = (message or "").lower()
    if not text or _CONTEXT.search(text):
        return None
    if any(hint in text for hint in _USAGE_HINTS):
        return "usage"
    if any(hint in text for hint in _RATE_HINTS):
        return "rate"
    if any(hint in text for hint in _LOGIN_HINTS):
        return "login"
    return None
