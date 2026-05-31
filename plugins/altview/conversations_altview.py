"""Conversations browser as an AltView plugin.

Provides a lazygit-style TUI for browsing and resuming conversations.
Two-pane layout: sessions list on left, message preview on right.
"""

import logging
from typing import Any, Dict, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg, wrap_text
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class ConversationsAltView(AltView):
    """AltView for browsing and managing conversations."""

    def __init__(self):
        metadata = AltViewMetadata(
            plugin_type="conversations",
            description="Browse and manage conversations",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[HST]",
            aliases=["convos", "sessions"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 15.0

        # State
        self.sessions: List[Dict[str, Any]] = []
        self.messages: List[Dict[str, Any]] = []
        self.selected_idx = 0
        self.message_scroll = 0
        self.active_pane = "sessions"  # "sessions" or "messages"
        self.session_scroll = 0

        # Layout
        self.left_width = 30

        # References set externally
        self.app = None
        self.conversation_manager = None

        # Result for resume
        self.selected_session: Optional[Dict[str, Any]] = None
        self.should_resume = False

    def set_app(self, app):
        """Directly set app reference."""
        self.app = app
        if hasattr(app, "llm_service") and app.llm_service:
            self.conversation_manager = app.llm_service.conversation_manager

    async def on_enter(self, renderer: Any) -> None:
        """Called when the view takes foreground control."""
        self._renderer = renderer
        logger.info("ConversationsAltView entered")

        # Reset state
        self.selected_idx = 0
        self.message_scroll = 0
        self.session_scroll = 0
        self.active_pane = "sessions"
        self.selected_session = None
        self.should_resume = False

        # Load sessions
        self._load_sessions()

        # Load messages for first session
        if self.sessions:
            self._load_messages()

    async def on_suspend(self) -> None:
        """Called when the view is being moved to the background."""
        await super().on_suspend()
        logger.info("ConversationsAltView suspended")

    # -- data loading --

    def _load_sessions(self):
        """Load available sessions from conversation manager."""
        if self.conversation_manager:
            all_sessions = self.conversation_manager.get_available_sessions()
            # Filter out sessions with only 1 message (not useful)
            self.sessions = [
                s for s in all_sessions if (s.get("message_count") or 0) > 1
            ]
            logger.info(
                "Loaded %d sessions (filtered from %d)",
                len(self.sessions),
                len(all_sessions),
            )
        else:
            self.sessions = []
            logger.warning("No conversation manager available")

    def _load_messages(self):
        """Load messages for currently selected session."""
        if not self.sessions or not self.conversation_manager:
            self.messages = []
            return

        if self.selected_idx >= len(self.sessions):
            self.messages = []
            return

        session = self.sessions[self.selected_idx]
        session_id = session.get("session_id", "")

        self.messages = self.conversation_manager.get_session_messages(session_id)
        self.message_scroll = 0
        logger.debug(
            "Loaded %d messages for session %s", len(self.messages), session_id
        )

    # -- rendering --

    async def render_frame(self, delta_time: float) -> bool:
        """Render the conversations browser UI."""
        if not self.renderer:
            return False

        width, height = self.renderer.get_terminal_size()
        theme = T()

        # Calculate layout
        self.left_width = min(35, max(25, width // 3))
        right_width = width - self.left_width - 1  # -1 for separator

        # Header row
        self._render_headers(width, right_width, theme)

        # Content area (below headers, above footer)
        content_height = height - 4  # header (2) + footer (2)

        # Render panes
        self._render_sessions_pane(content_height, theme)
        self._render_messages_pane(content_height, right_width, theme)

        # Separator line
        for row in range(2, 2 + content_height):
            self.renderer.write_at(self.left_width, row, C["line_v"], "")

        # Footer
        self._render_footer(width, height, theme)

        return True

    def _render_headers(self, width: int, right_width: int, theme):
        """Render header row for both panes."""
        assert self.renderer is not None  # guarded by render_frame
        session_count = len(self.sessions)
        left_title = f" Sessions {session_count} "

        if self.active_pane == "sessions":
            left_header = solid(
                left_title.ljust(self.left_width),
                theme.primary[0],
                theme.text_dark,
                self.left_width,
            )
        else:
            left_header = solid(
                left_title.ljust(self.left_width),
                theme.dark[0],
                theme.text_dim,
                self.left_width,
            )

        # Right header: current session info
        if self.sessions and self.selected_idx < len(self.sessions):
            session = self.sessions[self.selected_idx]
            session_name = self._get_display_name(session.get("session_id") or "")
            branch = (session.get("git_branch") or "")[:12]
            duration = session.get("duration") or ""
            right_title = f" {session_name}"
            if branch:
                right_title += f" | {branch}"
            if duration:
                right_title += f" | {duration}"
        else:
            right_title = " No session selected"

        if self.active_pane == "messages":
            right_header = solid(
                right_title.ljust(right_width + 1),
                theme.primary[0],
                theme.text_dark,
                right_width + 1,
            )
        else:
            right_header = solid(
                right_title.ljust(right_width + 1),
                theme.dark[0],
                theme.text_dim,
                right_width + 1,
            )

        # Top edge
        self.renderer.write_at(
            0,
            0,
            solid_fg(
                str(C["half_bottom"]) * self.left_width,
                theme.primary[0] if self.active_pane == "sessions" else theme.dark[0],
            ),
            "",
        )
        self.renderer.write_at(
            self.left_width + 1,
            0,
            solid_fg(
                str(C["half_bottom"]) * right_width,
                theme.primary[0] if self.active_pane == "messages" else theme.dark[0],
            ),
            "",
        )
        self.renderer.write_at(0, 1, left_header + " " + right_header, "")

    def _render_sessions_pane(self, content_height: int, theme):
        """Render the sessions list pane."""
        assert self.renderer is not None  # guarded by render_frame
        visible_start = self.session_scroll

        for row in range(content_height):
            y = 2 + row
            idx = visible_start + row

            if idx >= len(self.sessions):
                line = " " * self.left_width
                self.renderer.write_at(
                    0,
                    y,
                    solid(line, theme.dark[0], theme.text_dim, self.left_width),
                    "",
                )
                continue

            session = self.sessions[idx]
            is_selected = idx == self.selected_idx

            name = self._get_display_name(session.get("session_id") or "")
            name = name[:18]
            duration = (session.get("duration") or "")[:5]
            msg_count = session.get("message_count") or 0

            prefix = C["arrow_right"] if is_selected else " "
            line = f" {prefix} {name:<16} {msg_count:>3}m {duration:>5}"
            line = line[: self.left_width].ljust(self.left_width)

            if is_selected and self.active_pane == "sessions":
                self.renderer.write_at(
                    0,
                    y,
                    solid(line, theme.primary[0], theme.text_dark, self.left_width),
                    "",
                )
            elif is_selected:
                self.renderer.write_at(
                    0, y, solid(line, theme.dark[1], theme.text, self.left_width), ""
                )
            else:
                self.renderer.write_at(
                    0,
                    y,
                    solid(line, theme.dark[0], theme.text_dim, self.left_width),
                    "",
                )

    def _render_messages_pane(self, content_height: int, pane_width: int, theme):
        """Render the messages preview pane."""
        assert self.renderer is not None  # guarded by render_frame
        start_x = self.left_width + 1

        rendered_lines = self._build_message_lines(pane_width - 2)

        visible_start = self.message_scroll

        for row in range(content_height):
            y = 2 + row
            line_idx = visible_start + row

            if line_idx >= len(rendered_lines):
                line = " " * pane_width
                self.renderer.write_at(
                    start_x,
                    y,
                    solid(line, theme.dark[0], theme.text_dim, pane_width),
                    "",
                )
                continue

            line_data = rendered_lines[line_idx]
            line_type = line_data.get("type", "content")
            content = line_data.get("content", "")

            display = (" " + content)[:pane_width].ljust(pane_width)

            if line_type == "role_user":
                self.renderer.write_at(
                    start_x,
                    y,
                    solid(display, theme.dark[1], theme.user_tag, pane_width),
                    "",
                )
            elif line_type == "role_assistant":
                self.renderer.write_at(
                    start_x,
                    y,
                    solid(display, theme.dark[1], theme.ai_tag, pane_width),
                    "",
                )
            elif line_type == "content":
                self.renderer.write_at(
                    start_x,
                    y,
                    solid(display, theme.dark[0], theme.text, pane_width),
                    "",
                )
            else:
                # spacing and anything else
                self.renderer.write_at(
                    start_x,
                    y,
                    solid(display, theme.dark[0], theme.text_dim, pane_width),
                    "",
                )

    def _build_message_lines(self, width: int) -> List[Dict[str, Any]]:
        """Build list of rendered lines from messages."""
        lines: List[Dict[str, Any]] = []

        for msg in self.messages:
            role = msg.get("role", "unknown")
            # Prefer the untruncated display_content (includes tool calls, #25);
            # fall back to the older capped fields.
            content = (
                msg.get("display_content")
                or msg.get("full_content", "")
                or msg.get("preview", "")
            )

            # Role header
            role_display = f"[{role}]"
            lines.append({"type": f"role_{role}", "content": role_display})

            # Content lines (wrapped)
            content_lines = wrap_text(content, width - 3)
            for cl in content_lines:
                lines.append({"type": "content", "content": "  " + cl})

            # Spacing
            lines.append({"type": "spacing", "content": ""})

        return lines

    def _render_footer(self, width: int, height: int, theme):
        """Render footer with keybind hints."""
        assert self.renderer is not None  # guarded by render_frame
        footer_y = height - 2

        hints = " Tab: switch | Up/Down: navigate | Enter: resume | q/Esc: quit"
        if self.active_pane == "messages":
            hints = (
                " Tab: switch | Left/Right: page | Up/Down: scroll"
                " | Enter: resume | q/Esc: quit"
            )

        self.renderer.write_at(
            0, footer_y, solid_fg(str(C["half_bottom"]) * width, theme.dark[1]), ""
        )
        footer_line = hints[:width].ljust(width)
        self.renderer.write_at(
            0,
            footer_y + 1,
            solid(footer_line, theme.dark[1], theme.text_dim, width),
            "",
        )

    # -- helpers --

    def _get_display_name(self, session_id: str) -> str:
        """Extract display name from session ID."""
        if "-" in session_id:
            parts = session_id.split("-", 1)
            if len(parts) > 1:
                return parts[1]
            return session_id
        elif session_id.startswith("session_"):
            return session_id[8:]
        return session_id

    def _adjust_session_scroll(self):
        """Adjust session scroll to keep selected item visible."""
        if not self.renderer:
            return

        width, height = self.renderer.get_terminal_size()
        visible_height = height - 4  # header + footer

        if self.selected_idx < self.session_scroll:
            self.session_scroll = self.selected_idx

        if self.selected_idx >= self.session_scroll + visible_height:
            self.session_scroll = self.selected_idx - visible_height + 1

    # -- input handling --

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input. Returns True to exit."""
        # Exit on q or Escape
        if key_press.name == "Escape" or key_press.char in ["q", "\x1b"]:
            return True

        # Tab switches panes
        if key_press.name == "Tab" or key_press.char == "\t":
            if self.active_pane == "sessions":
                self.active_pane = "messages"
            else:
                self.active_pane = "sessions"
            return False

        # Enter resumes selected session
        if key_press.name == "Enter" or key_press.char in ["\r", "\n"]:
            if self.sessions and self.selected_idx < len(self.sessions):
                self.selected_session = self.sessions[self.selected_idx]
                self.should_resume = True
                logger.info(
                    "Selected session for resume: %s",
                    self.selected_session.get("session_id"),
                )
                return True
            return False

        # Navigation
        if key_press.name == "ArrowUp":
            if self.active_pane == "sessions":
                if self.selected_idx > 0:
                    self.selected_idx -= 1
                    self._adjust_session_scroll()
                    self._load_messages()
            else:
                self.message_scroll = max(0, self.message_scroll - 1)
            return False

        if key_press.name == "ArrowDown":
            if self.active_pane == "sessions":
                if self.selected_idx < len(self.sessions) - 1:
                    self.selected_idx += 1
                    self._adjust_session_scroll()
                    self._load_messages()
            else:
                self.message_scroll += 1
            return False

        # Left/Right arrows for paging in messages pane
        if key_press.name == "ArrowLeft":
            if self.active_pane == "messages":
                self.message_scroll = max(0, self.message_scroll - 10)
            return False

        if key_press.name == "ArrowRight":
            if self.active_pane == "messages":
                self.message_scroll += 10
            return False

        # Page navigation
        if key_press.name == "PageUp":
            if self.active_pane == "messages":
                self.message_scroll = max(0, self.message_scroll - 10)
            return False

        if key_press.name == "PageDown":
            if self.active_pane == "messages":
                self.message_scroll += 10
            return False

        return False

    # -- resume API --

    def get_resume_session(self) -> Optional[Dict[str, Any]]:
        """Get the session selected for resume, if any."""
        if self.should_resume:
            return self.selected_session
        return None
