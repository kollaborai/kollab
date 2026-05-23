"""Terminal session plugin -- subprocess.Popen + ring buffer.

NOTE: Renamed from tmux_plugin.py. Class name kept as TmuxPlugin for
plugin discovery and injection compatibility (application.py checks
__class__.__name__).

Manages terminal sessions without any tmux dependency.  Each session
is a subprocess.Popen with stdout piped into a thread-safe RingBuffer,
following the same pattern as the agent orchestrator.

Provides commands to:
- Create new terminal sessions with commands
- View live session output in alt buffer
- List active sessions
- Kill sessions
"""

import asyncio
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kollabor_tui.visual_effects import AgnosterSegment
from plugins.agent_orchestrator.ring_buffer import RingBuffer

if TYPE_CHECKING:
    from kollabor_events.models import CommandResult

logger = logging.getLogger(__name__)


def _filter_env(env: dict) -> dict:
    """Remove sensitive environment variables before passing to child processes."""
    sensitive_patterns = (
        "API_KEY",
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "CREDENTIAL",
        "AUTH",
        "AWS_",
        "AZURE_",
        "GCP_",
        "ANTHROPIC_",
        "OPENAI_",
    )
    filtered = {}
    for key, value in env.items():
        upper = key.upper()
        if any(pat in upper for pat in sensitive_patterns):
            continue
        filtered[key] = value
    return filtered


@dataclass
class TerminalSession:
    """Represents a managed terminal session backed by subprocess.Popen."""

    name: str
    command: str
    proc: Optional[subprocess.Popen] = None
    ring_buffer: Optional[RingBuffer] = None
    pid: int = 0
    created_at: datetime = field(default_factory=datetime.now)

    def is_alive(self) -> bool:
        """Check if the subprocess is still running."""
        if self.proc is None:
            return False
        return self.proc.poll() is None


