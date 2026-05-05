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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


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
        command: str,
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

        if not command.strip():
            return ShellResult(
                success=False, stdout="", stderr="", exit_code=-1, error="Empty command"
            )

        cwd = cwd or Path.cwd()
        self._cancelled = False

        # Filter sensitive environment variables
        if env is None:
            env = self._filter_env(os.environ.copy())

        try:
            # Platform-specific process creation
            if self._is_windows:
                self._current_process = await self._create_windows_process(
                    command, cwd, env
                )
            else:
                self._current_process = await self._create_unix_process(
                    command, cwd, env
                )

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

            return ShellResult(
                success=(self._current_process.returncode == 0),
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=self._current_process.returncode or 1,
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
                        except Exception:
                            pass
                self._current_process = None

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

    async def _kill_process_group(self) -> None:
        """Kill process and all its children (platform-specific)."""
        if self._current_process is None:
            return

        try:
            if self._is_windows:
                await self._kill_windows_process_tree()
            else:
                await self._kill_unix_process_group()
        except (ProcessLookupError, PermissionError, OSError):
            # Process already dead or inaccessible
            pass

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
        except (ProcessLookupError, PermissionError):
            pass

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
        except (ProcessLookupError, PermissionError):
            pass

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
