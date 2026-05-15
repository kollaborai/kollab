"""Live TUI proxy client for attaching to a running agent.

.. deprecated::
    This is the LEGACY standalone attach client. It runs as a bare
    terminal viewer outside the main Kollab application (launched from
    cli.py `_handle_cli_attach`). The primary attach path is now the
    in-app proxy mode implemented by
    `TerminalLLMChat._initialize_attach_proxy()` in
    `kollabor/application.py`, which provides richer features:

    - RPC client for RemoteStateService (profile/agent/skill switching)
    - Permission prompt routing via `AttachPermissionBridge`
    - Widget state refresh from daemon
    - Pending launch flag drain (--profile, --agent, --skill, etc.)
    - Daemon death watchdog with graceful shutdown
    - Hub info registration for status bar display
    - Ctrl+Z detach (daemon survives) vs Ctrl+C (daemon dies)

    This file is retained for backward compatibility with direct
    `kollab --attach <identity>` invocations that bypass the full
    application. The in-app proxy covers all features here (event
    stream rendering, input forwarding, detach) plus many more.

    The companion `kollabor/llm/permissions/attach_bridge.py` is NOT
    a replacement for this file -- it handles a different concern
    (routing permission prompts to visible attach clients via DisplayTap).

Connects to an agent's unix socket, subscribes to its display
event stream, and renders the output in the local terminal.
Supports optional interactive mode for input injection.

Usage:
    kollab --attach jarvis              read-only mirror
    kollab --attach jarvis --interactive  bidirectional
"""

import asyncio
import logging
import signal


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


logger = logging.getLogger(__name__)

# Module-level reference for signal handler cleanup
_current_client = None  # type: Optional["AttachClient"]


def _signal_cleanup(signum: int, frame: "Any") -> None:
    """Signal handler that restores terminal state before exit.

    Without this, SIGTERM/SIGHUP during interactive mode leaves the
    terminal in raw mode (no echo, no line buffering). This handler
    forces cleanup so the user gets a usable shell back.
    """
    if _current_client is not None:
        _current_client._exit_raw_mode()
    # Re-raise to default handler so the process actually exits
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


import json
import os
import select
import sys
import termios
import tty
from typing import Any, Dict, List, Optional


