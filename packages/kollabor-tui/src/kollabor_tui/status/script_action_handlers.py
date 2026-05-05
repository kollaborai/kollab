"""Script action handlers for interactive status widgets.

This module provides secure script execution for action-based widgets,
with support for case/switch routing and JSON result parsing.

Key Features:
- Execute action scripts with action key argument
- Parse JSON action results (success, message, output)
- Display command output safely
- Parameterized subprocess calls (never shell=True with user input)

Security:
- All subprocess calls use parameterized arguments
- User input is passed as positional arguments only
- No shell=True with untrusted input
- Timeouts prevent hanging scripts
"""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ActionStatus(Enum):
    """Status of action script execution."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    UNKNOWN_ACTION = "unknown_action"


@dataclass
class ActionResult:
    """Result from executing an action script.

    Attributes:
        status: ActionStatus indicating execution result
        success: Boolean indicating if action succeeded
        message: Human-readable message from script
        output: Raw command output (stdout)
        error_output: Error output (stderr) if any
        exit_code: Process exit code
        action_key: The action key that was executed
        metadata: Additional metadata from script JSON output
    """

    status: ActionStatus = ActionStatus.SUCCESS
    success: bool = True
    message: str = ""
    output: str = ""
    error_output: str = ""
    exit_code: int = 0
    action_key: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert ActionResult to dictionary.

        Returns:
            Dictionary representation of the result
        """
        return {
            "status": self.status.value,
            "success": self.success,
            "message": self.message,
            "output": self.output,
            "error_output": self.error_output,
            "exit_code": self.exit_code,
            "action_key": self.action_key,
            "metadata": self.metadata,
        }


# =============================================================================
# ACTION SCRIPT EXECUTION
# =============================================================================


