"""AltView sessions status widget.

Shows active altview sessions with status indicators in the status bar.
Format: altview: session-name  other-session(idle)

- plain name = agent still working or session suspended
- (idle) = background work completed, results ready to review
"""

import logging
from typing import Any, Optional

from kollabor_tui.design_system import T

from .utils import fg as _fg
from .widget_registry import StatusWidgetRegistry, WidgetCategory

logger = logging.getLogger(__name__)


def render_altview_status(width: int, ctx: Optional[Any] = None) -> str:
    """Render active altview sessions.

    Returns empty string when no sessions exist (hides widget).
    Uses event_bus service lookup to find the altview_stack_manager.
    """
    try:
        stack_manager = None
        if ctx:
            event_bus = getattr(ctx, "event_bus", None)
            if event_bus and hasattr(event_bus, "get_service"):
                stack_manager = event_bus.get_service("altview_stack_manager")

        if not stack_manager:
            return ""

        # Get only sessions that should be visible in status. The stack
        # manager filters out closed non-background views that remain in
        # the registry for re-entry.
        sessions = []
        if hasattr(stack_manager, "get_status_sessions"):
            sessions = stack_manager.get_status_sessions()
        elif hasattr(stack_manager, "get_all_sessions"):
            sessions = stack_manager.get_all_sessions()
            if isinstance(sessions, dict):
                sessions = list(sessions.values())
        elif hasattr(stack_manager, "sessions"):
            sessions = list(stack_manager.sessions.values())

        if not sessions:
            return ""

        # Build session display fragments
        prefix = _fg("altview:", T().ai_tag)
        fragments = []
        for session in sessions:
            if not _should_show_session(session):
                continue

            name = _session_name(session)
            status = _session_status(session)

            if status == "idle" or status == "completed":
                # Idle/completed: warn color with (idle) suffix
                fragment = _fg(f"{name}", T().warning[0]) + _fg("(idle)", T().text_dim)
            elif status == "suspended":
                # Suspended: dim text
                fragment = _fg(name, T().text_dim)
            else:
                # Running/active: primary color
                fragment = _fg(name, T().primary[0])

            fragments.append(fragment)

        if not fragments:
            return ""

        # Join with double space separator
        separator = "  "
        display = f"{prefix} {separator.join(fragments)}"

        # Calculate visible length for truncation check
        visible_parts = []
        visible_sessions = [session for session in sessions if _should_show_session(session)]
        for session in visible_sessions:
            name = _session_name(session)
            status = _session_status(session)
            if status in ("idle", "completed"):
                visible_parts.append(f"{name}(idle)")
            else:
                visible_parts.append(name)

        visible_text = f"altview: {separator.join(visible_parts)}"

        if len(visible_text) > width:
            # Truncate: show count instead of full list
            count = len(visible_sessions)
            idle_count = sum(
                1
                for s in visible_sessions
                if _session_status(s) in ("idle", "completed")
            )
            if idle_count > 0:
                summary = f"{count} views ({idle_count} idle)"
            else:
                summary = f"{count} views"
            return f"{prefix} {_fg(summary, T().text)}"

        return display

    except Exception as e:
        logger.error(f"altview widget error: {e}")
        return ""


def _session_name(session: Any) -> str:
    """Return a stable display name for a status session."""
    return str(
        getattr(
            session,
            "name",
            getattr(session, "session_name", str(session)),
        )
    )


def _session_status(session: Any) -> str:
    """Normalize AltView state/status values for display."""
    status = getattr(session, "status", None)
    if status is None:
        altview = getattr(session, "altview", None)
        status = getattr(altview, "state", "running")

    value = getattr(status, "value", status)
    if value == "complete":
        return "completed"
    return str(value)


def _should_show_session(session: Any) -> bool:
    """Return True when a session is active or has background results."""
    status = _session_status(session)
    if status == "running":
        return True

    altview = getattr(session, "altview", None)
    metadata = getattr(altview, "metadata", None)
    supports_background = bool(getattr(metadata, "supports_background", False))
    background_tasks = getattr(altview, "background_tasks", [])
    if supports_background and (background_tasks or status in ("idle", "completed")):
        return True

    return False


def register_altview_widget(registry: StatusWidgetRegistry) -> None:
    """Register the altview status widget."""
    registry.register(
        id="altview",
        name="AltView Sessions",
        description="Active altview sessions with status indicators",
        render_fn=render_altview_status,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=10,
        interactive=True,
        interaction_type="command",
        command="/altview",
    )
