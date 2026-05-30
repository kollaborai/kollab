"""Shared session-resume logic for conversation browsers.

Both the fullscreen (`ConversationsPlugin`) and AltView (`ConversationsAltView`)
conversation browsers let the user pick a past session to resume. So does the
`/resume <id>` command. All three drive the SAME daemon-side operation:
``state_service.resume_conversation(session_id)`` loads the session into the
daemon's conversation manager, swaps the live ``conversation_history`` (so the
next API turn continues with it), and returns display-ready metadata. The
caller renders that metadata via the message coordinator.

Why daemon-side and not a local emit: in attach mode the browser runs in the
client process, but the client never registers the ADD_MESSAGE handler -- a
client-side emit silently does nothing, and loading history into the client's
headless conversation manager leaves the daemon's state stale. Routing through
``state_service`` makes the DAEMON do the load+swap (verified: the resumed
messages appear in the next turn's wire request) and the rendered result streams
to the client like any other output. Works identically in local and attach mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ResumeOutcome:
    """Result of a resume attempt, for the caller to wrap in a CommandResult."""

    success: bool
    # resumed=False with success=True means there was nothing to resume (the
    # user quit the browser without selecting). Callers treat it as a no-op.
    resumed: bool = False
    session_id: Optional[str] = None
    error: Optional[str] = None


def _selected_session_id(browser: Any) -> Optional[str]:
    """Pull the selected session id from a conversation browser, if any."""
    if not hasattr(browser, "get_resume_session"):
        return None
    resume_session = browser.get_resume_session()
    if not resume_session:
        return None
    return resume_session.get("session_id")


def _render_resume_result(renderer: Any, result: dict) -> bool:
    """Render the daemon's resume result via the message coordinator.

    Returns True if it was rendered, False if no usable renderer was available
    (pipe mode / tests). Mirrors the /resume command's render path so all three
    surfaces show resumed conversations identically.
    """
    coordinator = getattr(renderer, "message_coordinator", None) if renderer else None
    if coordinator is None:
        return False

    header = result.get("header", "--- Resumed ---")
    success_msg = result.get("success_message", "[ok] Resumed. Continue below.")
    messages = result.get("messages", [])

    display_sequence: List[Tuple[str, str, dict]] = [("system", header, {})]
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant"):
            display_sequence.append((role, content, {}))
    display_sequence.append(("system", success_msg, {}))
    coordinator.display_message_sequence(display_sequence)
    return True


async def resume_session_id(
    event_bus: Any, renderer: Any, session_id: str
) -> ResumeOutcome:
    """Resume a specific session id via the daemon, then render the result.

    The single source of truth for "resume this session and show it" -- shared
    by the conversation browsers and the `/resume <id>` command so there is one
    daemon-routed path, not several copies.

    Args:
        event_bus: Event bus exposing ``get_service('state_service')``.
        renderer: A renderer with a ``message_coordinator`` for display. May be
            None (pipe mode); the resume still happens, just unrendered.
        session_id: The session to resume.

    Returns:
        ResumeOutcome.
    """
    state_service = None
    if event_bus is not None and hasattr(event_bus, "get_service"):
        state_service = event_bus.get_service("state_service")
    if state_service is None:
        return ResumeOutcome(
            success=False,
            error="State service not available -- cannot resume.",
            session_id=session_id,
        )

    logger.info("Resuming session via state_service: %s", session_id)
    try:
        result = await state_service.resume_conversation(session_id)
    except ValueError as e:
        return ResumeOutcome(
            success=False, error=f"Failed to resume session: {e}", session_id=session_id
        )
    except Exception as e:  # noqa: BLE001 - surface any RPC/daemon failure to the user
        logger.error("state_service.resume_conversation failed: %s", e)
        return ResumeOutcome(
            success=False,
            error=f"Error resuming conversation: {e}",
            session_id=session_id,
        )

    _render_resume_result(renderer, result if isinstance(result, dict) else {})
    return ResumeOutcome(success=True, resumed=True, session_id=session_id)


async def resume_browser_selection(
    event_bus: Any, renderer: Any, browser: Any
) -> ResumeOutcome:
    """Resume whatever session a conversation browser selected, if any.

    Thin wrapper over :func:`resume_session_id` that first pulls the selection
    off the browser. No-op (success, resumed=False) when nothing was selected.
    Shared by the altview and fullscreen conversation browsers.
    """
    session_id = _selected_session_id(browser)
    if not session_id:
        return ResumeOutcome(success=True, resumed=False)
    return await resume_session_id(event_bus, renderer, session_id)