async def execute_action_script(
    script_path: Path,
    action_key: str,
    timeout: int = 5,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> ActionResult:
    """Execute an action script with the given action key.

    The script receives the action key as its first argument ($1).
    Scripts should use case/switch routing to handle different actions.

    Args:
        script_path: Path to the action script (must be executable)
        action_key: Action key to pass to script (e.g., "l", "s", "c")
        timeout: Maximum execution time in seconds
        env: Optional environment variables (defaults to os.environ)
        cwd: Optional working directory (defaults to script parent)

    Returns:
        ActionResult with status, message, and output

    Example:
        script = Path("/path/to/docker-actions.sh")
        result = await execute_action_script(script, "l")

        # Script receives: ./docker-actions.sh "l"
        # Script uses: case "$1" in "l") docker ps -a ;; esac
    """
    if not script_path.exists():
        return ActionResult(
            status=ActionStatus.ERROR,
            success=False,
            message=f"Script not found: {script_path}",
            action_key=action_key,
        )

    # Default working directory to script parent
    work_dir = cwd or script_path.parent

    try:
        # Run script with action key as argument
        # SECURITY: Use list argument form, never shell=True with user input
        process = await asyncio.create_subprocess_exec(
            str(script_path),
            action_key,  # Action key passed as positional argument
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
        )

        try:
            # Wait with timeout
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            error_output = stderr.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0

            # Try parsing as JSON first
            if output:
                json_result = parse_action_result(output)
                if json_result:
                    return json_result

            # Non-JSON output: build basic result
            if exit_code == 0:
                return ActionResult(
                    status=ActionStatus.SUCCESS,
                    success=True,
                    message=f"Action '{action_key}' completed",
                    output=output,
                    exit_code=exit_code,
                    action_key=action_key,
                )
            else:
                return ActionResult(
                    status=ActionStatus.ERROR,
                    success=False,
                    message=error_output
                    or f"Action '{action_key}' failed with exit code {exit_code}",
                    output=output,
                    error_output=error_output,
                    exit_code=exit_code,
                    action_key=action_key,
                )

        except asyncio.TimeoutError:
            # Kill process on timeout
            try:
                process.kill()
                await process.wait()
            except Exception as e:
                logger.debug(f"Process cleanup after timeout failed: {e}")

            return ActionResult(
                status=ActionStatus.TIMEOUT,
                success=False,
                message=f"Action '{action_key}' timed out after {timeout}s",
                action_key=action_key,
            )
        finally:
            # Explicitly close transports to prevent Python 3.12
            # asyncio __del__ gc warnings
            for transport in (
                process.stdin,
                process.stdout,
                process.stderr,
            ):
                if transport and hasattr(transport, "close"):
                    try:
                        transport.close()
                    except Exception:
                        pass

    except PermissionError:
        return ActionResult(
            status=ActionStatus.ERROR,
            success=False,
            message=f"Script not executable: {script_path}",
            action_key=action_key,
        )
    except Exception as e:
        logger.error(f"Error executing action script {script_path}: {e}")
        return ActionResult(
            status=ActionStatus.ERROR,
            success=False,
            message=f"Error executing action: {e}",
            action_key=action_key,
        )


def execute_action_script_sync(
    script_path: Path,
    action_key: str,
    timeout: int = 5,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> ActionResult:
    """Synchronous version of execute_action_script.

    Uses subprocess.run instead of asyncio subprocess.

    Args:
        script_path: Path to the action script
        action_key: Action key to pass to script
        timeout: Maximum execution time in seconds
        env: Optional environment variables
        cwd: Optional working directory

    Returns:
        ActionResult with execution results
    """
    import os

    if not script_path.exists():
        return ActionResult(
            status=ActionStatus.ERROR,
            success=False,
            message=f"Script not found: {script_path}",
            action_key=action_key,
        )

    work_dir = cwd or script_path.parent
    script_env = env or os.environ.copy()

    try:
        # SECURITY: Parameterized call, never shell=True
        result = subprocess.run(
            [str(script_path), action_key],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=work_dir,
            env=script_env,
        )

        output = result.stdout.strip()
        error_output = result.stderr.strip()
        exit_code = result.returncode

        # Try parsing JSON output
        if output:
            json_result = parse_action_result(output)
            if json_result:
                return json_result

        # Build basic result from non-JSON output
        if exit_code == 0:
            return ActionResult(
                status=ActionStatus.SUCCESS,
                success=True,
                message=f"Action '{action_key}' completed",
                output=output,
                exit_code=exit_code,
                action_key=action_key,
            )
        else:
            # Check for unknown action pattern
            if (
                "unknown action" in output.lower()
                or "unknown action" in error_output.lower()
            ):
                return ActionResult(
                    status=ActionStatus.UNKNOWN_ACTION,
                    success=False,
                    message=error_output or f"Unknown action: {action_key}",
                    output=output,
                    error_output=error_output,
                    exit_code=exit_code,
                    action_key=action_key,
                )

            return ActionResult(
                status=ActionStatus.ERROR,
                success=False,
                message=error_output or f"Action '{action_key}' failed",
                output=output,
                error_output=error_output,
                exit_code=exit_code,
                action_key=action_key,
            )

    except subprocess.TimeoutExpired:
        return ActionResult(
            status=ActionStatus.TIMEOUT,
            success=False,
            message=f"Action '{action_key}' timed out after {timeout}s",
            action_key=action_key,
        )
    except PermissionError:
        return ActionResult(
            status=ActionStatus.ERROR,
            success=False,
            message=f"Script not executable: {script_path}",
            action_key=action_key,
        )
    except Exception as e:
        logger.error(f"Error executing action script {script_path}: {e}")
        return ActionResult(
            status=ActionStatus.ERROR,
            success=False,
            message=f"Error executing action: {e}",
            action_key=action_key,
        )


# =============================================================================
# JSON RESULT PARSING
# =============================================================================


def parse_action_result(output: str) -> Optional[ActionResult]:
    """Parse JSON output from action script into ActionResult.

    Scripts can output JSON with the following structure:
    {
      "success": true|false,
      "message": "Human-readable message",
      "output": "Command output to display",
      "metadata": { ... }
    }

    Args:
        output: Raw stdout from action script

    Returns:
        ActionResult if JSON is valid, None otherwise

    Example script output:
    ```json
    {
      "success": true,
      "message": "Listed 3 containers",
      "output": "CONTAINER ID   IMAGE     STATUS\\nabc123     nginx     Up 2 hours",
      "metadata": {"count": 3}
    }
    ```
    """
    if not output or not output.strip():
        return None

    try:
        data = json.loads(output)

        # Validate required fields
        if not isinstance(data, dict):
            logger.warning(f"Action script output is not a JSON object: {type(data)}")
            return None

        # Extract fields with defaults
        success = data.get("success", True)
        message = data.get("message", "")
        output_text = data.get("output", "")
        metadata = data.get("metadata", {})

        # Map to ActionStatus
        if success:
            status = ActionStatus.SUCCESS
        else:
            status = ActionStatus.ERROR

        return ActionResult(
            status=status,
            success=success,
            message=message,
            output=output_text,
            exit_code=0 if success else 1,
            metadata=metadata,
        )

    except json.JSONDecodeError:
        # Not JSON, return None to indicate raw output
        return None
    except Exception as e:
        logger.error(f"Error parsing action result JSON: {e}")
        return None


# =============================================================================
# OUTPUT DISPLAY HELPERS
# =============================================================================


def format_action_result(result: ActionResult, verbose: bool = False) -> str:
    """Format ActionResult for display to user.

    Args:
        result: ActionResult to format
        verbose: If True, include full output and metadata

    Returns:
        Formatted string for display
    """
    lines = []

    # Status indicator
    if result.success:
        status_icon = "[OK]"
    else:
        status_icon = "[ERROR]"

    lines.append(f"{status_icon} {result.message}")

    # Show output if present
    if result.output:
        if verbose:
            lines.append("\n--- Output ---")
            lines.append(result.output)
        else:
            # Truncate long output
            max_len = 200
            if len(result.output) > max_len:
                truncated = result.output[:max_len] + "..."
                lines.append(f"\n{truncated}")
            else:
                lines.append(f"\n{result.output}")

    # Show errors
    if result.error_output:
        lines.append(f"\n[Error] {result.error_output}")

    # Show metadata in verbose mode
    if verbose and result.metadata:
        lines.append("\n--- Metadata ---")
        for key, value in result.metadata.items():
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)


def display_command_output(
    renderer, output: str, title: str = "Command Output"
) -> None:
    """Display command output in the terminal.

    This is a helper for showing raw command output from action scripts.
    Uses the terminal renderer to display formatted output.

    Args:
        renderer: Terminal renderer instance
        output: Command output to display
        title: Optional title for the output section

    Note:
        This is a synchronous display function. For async contexts,
        use renderer's async display methods.
    """
    if not output:
        return

    # TODO: Implement proper output display via renderer
    # For now, just log the output
    logger.info(f"[{title}] {output}")

    # Future implementation could use:
    # - renderer.clear_active_area()
    # - Modal overlay for long output
    # - Paging for multi-line output
    pass


# =============================================================================
# ACTION KEY VALIDATION
# =============================================================================


def validate_action_key(action_key: str) -> bool:
    """Validate that an action key is safe to pass to a script.

    Action keys should be simple alphanumeric strings or single characters.
    This prevents injection attacks through action keys.

    Args:
        action_key: Action key to validate

    Returns:
        True if action key is safe, False otherwise

    Security:
        - Only alphanumeric, dash, underscore allowed
        - Max length of 32 characters
        - Prevents shell injection through action keys
    """
    if not action_key:
        return False

    # Check length
    if len(action_key) > 32:
        return False

    # Check character set (alphanumeric, dash, underscore only)
    allowed_pattern = r"^[a-zA-Z0-9_-]+$"
    import re

    if not re.match(allowed_pattern, action_key):
        logger.warning(f"Invalid action key (unsafe characters): {action_key}")
        return False

    return True


# =============================================================================
# SCRIPT ACTION HANDLER CLASS
# =============================================================================


class ScriptActionHandler:
    """Handler for script-based widget actions.

    This class provides a high-level interface for executing
    action scripts and parsing their results.

    Attributes:
        default_timeout: Default execution timeout in seconds
        default_cwd: Default working directory for scripts
    """

    def __init__(
        self,
        default_timeout: int = 5,
        default_cwd: Optional[Path] = None,
    ):
        """Initialize the script action handler.

        Args:
            default_timeout: Default execution timeout
            default_cwd: Default working directory for scripts
        """
        self.default_timeout = default_timeout
        self.default_cwd = default_cwd

    async def execute(
        self,
        script_path: Path,
        action_key: str,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[Path] = None,
    ) -> ActionResult:
        """Execute an action script asynchronously.

        Args:
            script_path: Path to action script
            action_key: Action key to execute
            timeout: Optional timeout override
            env: Optional environment variables
            cwd: Optional working directory

        Returns:
            ActionResult from script execution
        """
        # Validate action key for security
        if not validate_action_key(action_key):
            return ActionResult(
                status=ActionStatus.ERROR,
                success=False,
                message=f"Invalid action key: {action_key}",
                action_key=action_key,
            )

        # Use defaults if not specified
        exec_timeout = timeout or self.default_timeout
        work_dir = cwd or self.default_cwd

        return await execute_action_script(
            script_path=script_path,
            action_key=action_key,
            timeout=exec_timeout,
            env=env,
            cwd=work_dir,
        )

    def execute_sync(
        self,
        script_path: Path,
        action_key: str,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[Path] = None,
    ) -> ActionResult:
        """Execute an action script synchronously.

        Args:
            script_path: Path to action script
            action_key: Action key to execute
            timeout: Optional timeout override
            env: Optional environment variables
            cwd: Optional working directory

        Returns:
            ActionResult from script execution
        """
        # Validate action key for security
        if not validate_action_key(action_key):
            return ActionResult(
                status=ActionStatus.ERROR,
                success=False,
                message=f"Invalid action key: {action_key}",
                action_key=action_key,
            )

        # Use defaults if not specified
        exec_timeout = timeout or self.default_timeout
        work_dir = cwd or self.default_cwd

        return execute_action_script_sync(
            script_path=script_path,
            action_key=action_key,
            timeout=exec_timeout,
            env=env,
            cwd=work_dir,
        )


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "ActionStatus",
    "ActionResult",
    "execute_action_script",
    "execute_action_script_sync",
    "parse_action_result",
    "format_action_result",
    "display_command_output",
    "validate_action_key",
    "ScriptActionHandler",
]
