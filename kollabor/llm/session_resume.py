"""Shared session-resume logic for conversation browsers.

Both the fullscreen (`ConversationsPlugin`) and AltView (`ConversationsAltView`)
conversation browsers let the user pick a past session to resume. The actual
resume work -- load the session into the conversation manager, mint a fresh
session id, push the loaded history onto the live llm_service, and replay it to
the screen via ``ADD_MESSAGE`` -- is identical between them. It lives here so
neither command integrator has to depend on the other.

Process note: this runs in whichever process executes the command. In daemon
mode that is the daemon, where ``app.llm_service.conversation_manager`` lives,
and the ``ADD_MESSAGE`` emit streams to the attach client like any other output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResumeOutcome:
    """Result of a resume attempt, for the caller to wrap in a CommandResult."""

    success: bool
    # None when there was nothing to resume (user quit the browser without
    # selecting). Callers treat this as a no-op success, not a failure.
    resumed: bool = False
    session_id: Optional[str] = None
    new_session_id: Optional[str] = None
    error: Optional[str] = None


async def resume_selected_session(app: Any, event_bus: Any, browser: Any) -> ResumeOutcome:
    """Resume the session a conversation browser selected, if any.

    Args:
        app: The application instance (provides ``llm_service``).
        event_bus: Event bus used to emit ``ADD_MESSAGE`` for display.
        browser: The conversation browser plugin/view. Must expose
            ``get_resume_session() -> Optional[dict]`` returning a payload with
            a ``session_id`` key when the user selected a session.

    Returns:
        ResumeOutcome. ``resumed=False`` with ``success=True`` means the user
        did not pick anything -- a clean no-op, not an error.
    """
    if not hasattr(browser, "get_resume_session"):
        return ResumeOutcome(success=True, resumed=False)

    resume_session = browser.get_resume_session()
    if not resume_session or not app:
        return ResumeOutcome(success=True, resumed=False)

    session_id = resume_session.get("session_id")
    if not session_id:
        return ResumeOutcome(success=True, resumed=False)

    if not (hasattr(app, "llm_service") and app.llm_service):
        return ResumeOutcome(success=True, resumed=False)

    conv_mgr = app.llm_service.conversation_manager
    if not conv_mgr:
        return ResumeOutcome(success=True, resumed=False)

    logger.info(f"Resuming session: {session_id}")

    # Auto-save current conversation before swapping it out.
    if conv_mgr.messages:
        conv_mgr.save_conversation()

    # Load the selected session into the conversation manager.
    if not conv_mgr.load_session(session_id):
        return ResumeOutcome(
            success=False,
            error=f"Failed to load session: {session_id}",
            session_id=session_id,
        )

    # Mint a fresh session id so the resumed conversation does not overwrite
    # the original on save.
    from kollabor_ai import generate_session_name

    new_session_id = generate_session_name()
    conv_mgr.current_session_id = new_session_id

    # Push the loaded history onto the live llm_service and collect the
    # user/assistant turns for display.
    from kollabor_events.data_models import ConversationMessage

    llm_service = app.llm_service
    loaded_messages = []
    display_messages = []
    for msg in conv_mgr.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        loaded_messages.append(ConversationMessage(role=role, content=content))
        if role in ("user", "assistant"):
            display_messages.append({"role": role, "content": content})

    llm_service.conversation_history = loaded_messages
    if hasattr(llm_service, "session_stats"):
        llm_service.session_stats["messages"] = len(loaded_messages)

    # Replay the loaded conversation to the screen via ADD_MESSAGE.
    header = f"--- Resumed: {session_id[:20]}... as {new_session_id} ---"
    success_msg = f"[ok] Resumed: {new_session_id}. Continue below."
    all_messages = (
        [{"role": "system", "content": header}]
        + display_messages
        + [{"role": "system", "content": success_msg}]
    )

    from kollabor_events.models import EventType

    await event_bus.emit_with_hooks(
        EventType.ADD_MESSAGE,
        {
            "messages": all_messages,
            "options": {
                "show_loading": True,
                "loading_message": "Loading conversation...",
                "log_messages": False,
                "add_to_history": False,
                "display_messages": True,
            },
        },
        "conversations_resume",
    )

    return ResumeOutcome(
        success=True,
        resumed=True,
        session_id=session_id,
        new_session_id=new_session_id,
    )
