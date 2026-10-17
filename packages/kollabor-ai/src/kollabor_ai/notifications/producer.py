"""Tiny helper for producer sites to push env events.

Usage:
    from kollabor_ai.notifications.producer import push_env
    push_env(event_bus, "capability", "trust:full (was confirm_all)",
             kind="permission")

Fire-and-forget: if the env_queue service isn't registered (queue
plugin not loaded, pipe mode, tests) the push silently no-ops.
Nothing propagates — producers must not crash because notifications
aren't wired.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def push_env(
    event_bus: Any,
    symbol_key: str,
    message: str,
    kind: str = "external",
    collapse_key: Optional[str] = None,
) -> bool:
    """Push one event onto the env queue if available.

    Args:
        event_bus: the event bus carrying the ``env_queue`` service.
            None (or missing service) makes this a no-op.
        symbol_key: one of the keys in SYMBOLS.
        message: one-line human-readable text.
        kind: name of an EnvKind value (string form).
        collapse_key: optional dedup key for folding repeats.

    Returns:
        True when the event was queued, False when the rail was
        unavailable or the push failed. Callers that need the outage
        to be visible (e.g. approval-mode changes) check the result;
        fire-and-forget callers may ignore it. Never raises.
    """
    if event_bus is None:
        return False
    try:
        queue = event_bus.get_service("env_queue")
    except Exception:
        return False
    if queue is None:
        return False

    try:
        from .models import SYMBOLS, EnvEvent, EnvKind

        symbol = SYMBOLS.get(symbol_key)
        if symbol is None:
            return False
        try:
            kind_enum = EnvKind(kind)
        except ValueError:
            return False

        queue.push(
            EnvEvent(
                kind=kind_enum,
                symbol=symbol,
                message=message,
                collapse_key=collapse_key,
            )
        )
        return True
    except Exception as e:  # never raise out of a producer
        logger.debug("push_env failed: %s", e)
        return False
