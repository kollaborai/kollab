"""Shared shell command execution utility.

Used by:
- ShellCommandService (for ! user commands)
- ToolExecutor (for LLM <terminal> tool)

Each caller should create its own ShellExecutor instance to avoid
state conflicts during concurrent execution.
"""

import asyncio
import logging
import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Kill switch F11: [pid, start] of every tool shell this agent started, newest last. Presence carries it so
# a halt also stops what a shell backgrounded (`cmd &`, nohup) after the shell itself exited. Listeners
# (the hub's presence publish) run on every record, so the entry is on disk before the command runs far.
MAX_TOOL_SHELLS = 64
TOOL_SHELLS: List[List[float]] = []
TOOL_SHELL_LISTENERS: List[Callable[[], None]] = []
# ps start times are whole seconds and the record is the agent's clock from just before the spawn
START_DRIFT = 2.0


def _group_alive(pid: int) -> bool:
    """The shell or anything left in its process group still runs (a tool shell leads its group)."""
    for check in (os.kill, os.killpg):
        try:
            check(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            continue
    return False


def _etime_seconds(text: str) -> int | None:
    """``ps -o etime`` ([[dd-]hh:]mm:ss) as seconds; None when it is not one."""
    days, _, rest = text.rpartition("-")
    parts = rest.split(":")
    if not (days == "" or days.isdigit()) or not 2 <= len(parts) <= 3 or not all(x.isdigit() for x in parts):
        return None
    seconds = 0
    for x in parts:
        seconds = seconds * 60 + int(x)
    return seconds + int(days or 0) * 86400


def _pid_starts(pids) -> Dict[int, float]:
    """Epoch start of each pid we still hold a row for, in one ``ps`` call (empty when ps is missing).

    Kill switch F15a: the pid-reuse check for the presence rows. Same etime trick as the hub's
    ``halt_runtime.process_info`` (a package must not import from plugins), whole-second precision.
    """
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["/bin/ps", "-o", "pid=,etime=", "-p", ",".join(str(p) for p in sorted(pids))],
            capture_output=True, text=True, timeout=10,
            # minimal env: nothing from the environment (secrets included) rides along
            env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
    except Exception:  # best effort (ps missing, mocked, slow): unseen starts keep their rows
        return {}
    starts: Dict[int, float] = {}
    now = time.time()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        elapsed = _etime_seconds(parts[1])
        if elapsed is not None:
            starts[int(parts[0])] = float(int(now) - elapsed)
    return starts


def prune_tool_shells() -> None:
    """Kill switch F15a: drop rows whose shell and group are gone or whose pid a new process reused.

    Runs on every presence publish and before recording a new shell: an idle agent would otherwise
    carry dead pids all night, and a reused pid that now leads a group (other agents' tty-less jobs)
    would have that group swept by a halt. A pid whose start ``ps`` cannot see is kept — only a
    proven mismatch drops it. Keeps the newest ``MAX_TOOL_SHELLS``.
    """
    starts = _pid_starts({int(e[0]) for e in TOOL_SHELLS})
    kept = []
    for entry in TOOL_SHELLS:
        pid, started = int(entry[0]), float(entry[1])
        if not _group_alive(pid):
            continue
        began = starts.get(pid)
        if began is not None and abs(began - started) > START_DRIFT:
            continue
        kept.append(entry)
    TOOL_SHELLS[:] = kept[-MAX_TOOL_SHELLS:]


def record_tool_shell(pid: int, started: float) -> None:
    """Remember a tool shell; drop entries the prune rejects, keep the newest 64."""
    prune_tool_shells()
    # F12: the third field is the shell's parent at record time (this agent); the halt checks it
    TOOL_SHELLS[:] = (list(TOOL_SHELLS) + [[pid, started, os.getpid()]])[-MAX_TOOL_SHELLS:]
    for listener in list(TOOL_SHELL_LISTENERS):
        try:
            listener()
        except Exception as e:  # presence is best effort; the command still runs
            logger.debug(f"tool shell listener failed: {e}")


@dataclass
class ShellResult:
    """Result of shell command execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    cancelled: bool = False
    error: Optional[str] = None
    execution_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for event payloads."""
        return {
            "success": self.success,
            "stdout_length": len(self.stdout),
            "stderr_length": len(self.stderr),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "error": self.error,
            "execution_time": self.execution_time,
        }

    @property
    def combined_output(self) -> str:
        """Get combined stdout + stderr."""
        output = self.stdout
        if self.stderr:
            output += ("\n" if output else "") + self.stderr
        return output.rstrip() or "(no output)"


class ShellExecutor:
    """Async shell command executor with cancellation support.

    Each instance maintains its own execution state. Create one instance
    per service/component to avoid state conflicts.
    """

    def __init__(self, interactive: bool = False):
        """Initialize shell executor.

        Args:
            interactive: If True, run shell in interactive mode (-i flag)
                        which sources ~/.zshrc or ~/.bashrc for aliases.
                        May cause slowdowns on first run.
        """
        self._current_process: Optional[asyncio.subprocess.Process] = None
        self._cancelled = False
        self._is_windows = platform.system() == "Windows"
        self._interactive = interactive

    async def run(
        self,
        command: str | Sequence[str],
        timeout: int = 30,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        max_output_bytes: int = 10 * 1024 * 1024,  # 10MB default
    ) -> ShellResult:
        """Execute shell command asynchronously.

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            cwd: Working directory (defaults to current)
            env: Environment variables (defaults to filtered os.environ)
            max_output_bytes: Max output size before truncation

        Returns:
            ShellResult with stdout, stderr, exit_code, etc.
        """
        import time

        start_time = time.time()

        argv: tuple[str, ...] | None = None
        if isinstance(command, str):
            if not command.strip():
                return ShellResult(
                    success=False, stdout="", stderr="", exit_code=-1, error="Empty command"
                )
        else:
            argv = tuple(command)
            if not argv or any(not isinstance(argument, str) or not argument for argument in argv):
                return ShellResult(
                    success=False, stdout="", stderr="", exit_code=-1, error="Invalid argv"
                )

        cwd = cwd or Path.cwd()
        self._cancelled = False

        # Filter sensitive environment variables
        if env is None:
            env = self._filter_env(os.environ.copy())
        # Accident guard (network-sync-and-control.md section 1): set on every
        # tool child, after the filter, whether the caller passed env or not.
        env = {**env, "KOLLAB_TOOL_SHELL": "1", "KOLLAB_AGENT_PID": str(os.getpid())}

        try:
            # Voice-originated commands pass argv sequences and never enter a
            # shell. Existing string callers retain their configured shell path.
            if argv is not None:
                if self._is_windows:
                    self._current_process = await self._create_windows_process_argv(
                        argv, cwd, env
                    )
                else:
                    self._current_process = await self._create_unix_process_argv(
                        argv, cwd, env
                    )
            elif self._is_windows:
                self._current_process = await self._create_windows_process(
                    command, cwd, env
                )
            else:
                self._current_process = await self._create_unix_process(
                    command, cwd, env
                )
            pid = getattr(self._current_process, "pid", None)
            if not self._is_windows and isinstance(pid, int):
                record_tool_shell(pid, start_time)

            try:
                stdout, stderr = await asyncio.wait_for(
                    self._current_process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._kill_process_group()
                return ShellResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    timed_out=True,
                    error=f"Command timed out after {timeout}s",
                    execution_time=time.time() - start_time,
                )
            except asyncio.CancelledError:
                # Preserve outer task cancellation semantics while still
                # terminating the subprocess group before transports close.
                # An explicit ``cancel()`` sets _cancelled and is reported as
                # a normal cancelled ShellResult; task.cancel() must propagate
                # so callers (and TaskGroup/timeout scopes) can observe it.
                outer_cancelled = not self._cancelled
                if outer_cancelled:
                    try:
                        await asyncio.shield(self._kill_process_group())
                    except asyncio.CancelledError:
                        # The cleanup itself may observe cancellation; the
                        # original cancellation still takes precedence.
                        pass
                    raise

                await self._kill_process_group()
                return ShellResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    cancelled=True,
                    error="Command cancelled by user",
                    execution_time=time.time() - start_time,
                )

            # Check if cancelled during execution
            if self._cancelled:
                return ShellResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    cancelled=True,
                    error="Command cancelled by user",
                    execution_time=time.time() - start_time,
                )

            # Decode output with size limit
            stdout_text = self._decode_output(stdout, max_output_bytes)
            stderr_text = self._decode_output(stderr, max_output_bytes)

            returncode = self._current_process.returncode
            return ShellResult(
                success=(returncode == 0),
                stdout=stdout_text,
                stderr=stderr_text,
                # `returncode or 1` reported every success as exit 1.
                exit_code=1 if returncode is None else returncode,
                execution_time=time.time() - start_time,
            )

        except Exception as e:
            logger.error(f"Shell execution failed: {e}")
            return ShellResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                error=str(e),
                execution_time=time.time() - start_time,
            )
        finally:
            if self._current_process is not None:
                # Explicitly close transports to prevent Python 3.12
                # asyncio __del__ gc warnings
                for transport in (
                    self._current_process.stdin,
                    self._current_process.stdout,
                    self._current_process.stderr,
                ):
                    if transport and hasattr(transport, "close"):
                        try:
                            transport.close()
                        except Exception as exc:
                            logger.debug(
                                "Could not close %s transport: %s",
                                type(transport).__name__,
                                exc,
                            )
                self._current_process = None

    async def run_argv(
        self,
        argv: Sequence[str],
        timeout: int = 30,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        max_output_bytes: int = 10 * 1024 * 1024,
    ) -> ShellResult:
        """Execute an already parsed argv sequence without invoking a shell."""
        if isinstance(argv, str):
            return ShellResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                error="run_argv requires an argument sequence",
            )
        return await self.run(
            tuple(argv),
            timeout=timeout,
            cwd=cwd,
            env=env,
            max_output_bytes=max_output_bytes,
        )

    async def cancel(self) -> None:
        """Cancel currently running command.

        This should be called by external code that wants to stop execution.
        Sets cancellation flag and kills the process group.
        """
        self._cancelled = True
        await self._kill_process_group()

    async def _create_unix_process(
        self, command: str, cwd: Path, env: Dict[str, str]
    ) -> asyncio.subprocess.Process:
        """Create process with Unix-specific settings using user's shell.

        When interactive=True, sources shell config files for aliases.
        When interactive=False (default), runs faster but no aliases.
        """
        # Get user's shell from environment, fallback to /bin/sh
        user_shell = env.get("SHELL", "/bin/sh")

        # Build shell arguments
        if self._interactive:
            # Interactive mode: -i sources .zshrc/.bashrc for aliases
            # Note: May be slower on first run due to rc file loading
            shell_args = [user_shell, "-i", "-c", command]
        else:
            # Non-interactive: faster but no aliases
            shell_args = [user_shell, "-c", command]

        return await asyncio.create_subprocess_exec(
            *shell_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,  # Create new process group
        )

    async def _create_windows_process(
        self, command: str, cwd: Path, env: Dict[str, str]
    ) -> asyncio.subprocess.Process:
        """Create process with Windows-specific settings."""
        # Windows doesn't use start_new_session the same way
        # We'll use CREATE_NEW_PROCESS_GROUP via creationflags
        import subprocess

        return await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if self._is_windows else 0  # type: ignore[attr-defined]
            ),
        )

    async def _create_unix_process_argv(
        self, argv: Sequence[str], cwd: Path, env: Dict[str, str]
    ) -> asyncio.subprocess.Process:
        """Create a shell-free Unix process from a validated argv sequence."""
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )

    async def _create_windows_process_argv(
        self, argv: Sequence[str], cwd: Path, env: Dict[str, str]
    ) -> asyncio.subprocess.Process:
        """Create a shell-free Windows process from a validated argv sequence."""
        import subprocess

        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    async def _kill_process_group(self) -> None:
        """Kill process and all its children (platform-specific)."""
        if self._current_process is None:
            return

        try:
            if self._is_windows:
                await self._kill_windows_process_tree()
            else:
                await self._kill_unix_process_group()
        except (ProcessLookupError, PermissionError, OSError) as exc:
            # Process already dead or inaccessible; debug keeps kill-path
            # noise down while the failure stays traceable.
            logger.debug("Process group kill skipped: %s", exc)

    async def _kill_unix_process_group(self) -> None:
        """Kill Unix process group using POSIX signals."""
        if self._current_process is None:
            return

        try:
            # Kill entire process group
            pgid = os.getpgid(self._current_process.pid)
            os.killpg(pgid, signal.SIGTERM)

            # Wait briefly for graceful shutdown
            try:
                await asyncio.wait_for(self._current_process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                # Force kill if still running
                os.killpg(pgid, signal.SIGKILL)
                await self._current_process.wait()
        except (ProcessLookupError, PermissionError) as exc:
            # Process already dead or inaccessible; debug keeps kill-path
            # noise down while the failure stays traceable.
            logger.debug("Process kill skipped: %s", exc)

    async def _kill_windows_process_tree(self) -> None:
        """Kill Windows process tree using taskkill."""
        if self._current_process is None:
            return

        try:
            # Try gentle termination first
            self._current_process.terminate()

            # Wait briefly
            try:
                await asyncio.wait_for(self._current_process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                # Force kill with taskkill /F /T
                import subprocess

                try:
                    subprocess.run(
                        [
                            "taskkill",
                            "/F",
                            "/T",
                            "/PID",
                            str(self._current_process.pid),
                        ],
                        capture_output=True,
                        timeout=5,
                    )
                except Exception:
                    # Last resort: kill()
                    self._current_process.kill()
                    await self._current_process.wait()
        except (ProcessLookupError, PermissionError) as exc:
            # Process already dead or inaccessible; debug keeps kill-path
            # noise down while the failure stays traceable.
            logger.debug("Process kill skipped: %s", exc)

    def _filter_env(self, env: Dict[str, str]) -> Dict[str, str]:
        """Remove sensitive environment variables."""
        sensitive_patterns = [
            "API_KEY",
            "SECRET",
            "TOKEN",
            "PASSWORD",
            "CREDENTIAL",
            "AWS_",
            "AZURE_",
            "GCP_",
            "ANTHROPIC_",
            "OPENAI_",
        ]
        return {
            k: v
            for k, v in env.items()
            if not any(pattern in k.upper() for pattern in sensitive_patterns)
        }

    def _decode_output(self, data: bytes, max_bytes: int) -> str:
        """Decode output with size limit and binary detection."""
        if len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True
        else:
            truncated = False

        # Detect binary content
        if b"\x00" in data[:1024]:
            return "[binary output not displayed]"

        text = data.decode("utf-8", errors="replace")

        if truncated:
            text += f"\n[output truncated at {max_bytes // 1024}KB]"

        return text