class AttachClient:
    """Live terminal mirror for a running agent."""

    def __init__(
        self,
        socket_path: str,
        identity: str,
        interactive: bool = False,
    ):
        self.socket_path = socket_path
        self.identity = identity
        self.interactive = interactive
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._active_lines: List[str] = []
        self._active_line_count: int = 0
        self._streaming: bool = False
        self._old_termios: Optional[list] = None

    async def run(self) -> None:
        """Main attach loop."""
        global _current_client
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self.socket_path
            )
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            print(f"cannot connect to {self.identity}: {e}", file=sys.stderr)
            return

        # Send attach request
        req = (
            json.dumps(
                {
                    "action": "attach",
                    "mode": "interactive" if self.interactive else "readonly",
                    "client_id": f"attach-{os.getpid()}",
                }
            )
            + "\n"
        )
        self._writer.write(req.encode())
        await self._writer.drain()

        # Read ack
        try:
            ack_line = await asyncio.wait_for(self._reader.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            print("attach timeout: no response from agent", file=sys.stderr)
            return

        if not ack_line:
            print("attach failed: connection closed", file=sys.stderr)
            return

        ack = json.loads(ack_line.decode().strip())
        if ack.get("type") == "error":
            print(f"attach failed: {ack.get('msg', 'unknown error')}", file=sys.stderr)
            return
        if ack.get("type") != "attach_ack":
            print(f"unexpected response: {ack}", file=sys.stderr)
            return

        # Print header
        mode_str = "interactive" if self.interactive else "read-only"
        uptime = ack.get("uptime", 0)
        uptime_str = f"{uptime // 60}m{uptime % 60}s" if uptime else "0s"
        detach_key = "Ctrl+D" if self.interactive else "Ctrl+C"

        print(f"\r\n  attached to {self.identity} ({mode_str}) | uptime {uptime_str}")
        print(f"  {detach_key} to detach\r\n")

        # Enter raw mode if interactive
        if self.interactive:
            self._enter_raw_mode()
            # Register signal handlers so raw mode is cleaned up if the
            # terminal dies (SIGHUP) or the process is killed (SIGTERM).
            _current_client = self
            signal.signal(signal.SIGTERM, _signal_cleanup)
            signal.signal(signal.SIGHUP, _signal_cleanup)

        try:
            if self.interactive:
                await asyncio.gather(
                    self._read_events(),
                    self._read_local_input(),
                    return_exceptions=True,
                )
            else:
                await self._read_events()
        except KeyboardInterrupt:
            pass
        finally:
            if self.interactive:
                _current_client = None
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                signal.signal(signal.SIGHUP, signal.SIG_DFL)
                self._exit_raw_mode()
            self._clear_active_area()
            print(f"\r\n  detached from {self.identity}\r\n")
            if self._writer:
                try:
                    detach_msg = json.dumps({"type": "detach"}) + "\n"
                    self._writer.write(detach_msg.encode())
                    await self._writer.drain()
                except Exception as e:
                    logger.debug(f"detach send failed: {e}")
                self._writer.close()

    async def _read_events(self) -> None:
        """Read and render display events from the agent."""
        while True:
            if self._reader is None:
                break
            try:
                line = await self._reader.readline()
            except (ConnectionError, OSError):
                break
            if not line:
                break

            try:
                event = json.loads(line.decode().strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            etype = event.get("type", "")

            if etype in (
                "output",
                "stream_chunk",
                "stream_start",
                "stream_end",
                "clear",
            ):
                self._render_event(event)

            elif etype == "active_area":
                # Skip active_area for now - the raw ANSI cursor sequences
                # from the agent's render loop conflict with the attacher's
                # terminal. Phase 6 will add proper local re-rendering.
                pass

            elif etype == "heartbeat":
                pass

    def _render_event(self, event: Dict[str, Any]) -> None:
        """Render a single display event."""
        etype = event.get("type", "")

        if etype == "output":
            self._clear_active_area()
            rendered = event.get("rendered", "")
            if rendered:
                sys.stdout.write(rendered + "\r\n")
                sys.stdout.flush()

        elif etype == "stream_chunk":
            self._clear_active_area()
            chunk = event.get("chunk", "")
            if chunk:
                # Replace newlines for raw mode
                raw = chunk.replace("\n", "\r\n")
                sys.stdout.write(raw)
                sys.stdout.flush()

        elif etype == "stream_start":
            self._streaming = True

        elif etype == "stream_end":
            self._streaming = False
            sys.stdout.write("\r\n")
            sys.stdout.flush()

        elif etype == "clear":
            pass

    def _clear_active_area(self) -> None:
        """Clear previously rendered active area lines."""
        if self._active_line_count > 0:
            sys.stdout.write(f"\033[{self._active_line_count}A")
            sys.stdout.write("\033[J")
            sys.stdout.flush()
            self._active_line_count = 0

    def _redraw_active_area(self) -> None:
        """Redraw the active area at the bottom of the screen."""
        if not self._active_lines:
            return
        self._clear_active_area()
        for i, line in enumerate(self._active_lines):
            if i > 0:
                sys.stdout.write("\r\n")
            sys.stdout.write(f"\r{line}")
        sys.stdout.flush()
        self._active_line_count = len(self._active_lines)

    async def _read_local_input(self) -> None:
        """Read local keystrokes and forward to agent (interactive mode)."""
        loop = _get_loop()
        while True:
            # Non-blocking stdin read
            readable = await loop.run_in_executor(
                None, lambda: select.select([sys.stdin], [], [], 0.05)[0]
            )
            if not readable:
                continue

            try:
                chunk = os.read(sys.stdin.fileno(), 8192)
            except OSError:
                break

            if not chunk:
                break

            text = chunk.decode("utf-8", errors="ignore")

            # Ctrl+D = detach
            if "\x04" in text:
                break

            # Send to agent
            if self._writer:
                try:
                    msg = json.dumps({"type": "input", "text": text}) + "\n"
                    self._writer.write(msg.encode())
                    await self._writer.drain()
                except (ConnectionError, OSError):
                    break

    def _enter_raw_mode(self) -> None:
        """Enter raw terminal mode for keystroke capture."""
        try:
            self._old_termios = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
        except Exception:
            self._old_termios = None

    def _exit_raw_mode(self) -> None:
        """Restore terminal to normal mode."""
        if self._old_termios is not None:
            try:
                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios
                )
            except Exception:
                pass
            self._old_termios = None
