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
# A provider names itself mid-sentence: "Invalid openai API key".
_BAD_KEY = re.compile(r"invalid ([\w.-]+ )?api key")
# What a native turn's exception type or provider error code says, ahead of its text.
_KINDS = {
    "AuthenticationError": "login",
    "authentication_error": "login",
    "invalid_api_key": "login",
    "insufficient_quota": "usage",
}

# What the fallback notice says happened, per kind.
REASONS = {
    "usage": "hit its usage limit",
    "rate": "is rate limited",
    "login": "needs a new login",
}


def failure_kind(
    message: str, error_type: Optional[str] = None, error_code: Optional[str] = None
) -> Optional[str]:
    """``"usage"``, ``"rate"`` or ``"login"`` when the model can't serve; else None.

    A native turn's error also has its exception type and provider error code;
    an ACP harness's has only its text. A context-window overflow is not one:
    the conversation is too big for any model of that size, and the compaction
    plugin owns that case.
    """
    text = (message or "").lower()
    if _CONTEXT.search(text):
        return None
    kind = _KINDS.get(str(error_code or "")) or _KINDS.get(str(error_type or ""))
    if kind or not text:
        return kind
    if any(hint in text for hint in _USAGE_HINTS):
        return "usage"
    if any(hint in text for hint in _RATE_HINTS):
        return "rate"
    if any(hint in text for hint in _LOGIN_HINTS) or _BAD_KEY.search(text):
        return "login"
    return None
