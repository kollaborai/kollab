"""Error helpers for the voice worker's Engine turn calls (#571)."""

from __future__ import annotations

import json


def rejected_detail(body_text: str) -> str:
    """The Engine's own explanation from a rejected turn's response body.

    The Engine answers a reload-needed turn with a plain-language detail;
    the worker must pass it on instead of a bare status code.
    """
    try:
        payload = json.loads(body_text)
    except ValueError:
        return body_text.strip()[:300]
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if detail:
            return str(detail)[:300]
    return ""
