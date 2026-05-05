"""Shell command service for ! prefix commands.

Handles validation, hook integration, and display coordination.
"""

import asyncio
import logging
import re
from pathlib import Path

from kollabor_events.models import EventType

from .shell_executor import ShellExecutor, ShellResult

logger = logging.getLogger(__name__)


class ShellCommandService:
    """Service for executing user shell commands via ! prefix.

    Each instance creates its own ShellExecutor to avoid state conflicts.
    """

    # Commands that require confirmation
    DANGEROUS_PATTERNS = [
        r"rm\s+(-[rf]+\s+)*[/~]",  # rm -rf /
        r"rm\s+-rf",
        r"mkfs\.",
        r"dd\s+.*of=/dev",
        r">\s*/dev/sd",
        r"chmod\s+-R\s+777",
        r":\(\)\s*{\s*:\|:&\s*}",  # fork bomb
    ]

    # Commands that won't work (need TTY)
    INTERACTIVE_COMMANDS = {
        "vim",
        "vi",
        "nano",
        "emacs",
        "less",
        "more",
        "top",
        "htop",
        "man",
        "ssh",
        "telnet",
        "ftp",
        "python",
        "node",
        "irb",
        "mysql",
    }

    # Commands that need special handling
    CD_COMMAND_PATTERN = r"^\s*cd\s+"

    def __init__(self, event_bus, config, renderer):
        self.event_bus = event_bus
        self.config = config
        self.renderer = renderer

        # Create own executor instance (not shared)
        # Check config for interactive shell mode (sources .zshrc/.bashrc for aliases)
        interactive_shell = (
            config.get("terminal.interactive_shell", False) if config else False
        )
        self.executor = ShellExecutor(interactive=interactive_shell)
        self._executing = False

        # Statistics
        self.stats = {
            "total_commands": 0,
            "successful": 0,
            "failed": 0,
            "cancelled": 0,
            "blocked": 0,
        }

    async def execute(self, command: str) -> None:
        """Execute a shell command from ! prefix input.

        Args:
            command: Full input including ! prefix (e.g., "!ls -la")
        """
        # Strip ! prefix
        shell_cmd = command[1:].strip() if command.startswith("!") else command.strip()

        if not shell_cmd:
            return

        # Check if enabled
        if not self.config.get("shell.enabled", True):
            await self._display_error(shell_cmd, "Shell commands are disabled")
            return

        # Check for cd command (doesn't change app's cwd)
        if re.match(self.CD_COMMAND_PATTERN, shell_cmd):
            await self._display_error(
                shell_cmd,
                "'cd' doesn't change Kollabor's working directory. Use absolute paths instead.",
            )
            return

        # Check for interactive commands
        base_cmd = shell_cmd.split()[0] if shell_cmd.split() else ""
        if base_cmd in self.INTERACTIVE_COMMANDS:
            await self._display_error(
                shell_cmd,
                f"'{base_cmd}' requires an interactive terminal and cannot run here",
            )
            return

        # Check for dangerous commands
        if self._is_dangerous(shell_cmd):
            await self._display_error(
                shell_cmd,
                "Command blocked: matches dangerous pattern. Use terminal directly.",
            )
            self.stats["blocked"] += 1
            return

        # Get config (no display limits - show full output)
        timeout = self.config.get("shell.timeout", 30)
        max_lines = self.config.get(
            "shell.display_lines", 1000000
        )  # Effectively unlimited
        max_chars = self.config.get(
            "shell.display_chars", 10000000
        )  # Effectively unlimited
        show_exit = self.config.get("shell.show_exit_code", True)
        show_cwd = self.config.get("shell.show_cwd", True)

        # Emit PRE event - plugins can cancel
        cwd = Path.cwd()
        pre_data = {
            "command": shell_cmd,
            "cwd": str(cwd),
            "timeout": timeout,
            "can_cancel": True,
        }

        result = await self.event_bus.emit_with_hooks(
            EventType.SHELL_COMMAND_PRE, pre_data, "shell_command_service"
        )

        # Check if plugin cancelled
        if result.get("can_cancel") is False:
            await self._display_error(shell_cmd, "Command blocked by plugin")
            self.stats["blocked"] += 1
            return

        # Show execution indicator
        self._show_executing(shell_cmd)

        self.stats["total_commands"] += 1
        self._executing = True

        try:
            # Execute command
            shell_result = await self.executor.run(shell_cmd, timeout=timeout, cwd=cwd)

            if shell_result.cancelled:
                self.stats["cancelled"] += 1
                await self._emit_cancel_event(shell_cmd)
                self._hide_executing()
                return

            if shell_result.error and not shell_result.timed_out:
                self.stats["failed"] += 1
                await self._emit_error_event(shell_cmd, shell_result)
                await self._display_error(shell_cmd, shell_result.error)
                self._hide_executing()
                return

            if shell_result.timed_out:
                self.stats["failed"] += 1
                await self._emit_error_event(shell_cmd, shell_result)
                await self._display_error(shell_cmd, f"Timed out after {timeout}s")
                self._hide_executing()
                return

            # Success
            if shell_result.success:
                self.stats["successful"] += 1
            else:
                self.stats["failed"] += 1

            # Hide executing indicator
            self._hide_executing()

            # Format output
            output = shell_result.combined_output
            output = self._strip_ansi(output)

            # Build full content for LLM history
            full_content = self._format_full_output(
                shell_cmd, output, shell_result.exit_code, show_exit, show_cwd, cwd
            )

            # Build truncated content for display
            display_content = self._format_display_output(
                shell_cmd,
                output,
                shell_result.exit_code,
                show_exit,
                show_cwd,
                cwd,
                max_lines,
                max_chars,
            )

            # Emit POST event
            await self.event_bus.emit_with_hooks(
                EventType.SHELL_COMMAND_POST,
                {
                    "command": shell_cmd,
                    "result": shell_result.to_dict(),
                    "execution_time": shell_result.execution_time,
                    "total_lines": len(output.split("\n")),
                    "displayed_lines": min(max_lines, len(output.split("\n"))),
                },
                "shell_command_service",
            )

            # Add to conversation history (full output, not displayed)
            await self.event_bus.emit_with_hooks(
                EventType.ADD_MESSAGE,
                {
                    "messages": [{"role": "user", "content": full_content}],
                    "options": {
                        "display_messages": False,
                        "add_to_history": True,
                        "log_messages": True,
                        "trigger_llm": False,
                        "show_loading": False,
                    },
                },
                "shell_command_service",
            )

            # Display truncated output using message_coordinator
            self.renderer.message_coordinator.display_message_sequence(
                [("user", display_content, {})]
            )

        except asyncio.CancelledError:
            self.stats["cancelled"] += 1
            await self.executor.cancel()
            await self._emit_cancel_event(shell_cmd)
            self._hide_executing()
            raise  # Re-raise for proper cleanup
        finally:
            self._executing = False

    async def cancel(self) -> None:
        """Cancel currently running shell command.

        Should be called by external code (e.g., Ctrl+C handler).
        """
        if self._executing:
            await self.executor.cancel()

    def _is_dangerous(self, command: str) -> bool:
        """Check if command matches dangerous patterns."""
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape codes from output.

        Handles CSI, OSC, and other common escape sequences.
        """
        # CSI sequences (most common)
        text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
        # OSC sequences
        text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
        # Character set sequences
        text = re.sub(r"\x1b\([0-9A-Z]", "", text)
        return text

    def _format_full_output(
        self,
        cmd: str,
        output: str,
        exit_code: int,
        show_exit: bool,
        show_cwd: bool,
        cwd: Path,
    ) -> str:
        """Format full output for LLM history."""
        parts = []
        if show_cwd:
            parts.append(f"[{cwd}]")
        parts.append(f"$ {cmd}")
        parts.append(output)
        if show_exit and exit_code != 0:
            parts.append(f"[exit: {exit_code}]")
        return "\n".join(parts)

    def _format_display_output(
        self,
        cmd: str,
        output: str,
        exit_code: int,
        show_exit: bool,
        show_cwd: bool,
        cwd: Path,
        max_lines: int,
        max_chars: int,
    ) -> str:
        """Format truncated output for terminal display."""
        lines = output.split("\n")
        total_lines = len(lines)

        # Truncate by lines
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append(f"... [{total_lines - max_lines} more lines]")

        truncated_output = "\n".join(lines)

        # Truncate by characters (for single long lines)
        if len(truncated_output) > max_chars:
            truncated_output = truncated_output[:max_chars]
            truncated_output += f"\n... [truncated at {max_chars} chars]"

        parts = []
        if show_cwd:
            parts.append(f"[{cwd}]")
        parts.append(f"$ {cmd}")
        parts.append(truncated_output)
        if show_exit and exit_code != 0:
            parts.append(f"[exit: {exit_code}]")
        return "\n".join(parts)

    def _show_executing(self, command: str) -> None:
        """Show execution indicator in status area."""
        # [sapphire-fixed] status_area.update via event_bus.renderer
        if hasattr(self, 'event_bus') and self.event_bus:
            try:
                renderer = self.event_bus.get_service("renderer")
                if renderer and hasattr(renderer, 'status_area'):
                    renderer.status_area.update(
                        "shell-exec", f"exec: {command[:60]}...", priority=50
                    )
            except Exception:
                pass  # graceful fallback
        logger.debug(f"Executing: {command}")

    def _hide_executing(self) -> None:
        """Hide execution indicator."""
        # [sapphire-fixed] status_area.clear
        if hasattr(self, 'event_bus') and self.event_bus:
            try:
                renderer = self.event_bus.get_service("renderer")
                if renderer and hasattr(renderer, 'status_area'):
                    renderer.status_area.clear("shell-exec")
            except Exception:
                pass
        logger.debug("Execution complete")

    async def _display_error(self, command: str, error: str) -> None:
        """Display error message in conversation using message_coordinator."""
        content = f"$ {command}\n[error] {error}"

        # Add to history but don't display via ADD_MESSAGE
        await self.event_bus.emit_with_hooks(
            EventType.ADD_MESSAGE,
            {
                "messages": [{"role": "user", "content": content}],
                "options": {
                    "display_messages": False,
                    "add_to_history": True,
                    "log_messages": True,
                    "trigger_llm": False,
                    "show_loading": False,
                },
            },
            "shell_command_service",
        )

        # Display using message_coordinator (consistent with success path)
        self.renderer.message_coordinator.display_message_sequence(
            [("user", content, {})]
        )

    async def _emit_error_event(self, command: str, result: ShellResult) -> None:
        """Emit error event for plugins."""
        error_type = "timeout" if result.timed_out else "other"
        await self.event_bus.emit_with_hooks(
            EventType.SHELL_COMMAND_ERROR,
            {"command": command, "error": result.error, "error_type": error_type},
            "shell_command_service",
        )

    async def _emit_cancel_event(self, command: str) -> None:
        """Emit cancel event for plugins."""
        await self.event_bus.emit_with_hooks(
            EventType.SHELL_COMMAND_CANCEL,
            {"command": command},
            "shell_command_service",
        )

    def get_stats(self) -> dict:
        """Get execution statistics."""
        return self.stats.copy()  # type: ignore[no-any-return]
