"""Terminal AltView -- live session viewer with input passthrough.

Reads from the session's RingBuffer (subprocess-based, no tmux dependency).
Uses the AltView stack system, which properly coordinates with
the message coordinator (pauses hub messages during viewing,
replays them on exit).

Features:
    - Live ring buffer output at 2 FPS
    - Full keyboard passthrough to active session stdin
    - Session cycling with Alt+Left/Right
    - Kill session with Alt+x
    - ANSI color preservation from subprocess output
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import T, solid
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class TerminalAltView(AltView):
    """Live terminal session viewer with keyboard passthrough.

    Renders ring buffer output and forwards keyboard input to
    the active session's stdin.  Supports cycling through sessions
    and killing them.
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="terminal-view",
            description="Live terminal session viewer",
            version="2.0.0",
            author="Kollabor",
            category="internal",
            icon="[T]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
            background_compatible=True,
        )
        super().__init__(metadata)

        self.target_fps = 2.0

        self._current_session: Optional[str] = None
        self._initial_session: Optional[str] = None
        self._cached_lines: List[str] = []
        self._last_capture: float = 0.0
        # Dict[str, TerminalSession] -- reference to TmuxPlugin.sessions
        self._sessions_source: Optional[Dict[str, Any]] = None

    def set_session(self, session_name: str) -> None:
        """Set the initial session to view."""
        self._initial_session = session_name
        self._current_session = session_name

    def set_sessions_source(self, sessions: Dict[str, Any]) -> None:
        """Pass a reference to the plugin's sessions dict.

        This replaces the old set_tmux_config -- the altview reads
        ring buffers directly from the session objects.
        """
        self._sessions_source = sessions

    # Keep old method signature so nothing breaks if called
    def set_terminal_config(self, tmux_socket: Optional[str], config: Any) -> None:
        """Deprecated.  Use set_sessions_source() instead."""
        pass

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        if self._initial_session and not self._current_session:
            self._current_session = self._initial_session
        self._cached_lines = self._capture_output()
        self._last_capture = time.monotonic()

    async def render_frame(self, delta_time: float) -> bool:
        if not self.renderer:
            return False

        now = time.monotonic()
        if now - self._last_capture > 0.4:
            self._cached_lines = self._capture_output()
            self._last_capture = now

        width, height = self.renderer.get_terminal_size()
        theme = T()

        self.renderer.clear_screen()

        # Header bar
        session_names = self._get_session_names()
        session_idx = (
            session_names.index(self._current_session) + 1
            if self._current_session and self._current_session in session_names
            else 0
        )
        total = len(session_names)
        header = f" {self._current_session or 'none'} ({session_idx}/{total}) "
        header_line = header.center(width)
        self.renderer.write_at(
            0,
            0,
            solid(header_line, theme.primary[0], theme.text_dark, width),
            "",
        )

        # Content area (lines 1 to height-2)
        content_height = height - 2
        lines = self._cached_lines

        if len(lines) > content_height:
            lines = lines[-content_height:]

        for i in range(content_height):
            row = i + 1
            if i < len(lines):
                line = lines[i]
                visible_len = len(self._strip_ansi(line))
                if visible_len < width:
                    line = line + " " * (width - visible_len)
                self.renderer.write_raw(f"\033[{row + 1};1H{line}")
            else:
                self.renderer.write_raw(f"\033[{row + 1};1H{' ' * width}")

        # Footer bar
        footer = " Esc: exit | Alt+Left/Right: cycle | Alt+x: kill "
        footer_line = footer.center(width)
        self.renderer.write_at(
            0,
            height - 1,
            solid(footer_line, theme.dark[1], theme.text_dim, width),
            "",
        )

        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "Escape":
            return True

        # Alt+x kills current session
        if key_press.code == 8776 and self._current_session:
            session = self._get_session(self._current_session)
            if session:
                try:
                    if session.proc and session.proc.poll() is None:
                        if session.proc.stdin:
                            try:
                                session.proc.stdin.close()
                            except Exception:
                                pass
                        session.proc.terminate()
                        try:
                            session.proc.wait(timeout=3)
                        except Exception:
                            session.proc.kill()
                    # Remove from source dict
                    if (
                        self._sessions_source
                        and self._current_session in self._sessions_source
                    ):
                        del self._sessions_source[self._current_session]
                except Exception as e:
                    logger.error(f"Failed to kill session {self._current_session}: {e}")
            return True

        # Alt+Right / Alt+f: next session
        if key_press.name in ("Alt+ArrowRight", "Alt+f"):
            self._cycle_session(forward=True)
            return False

        # Alt+Left / Alt+b: previous session
        if key_press.name in ("Alt+ArrowLeft", "Alt+b"):
            self._cycle_session(forward=False)
            return False

        # Forward to stdin if we have an active session
        if not self._current_session:
            return False

        session = self._get_session(self._current_session)
        if not session or not session.proc or not session.proc.stdin:
            return False
        if not session.is_alive():
            return False

        # Ctrl+C -> send interrupt byte
        if key_press.char and ord(key_press.char) == 3:
            self._write_stdin(session, "\x03")
            return False

        # Special key mapping to escape sequences
        key_map = {
            "Enter": "\n",
            "Backspace": "\x7f",
            "Tab": "\t",
            "ArrowUp": "\033[A",
            "ArrowDown": "\033[B",
            "ArrowRight": "\033[C",
            "ArrowLeft": "\033[D",
            "Home": "\033[H",
            "End": "\033[F",
            "Delete": "\033[3~",
        }

        if key_press.name in key_map:
            self._write_stdin(session, key_map[key_press.name])
        elif key_press.char:
            self._write_stdin(session, key_press.char)

        return False

    async def on_complete(self) -> None:
        self._cached_lines = []
        self._current_session = None

    # -- helpers --

    def _get_session(self, name: str) -> Any:
        """Get a session object from the source dict."""
        if self._sessions_source is None:
            return None
        return self._sessions_source.get(name)

    def _get_session_names(self) -> List[str]:
        """Get list of alive session names from the source dict."""
        if self._sessions_source is None:
            return []
        return [n for n, s in self._sessions_source.items() if s.is_alive()]

    def _write_stdin(self, session: Any, data: str) -> None:
        """Write data to session's stdin."""
        try:
            session.proc.stdin.write(data.encode("utf-8"))
            session.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _capture_output(self) -> List[str]:
        """Read from ring buffer of current session."""
        if not self._current_session:
            return ["(no session selected)"]

        session = self._get_session(self._current_session)
        if session is None:
            return [f"(session '{self._current_session}' not found)"]
        if session.ring_buffer is None:
            return ["(no output buffer)"]

        lines = session.ring_buffer.get_last(500)
        # Strip trailing empty lines
        while lines and not lines[-1].strip():
            lines.pop()
        return lines if lines else ["(no output yet)"]

    def _cycle_session(self, forward: bool = True) -> None:
        sessions = self._get_session_names()
        if not sessions or not self._current_session:
            return
        try:
            idx = sessions.index(self._current_session)
        except ValueError:
            idx = 0
        if forward:
            idx = (idx + 1) % len(sessions)
        else:
            idx = (idx - 1) % len(sessions)
        self._current_session = sessions[idx]
        # Force immediate recapture
        self._cached_lines = self._capture_output()
        self._last_capture = time.monotonic()

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