class TmuxPlugin:
    """Plugin for terminal session management and live viewing.

    Despite the class name (kept for backward compat / plugin discovery),
    this no longer uses tmux.  All sessions are subprocess.Popen instances
    with output captured into RingBuffer.
    """

    def __init__(self, name: str = "tmux", event_bus=None, renderer=None, config=None):
        self.name = name
        self.version = "2.0.0"
        self.description = "Manage and view terminal sessions"
        self.enabled = True

        self.sessions: Dict[str, TerminalSession] = {}
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self.command_registry: Optional[Any] = None
        self.input_handler = None
        self._current_session: Optional[str] = None

        self.logger = logger

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            "plugins": {
                "tmux": {
                    "enabled": True,
                    "show_status": True,
                    "refresh_rate_ms": 500,
                    "capture_lines": 200,
                }
            },
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self, event_bus, config, **kwargs) -> None:
        try:
            self.event_bus = event_bus
            self.config = config
            self.command_registry = kwargs.get("command_registry")
            self.input_handler = kwargs.get("input_handler")
            self.renderer = kwargs.get("renderer")

            if self.command_registry:
                self._register_commands()

            await self._register_status_widget()

            self.logger.info("Terminal plugin initialized successfully")
        except Exception as e:
            self.logger.error(f"Error initializing terminal plugin: {e}")
            raise

    async def shutdown(self) -> None:
        try:
            # Kill all managed sessions
            for name in list(self.sessions.keys()):
                session = self.sessions[name]
                self._kill_process(session)
            self.sessions.clear()
            self._current_session = None
            self.logger.info("Terminal plugin shutdown completed")
        except Exception as e:
            self.logger.error(f"Error shutting down terminal plugin: {e}")

    async def register_hooks(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Command registration
    # ------------------------------------------------------------------

    def _register_commands(self):
        from kollabor_events.models import (
            CommandCategory,
            CommandDefinition,
            CommandMode,
            SubcommandInfo,
        )

        terminal_cmd = CommandDefinition(
            name="terminal",
            description="Manage terminal sessions (new/view/list/kill)",
            handler=self._handle_tmux_command,
            plugin_name=self.name,
            category=CommandCategory.CUSTOM,
            mode=CommandMode.INSTANT,
            aliases=["term", "tmux", "t"],
            icon="[>_]",
            subcommands=[
                SubcommandInfo("new", "<name> <cmd>", "Create session running command"),
                SubcommandInfo("view", "[name]", "Live view session (default)"),
                SubcommandInfo("list", "", "List all sessions"),
                SubcommandInfo("kill", "<name>", "Kill a session"),
                SubcommandInfo("attach", "<name>", "View session (alias for view)"),
            ],
        )
        self.command_registry.register_command(terminal_cmd)
        self.logger.info("Terminal commands registered")

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _handle_tmux_command(self, command) -> "CommandResult":
        from kollabor_events.models import CommandResult

        args = command.args if command.args else []

        if not args:
            return await self._handle_view_session([])

        subcommand = args[0].lower()

        if subcommand == "new":
            return await self._handle_new_session(args[1:])
        elif subcommand == "view":
            return await self._handle_view_session(args[1:])
        elif subcommand in ("list", "ls"):
            return await self._handle_list_sessions()
        elif subcommand == "kill":
            return await self._handle_kill_session(args[1:])
        elif subcommand == "attach":
            return await self._handle_view_session(args[1:])
        elif subcommand in ("help", "--help", "-h"):
            return CommandResult(
                success=True, message=self._get_help_text(), display_type="info"
            )
        else:
            return CommandResult(
                success=False,
                message=f"Unknown subcommand: {subcommand}\n\n{self._get_help_text()}",
                display_type="error",
            )

    def _get_help_text(self) -> str:
        return """Terminal Session Manager

Usage:
  /terminal new <name> <command>  Create new session running command
  /terminal view [name]           Live view session
  /terminal list                  List all sessions
  /terminal kill <name>           Kill a session

Examples:
  /terminal new myserver python -m http.server 8080
  /terminal new logs tail -f /var/log/syslog
  /terminal view
  /terminal kill myserver

Aliases: /t, /term, /tmux"""

    # ------------------------------------------------------------------
    # Subcommand handlers
    # ------------------------------------------------------------------

    async def _handle_new_session(self, args: List[str]) -> "CommandResult":
        from kollabor_events.models import CommandResult

        if len(args) < 1:
            return CommandResult(
                success=False,
                message="Usage: /terminal new <session_name> [command]",
                display_type="error",
            )

        session_name = args[0]
        command = " ".join(args[1:]) if len(args) > 1 else None

        if session_name in self.sessions and self.sessions[session_name].is_alive():
            return CommandResult(
                success=False,
                message=f"Session '{session_name}' already exists",
                display_type="error",
            )

        try:
            shell_cmd = command if command else os.environ.get("SHELL", "/bin/bash")

            env = _filter_env(os.environ.copy())
            env["KOLLAB_ROOT_SOCKET"] = os.environ.get(
                "KOLLAB_ROOT_SOCKET", Path.cwd().name
            )

            try:
                cmd_parts = shlex.split(shell_cmd)
            except ValueError:
                cmd_parts = ["/bin/sh", "-c", shell_cmd]
            proc = subprocess.Popen(
                cmd_parts,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                cwd=str(Path.cwd()),
                env=env,
            )

            ring_buf = RingBuffer()
            pump = threading.Thread(
                target=self._pump_output,
                args=(proc, ring_buf, session_name),
                daemon=True,
                name=f"pump-{session_name}",
            )
            pump.start()

            self.sessions[session_name] = TerminalSession(
                name=session_name,
                command=command or "shell",
                proc=proc,
                ring_buffer=ring_buf,
                pid=proc.pid,
            )

            msg = (
                "tip: use /hub spawn <identity> type=<agent-type> <task> "
                "for agent management\n\n"
            )
            msg += f"Created session '{session_name}'"
            if command:
                msg += f" running: {command}"
            return CommandResult(success=True, message=msg, display_type="success")

        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Error creating session: {e}",
                display_type="error",
            )

    async def _handle_view_session(self, args: List[str]) -> "CommandResult":
        from kollabor_events.models import CommandResult

        if not args:
            alive = [n for n, s in self.sessions.items() if s.is_alive()]
            if not alive:
                return CommandResult(
                    success=False,
                    message="No sessions found. Use '/terminal new <name> <command>' to create one.",
                    display_type="error",
                )
            session_name = alive[0]
        else:
            session_name = args[0]
            if (
                session_name not in self.sessions
                or not self.sessions[session_name].is_alive()
            ):
                return CommandResult(
                    success=False,
                    message=f"Session '{session_name}' not found or dead",
                    display_type="error",
                )

        self.logger.info("tip: use /hub console for agent management")

        if self.event_bus:
            try:
                from plugins.terminal_altview import TerminalAltView

                stack_mgr = None
                try:
                    stack_mgr = self.event_bus.get_service("altview_stack_manager")
                except Exception:
                    pass

                if not stack_mgr:
                    from kollabor_tui.altview.stack_manager import AltViewStackManager

                    stack_mgr = AltViewStackManager(self.event_bus, self.renderer)
                    self.event_bus.register_service("altview_stack_manager", stack_mgr)

                altview = TerminalAltView()
                altview.set_session(session_name)
                altview.set_sessions_source(self.sessions)

                self._current_session = session_name
                await stack_mgr.push(altview, f"terminal-{session_name}")

                return CommandResult(
                    success=True,
                    message="",
                    display_type="none",
                )

            except Exception as e:
                logger.error(f"Failed to open terminal altview: {e}")
                return CommandResult(
                    success=False,
                    message=f"Terminal view error: {e}",
                    display_type="error",
                )
        else:
            return CommandResult(
                success=False,
                message="Live view not available - event bus not configured",
                display_type="error",
            )

    async def _handle_list_sessions(self) -> "CommandResult":
        from kollabor_events.models import CommandResult

        # Clean up dead sessions
        dead = [n for n, s in self.sessions.items() if not s.is_alive()]
        for n in dead:
            del self.sessions[n]

        if not self.sessions:
            return CommandResult(
                success=True,
                message="No sessions found. Use '/terminal new <name> <command>' to create one.",
                display_type="info",
            )

        lines = ["tip: use /hub console for agent management", "", "Terminal Sessions:"]
        for name, session in self.sessions.items():
            status = "ALIVE" if session.is_alive() else "DEAD"
            lines.append(f"[{status}] {name} (pid={session.pid}) -- {session.command}")

        return CommandResult(
            success=True, message="\n".join(lines), display_type="info"
        )

    async def _handle_kill_session(self, args: List[str]) -> "CommandResult":
        from kollabor_events.models import CommandResult

        if not args:
            return CommandResult(
                success=False,
                message="Usage: /terminal kill <session_name>",
                display_type="error",
            )

        session_name = args[0]

        if session_name not in self.sessions:
            return CommandResult(
                success=False,
                message=f"Session '{session_name}' not found",
                display_type="error",
            )

        session = self.sessions[session_name]
        self._kill_process(session)
        del self.sessions[session_name]

        return CommandResult(
            success=True,
            message=f"tip: use /hub stop <name> for agent management\n\nKilled session '{session_name}'",
            display_type="info",
        )

    # ------------------------------------------------------------------
    # Output capture / input send
    # ------------------------------------------------------------------

    @staticmethod
    def _pump_output(proc: subprocess.Popen, ring_buf: RingBuffer, label: str) -> None:
        """Read proc.stdout line-by-line into ring buffer (daemon thread)."""
        stdout = proc.stdout
        assert stdout is not None  # ensured by caller
        try:
            for raw_line in iter(stdout.readline, b""):
                try:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    line = repr(raw_line)
                ring_buf.append(line)
        except Exception as e:
            logger.debug(f"[pump] {label} pump ended: {e}")
        finally:
            logger.debug(f"[pump] {label} stdout closed")

    def _capture_output(
        self, session_name: Optional[str], max_lines: Optional[int] = None
    ) -> List[str]:
        """Read last N lines from session's ring buffer."""
        if not session_name:
            return []
        session = self.sessions.get(session_name)
        if session is None or session.ring_buffer is None:
            return [f"(session '{session_name}' not found)"]

        n = max_lines or 200
        lines = session.ring_buffer.get_last(n)

        # Strip trailing empty lines
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    def _send_stdin(self, session_name: Optional[str], text: str) -> bool:
        """Write text to session's stdin."""
        if not session_name:
            return False
        session = self.sessions.get(session_name)
        if session is None or session.proc is None or session.proc.stdin is None:
            return False
        if not session.is_alive():
            return False
        try:
            data = (text + "\n").encode("utf-8")
            session.proc.stdin.write(data)
            session.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError) as e:
            self.logger.error(f"Failed to send stdin to {session_name}: {e}")
            return False

    # ------------------------------------------------------------------
    # Process management
    # ------------------------------------------------------------------

    def _kill_process(self, session: TerminalSession) -> bool:
        """Terminate subprocess: SIGTERM -> wait -> SIGKILL."""
        if session.proc is None:
            return False
        if session.proc.poll() is not None:
            return True
        try:
            # Close stdin first so child sees EOF
            if session.proc.stdin:
                try:
                    session.proc.stdin.close()
                except Exception:
                    pass
            session.proc.terminate()
            try:
                session.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.proc.kill()
                session.proc.wait(timeout=2)
            return True
        except Exception as e:
            self.logger.error(f"Failed to kill process for {session.name}: {e}")
            return False

    # ------------------------------------------------------------------
    # Status widget
    # ------------------------------------------------------------------

    async def _register_status_widget(self) -> None:
        try:
            if hasattr(self, "app") and self.app:
                widget_api = self.app.get_widget_api()
                if widget_api:
                    widget_api.register_widget(
                        id="tmux-sessions",
                        name="Terminal Sessions",
                        description="Active terminal session count and names",
                        render_fn=self._render_tmux_widget,
                        default_width="auto",
                        min_width=8,
                    )
                    logger.info("Registered terminal-sessions status widget")
        except Exception as e:
            logger.error(f"Failed to register terminal widget: {e}")

    def _render_tmux_widget(self, width: int, context) -> str:
        from kollabor_tui.design_system import T

        def _fg(text: str, color: tuple) -> str:
            r, g, b = color
            return f"\033[38;2;{r};{g};{b}m{text}\033[39m"

        if not self.enabled:
            return _fg("term: off", T().text_dim)

        alive = [n for n, s in self.sessions.items() if s.is_alive()]
        count = len(alive)
        if count == 0:
            return _fg("0 term", T().text_dim)

        names_text = " ".join(alive[:2])
        if count > 2:
            return (
                _fg(f"{count} term", T().text)
                + " "
                + _fg(names_text, T().text_dim)
                + _fg(f" +{count - 2}", T().text_dim)
            )
        return _fg(f"{count} term", T().text) + " " + _fg(names_text, T().text_dim)

    def _get_tmux_sessions_content(self) -> List[str]:
        try:
            seg = AgnosterSegment()

            if not self.enabled:
                seg.add_neutral("Term: Disabled", "dark")
                return [seg.render()]

            alive = [n for n, s in self.sessions.items() if s.is_alive()]
            count = len(alive)

            if count == 0:
                seg.add_lime("Term", "dark")
                seg.add_neutral("No sessions", "mid")
                return [seg.render()]

            seg.add_lime("Term", "dark")
            seg.add_cyan(f"{count} active", "dark")

            max_show = 3
            names = alive[:max_show]
            names_str = " | ".join(names)
            if count > max_show:
                names_str += f" +{count - max_show}"
            seg.add_lime(names_str)
            seg.add_neutral("/terminal", "mid")

            return [seg.render()]
        except Exception as e:
            logger.error(f"Error getting terminal sessions content: {e}")
            seg = AgnosterSegment()
            seg.add_neutral("Term: Error", "dark")
            return [seg.render()]

    def get_status_line(self) -> Dict[str, List[str]]:
        return {"A": [], "B": [], "C": []}

    # ===== Public API for ToolExecutor Integration =====

    async def execute_foreground(
        self, command: str, timeout: int = 90, cwd: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute command asynchronously, capture output, return result."""
        from kollabor_agent.shell_executor import ShellExecutor

        try:
            effective_cwd = Path(cwd) if cwd and cwd.strip() else Path.cwd()

            env = _filter_env(os.environ.copy())
            env["KOLLAB_ROOT_SOCKET"] = os.environ.get(
                "KOLLAB_ROOT_SOCKET", Path.cwd().name
            )

            executor = ShellExecutor()
            result = await executor.run(
                command, timeout=timeout, cwd=effective_cwd, env=env
            )

            if result.timed_out:
                return {
                    "success": False,
                    "output": result.stdout or "",
                    "error": f"Command timed out after {timeout}s",
                    "exit_code": -1,
                }

            output = result.combined_output if result.stderr else result.stdout
            lines = output.rstrip("\n").split("\n")
            cleaned = self._clean_foreground_output(lines, command)

            return {
                "success": result.success,
                "output": cleaned,
                "error": (
                    ""
                    if result.success
                    else f"Command exited with code {result.exit_code}"
                ),
                "exit_code": result.exit_code,
            }

        except Exception as e:
            self.logger.error(f"Error in execute_foreground: {e}")
            return {"success": False, "output": "", "error": str(e), "exit_code": -1}

    async def execute_background(
        self,
        command: str,
        name: Optional[str] = None,
        timeout: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute command in a persistent background session."""
        if not name:
            name = self._generate_session_name(command)

        if name in self.sessions and self.sessions[name].is_alive():
            return {
                "success": False,
                "session_name": name,
                "message": f"Session '{name}' already exists",
            }

        try:
            effective_cwd = cwd if cwd and cwd.strip() else str(Path.cwd())

            env = _filter_env(os.environ.copy())
            env["KOLLAB_ROOT_SOCKET"] = os.environ.get(
                "KOLLAB_ROOT_SOCKET", Path.cwd().name
            )

            try:
                cmd_parts = shlex.split(command)
            except ValueError:
                cmd_parts = ["/bin/sh", "-c", command]
            proc = subprocess.Popen(
                cmd_parts,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                cwd=effective_cwd,
                env=env,
            )

            ring_buf = RingBuffer()
            pump = threading.Thread(
                target=self._pump_output,
                args=(proc, ring_buf, name),
                daemon=True,
                name=f"pump-bg-{name}",
            )
            pump.start()

            self.sessions[name] = TerminalSession(
                name=name,
                command=command,
                proc=proc,
                ring_buffer=ring_buf,
                pid=proc.pid,
            )

            if timeout:
                timeout_seconds = self._parse_timeout(timeout)
                if timeout_seconds > 0:
                    asyncio.create_task(self._auto_kill_after(name, timeout_seconds))

            return {
                "success": True,
                "session_name": name,
                "message": (
                    f"Background session '{name}' started (running in background). "
                    f"Use /terminal view {name} to check output, "
                    f'or <terminal-output name="{name}" /> to capture recent lines.'
                ),
            }

        except Exception as e:
            self.logger.error(f"Error in execute_background: {e}")
            return {"success": False, "session_name": name, "message": str(e)}

    async def get_session_status(self, name: str) -> Dict[str, Any]:
        if name == "*":
            sessions_info = []
            for session_name, session in self.sessions.items():
                sessions_info.append(
                    {
                        "name": session_name,
                        "command": session.command,
                        "created_at": session.created_at.isoformat(),
                        "status": "alive" if session.is_alive() else "dead",
                    }
                )
            return {"success": True, "sessions": sessions_info}
        else:
            found: Optional[TerminalSession] = self.sessions.get(name)
            if found:
                return {
                    "success": True,
                    "name": name,
                    "command": found.command,
                    "created_at": found.created_at.isoformat(),
                    "status": "alive" if found.is_alive() else "dead",
                }
            return {"success": False, "message": f"Session '{name}' not found"}

    async def capture_session_output(
        self, name: str, lines: int = 50
    ) -> Dict[str, Any]:
        session = self.sessions.get(name)
        if session is None:
            return {
                "success": False,
                "output": [],
                "message": f"Session '{name}' not found",
            }

        output_lines = self._capture_output(name, max_lines=lines)
        return {"success": True, "output": output_lines}

    async def kill_background_session(
        self, name: str, signal: str = "SIGTERM"
    ) -> Dict[str, Any]:
        if name == "*":
            killed = []
            failed = []
            for session_name in list(self.sessions.keys()):
                session = self.sessions[session_name]
                if self._kill_process(session):
                    killed.append(session_name)
                    del self.sessions[session_name]
                else:
                    failed.append(session_name)

            if failed:
                return {
                    "success": False,
                    "message": f"Killed {len(killed)} sessions, failed: {', '.join(failed)}",
                }
            return {"success": True, "message": f"Killed {len(killed)} sessions"}
        else:
            found: Optional[TerminalSession] = self.sessions.get(name)
            if found is None:
                return {"success": False, "message": f"Session '{name}' not found"}

            if self._kill_process(found):
                del self.sessions[name]
                return {"success": True, "message": f"Killed session '{name}'"}
            return {"success": False, "message": f"Failed to kill session '{name}'"}

    # ===== Helper Methods =====

    def _generate_session_name(self, command: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9]", "-", command)[:8]
        timestamp = str(int(time.time()))[-6:]
        return f"{sanitized}-{timestamp}"

    def _parse_timeout(self, timeout_str: str) -> int:
        match = re.match(r"^(\d+)([smh]?)$", timeout_str.lower())
        if not match:
            self.logger.warning(f"Invalid timeout format: {timeout_str}")
            return 0

        value = int(match.group(1))
        unit = match.group(2) or "s"

        if unit == "s":
            return value
        elif unit == "m":
            return value * 60
        elif unit == "h":
            return value * 3600
        return 0

    def _clean_foreground_output(self, lines: List[str], command: str) -> str:
        if not lines:
            return ""

        cleaned = []
        wrapped_cmd = f"{command}; exit $?"

        for line in lines:
            if line.startswith("Pane is dead"):
                continue
            if line.strip() == wrapped_cmd or line.strip() == command:
                continue
            if any(
                indicator in line
                for indicator in ["\u276f", "\u279c", "$ ", "> ", "# "]
            ):
                if wrapped_cmd in line or command in line:
                    continue
            if "\ue0a0" in line or "\ue718" in line or "\U0001f4e6" in line:
                continue
            cleaned.append(line)

        while cleaned and not cleaned[0].strip():
            cleaned.pop(0)
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()

        return "\n".join(cleaned)

    async def _auto_kill_after(self, session_name: str, seconds: int):
        try:
            await asyncio.sleep(seconds)
            session = self.sessions.get(session_name)
            if session and session.is_alive():
                self.logger.info(
                    f"Auto-killing session '{session_name}' after {seconds}s timeout"
                )
                await self.kill_background_session(session_name)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Error in auto-kill task for {session_name}: {e}")

    @staticmethod
    def get_config_widgets() -> Optional[Dict[str, Any]]:
        return {
            "title": "Terminal Settings",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Show Status",
                    "config_path": "plugins.tmux.show_status",
                    "help": "Show terminal session count in status bar",
                },
                {
                    "type": "slider",
                    "label": "Refresh Rate (ms)",
                    "config_path": "plugins.tmux.refresh_rate_ms",
                    "min_value": 50,
                    "max_value": 1000,
                    "step": 50,
                    "help": "Live view refresh rate in milliseconds",
                },
                {
                    "type": "slider",
                    "label": "Capture Lines",
                    "config_path": "plugins.tmux.capture_lines",
                    "min_value": 10,
                    "max_value": 1000,
                    "step": 10,
                    "help": "Number of lines to capture from output buffer",
                },
            ],
        }
