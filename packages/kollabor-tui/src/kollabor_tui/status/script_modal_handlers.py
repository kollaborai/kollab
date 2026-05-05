"""Script modal handlers for interactive status widgets.

This module provides handlers for script-based widgets that return JSON
modal definitions. It enables widgets defined as shell scripts to provide
interactive modals with dynamic options.

Script Modal Widget Pattern:
    Main widget script (git-branch.sh):
        # Displays current status in status area
        echo "⎇ main"

    Modal handler script (git-branch-modal.sh):
        # Returns JSON modal definition on activation
        cat <<EOF
        {
          "title": "Git Branch Actions",
          "options": [
            {
              "label": "View Status",
              "command": ["git", "status"],
              "display": "terminal"
            },
            {
              "label": "Switch Branch",
              "action": ["git", "checkout", "{input}"],
              "input": "branch_name"
            }
          ]
        }
        EOF

JSON Modal Definition Format:
    {
        "title": str,           # Modal title
        "options": [            # Array of modal options
            {
                "label": str,       # Display text
                "command": list,    # Optional: Command to execute
                "action": list,     # Optional: Action command
                "display": str,     # Optional: "terminal" or "modal"
                "input": str,       # Optional: Input field name
                "confirm": bool,    # Optional: Show confirmation
            }
        ]
    }

Reference: docs/specs/interactive-status-widgets-spec.md (lines 519-598)
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# SCRIPT EXECUTION
# =============================================================================


async def execute_script_modal(
    script_path: str,
    timeout: int = 3000,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Execute a script that returns JSON modal definition.

    Runs the specified script and parses its stdout as JSON to produce
    a modal configuration dictionary.

    Args:
        script_path: Path to the script to execute (relative or absolute)
        timeout: Execution timeout in milliseconds (default: 3000ms = 3s)
        env: Optional environment variables for script execution

    Returns:
        Modal configuration dict with title and options array

    Raises:
        ScriptModalError: If script execution fails or JSON is invalid

    Example:
        >>> config = await execute_script_modal("./git-branch-modal.sh")
        >>> print(config["title"])
        'Git Branch Actions'
    """
    logger.info(f"Executing script modal: {script_path}")

    # Resolve script path
    script = Path(script_path)
    if not script.is_absolute():
        # Assume relative to current directory
        script = Path.cwd() / script

    if not script.exists():
        raise ScriptModalError(f"Script not found: {script_path}")

    # Convert timeout to seconds
    timeout_seconds = timeout / 1000.0

    try:
        # Run script in subprocess with timeout
        process = await asyncio.create_subprocess_exec(
            str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Wait for completion with timeout
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ScriptModalError(
                f"Script execution timed out after {timeout}ms: {script_path}"
            )

        # Check exit code
        if process.returncode != 0:
            error_output = stderr.decode("utf-8", errors="replace").strip()
            raise ScriptModalError(
                f"Script failed with exit code {process.returncode}: {error_output}"
            )

        # Parse JSON output
        json_output = stdout.decode("utf-8", errors="replace").strip()
        logger.debug(f"Script output: {json_output[:200]}...")

        modal_config = parse_modal_json(json_output, script_path)

        logger.info(
            f"Script modal executed successfully: "
            f"{len(modal_config.get('options', []))} options"
        )
        return modal_config

    except PermissionError:
        raise ScriptModalError(
            f"Script not executable: {script_path}. " f"Run: chmod +x {script_path}"
        )
    except json.JSONDecodeError as e:
        raise ScriptModalError(f"Invalid JSON output from script {script_path}: {e}")
    except Exception as e:
        raise ScriptModalError(f"Error executing script {script_path}: {e}")


def parse_modal_json(json_string: str, source: str = "<unknown>") -> Dict[str, Any]:
    """Parse JSON output from script modal handler.

    Validates and normalizes the JSON modal definition returned by a script.

    Args:
        json_string: Raw JSON string from script stdout
        source: Source identifier for error messages (e.g., script path)

    Returns:
        Validated modal configuration dict

    Raises:
        ScriptModalError: If JSON is invalid or missing required fields

    Example:
        >>> config = parse_modal_json('{"title": "Test", "options": []}')
        >>> config["title"]
        'Test'
    """
    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as e:
        raise ScriptModalError(
            f"Failed to parse JSON from {source}: {e}\n"
            f"JSON content: {json_string[:200]}..."
        )

    # Validate required fields
    if "title" not in data:
        raise ScriptModalError(
            f"Missing required field 'title' in modal definition from {source}"
        )

    if "options" not in data:
        raise ScriptModalError(
            f"Missing required field 'options' in modal definition from {source}"
        )

    if not isinstance(data["options"], list):
        raise ScriptModalError(
            f"Field 'options' must be an array in modal definition from {source}"
        )

    # Validate each option
    for i, option in enumerate(data["options"]):
        if not isinstance(option, dict):
            raise ScriptModalError(
                f"Option {i} must be an object in modal definition from {source}"
            )

        if "label" not in option:
            raise ScriptModalError(
                f"Option {i} missing required field 'label' in modal definition from {source}"
            )

        # Validate that option has either command or action
        if "command" not in option and "action" not in option:
            raise ScriptModalError(
                f"Option {i} ('{option.get('label', '')}') must have 'command' or 'action' "
                f"in modal definition from {source}"
            )

    # Normalize data (ensure title is string, options is list, etc.)
    normalized = {
        "title": str(data["title"]),
        "options": list(data["options"]),
    }

    # Add optional fields
    if "footer" in data:
        normalized["footer"] = str(data["footer"])

    logger.debug(
        f"Parsed modal JSON: {normalized['title']}, {len(normalized['options'])} options"
    )
    return normalized


# =============================================================================
# ACTION ROUTING
# =============================================================================


async def route_modal_action(
    option: Dict[str, Any],
    context: Any,
) -> Dict[str, Any]:
    """Route modal option action to appropriate handler.

    Executes the command or action defined in a modal option, handling
    input field substitution, confirmation prompts, and display types.

    Args:
        option: Modal option dict with command/action configuration
        context: Widget context with app services

    Returns:
        Result dict with success status and output

    Example:
        >>> option = {"command": ["git", "status"], "display": "terminal"}
        >>> result = await route_modal_action(option, context)
        >>> result["success"]
        True
    """
    logger.info(f"Routing modal action: {option.get('label', 'unknown')}")

    result: Dict[str, Any] = {
        "success": False,
        "output": None,
        "error": None,
    }

    try:
        # Check for confirmation prompt
        if option.get("confirm", False):
            # TODO: Show confirmation dialog
            # For now, log and continue
            logger.info(f"Confirmation required for: {option.get('label')}")

        # Execute command or action
        if "command" in option:
            # Execute command and display output
            command = option["command"]
            display_type = option.get("display", "terminal")

            # Substitute input placeholders
            if "input" in option and context:
                # Get input value from context
                input_value = getattr(context, "input_value", None)
                if input_value:
                    command = [
                        str(part).replace("{input}", input_value) for part in command
                    ]

            logger.debug(f"Executing command: {command}")

            # Run command
            process_result = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process_result.communicate()

            if process_result.returncode == 0:
                output = stdout.decode("utf-8", errors="replace")
                result["success"] = True
                result["output"] = output

                # Display based on type
                if display_type == "terminal":
                    # Output to terminal (e.g., in message area)
                    logger.debug(f"Command output: {output[:100]}...")
                    # TODO: Send to message display
                else:
                    # Display in modal
                    result["display"] = output
            else:
                error = stderr.decode("utf-8", errors="replace")
                result["error"] = error
                logger.error(f"Command failed: {error}")

        elif "action" in option:
            # Execute action
            action = option["action"]

            # Substitute input placeholders
            if "input" in option and context:
                input_value = getattr(context, "input_value", None)
                if input_value:
                    action = [
                        str(part).replace("{input}", input_value) for part in action
                    ]

            logger.debug(f"Executing action: {action}")

            # Run action
            process_result = await asyncio.create_subprocess_exec(
                *action,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process_result.communicate()

            if process_result.returncode == 0:
                output = stdout.decode("utf-8", errors="replace")
                result["success"] = True
                result["output"] = output
            else:
                error = stderr.decode("utf-8", errors="replace")
                result["error"] = error
                logger.error(f"Action failed: {error}")

        else:
            result["error"] = "Option has no command or action"
            logger.warning(f"Option has no command or action: {option.get('label')}")

    except FileNotFoundError as e:
        result["error"] = f"Command not found: {e}"
        logger.error(f"Command not found: {e}")
    except PermissionError as e:
        result["error"] = f"Permission denied: {e}"
        logger.error(f"Permission denied: {e}")
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error executing action: {e}")

    return result


# =============================================================================
# SCRIPT WIDGET ACTIVATION HANDLER
# =============================================================================


async def activate_script_widget(
    widget_id: str,
    context: Any,
    script_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Activation handler for script-based modal widgets.

    This is a wrapper that executes a script modal handler and returns
    its configuration. It can be registered as the on_activate handler
    for script-based widgets.

    Args:
        widget_id: Widget identifier
        context: Widget context object
        script_path: Optional path to modal handler script (if not provided,
                    uses widget's on_activate path from context)

    Returns:
        Modal configuration dict from script execution

    Example:
        >>> config = await activate_script_widget(
        ...     "git-branch",
        ...     context,
        ...     "./git-branch-modal.sh"
        ... )
    """
    logger.info(f"Activating script widget: {widget_id}")

    # Get script path from context if not provided
    if not script_path and context:
        script_path = getattr(context, "on_activate_path", None)

    if not script_path:
        raise ScriptModalError(f"No script path provided for widget {widget_id}")

    # Execute script modal
    timeout = getattr(context, "timeout", 3000) if context else 3000
    return await execute_script_modal(script_path, timeout=timeout)


# =============================================================================
# ERROR HANDLING
# =============================================================================


class ScriptModalError(Exception):
    """Exception raised when script modal execution fails."""

    def __init__(self, message: str):
        """Initialize error with message.

        Args:
            message: Error message describing what went wrong
        """
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        """Return error message."""
        return self.message


# =============================================================================
# VALIDATION UTILITIES
# =============================================================================


def validate_modal_config(config: Dict[str, Any]) -> List[str]:
    """Validate modal configuration and return list of errors.

    Args:
        config: Modal configuration dict to validate

    Returns:
        List of error messages (empty if valid)

    Example:
        >>> errors = validate_modal_config({"title": "Test", "options": []})
        >>> errors
        []
    """
    errors = []

    # Check required fields
    if "title" not in config:
        errors.append("Missing required field: 'title'")

    if "options" not in config:
        errors.append("Missing required field: 'options'")
    elif not isinstance(config["options"], list):
        errors.append("Field 'options' must be an array")
    else:
        # Validate each option
        for i, option in enumerate(config["options"]):
            if not isinstance(option, dict):
                errors.append(f"Option {i} must be an object")
                continue

            if "label" not in option:
                errors.append(f"Option {i} missing required field: 'label'")

            if "command" not in option and "action" not in option:
                errors.append(
                    f"Option {i} ('{option.get('label', '')}') must have 'command' or 'action'"
                )

    return errors


def normalize_modal_option(option: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a modal option by filling in defaults.

    Args:
        option: Raw option dict from script

    Returns:
        Normalized option dict with defaults filled in

    Example:
        >>> normalize_modal_option({"label": "Test", "command": ["echo", "hi"]})
        {'label': 'Test', 'command': ['echo', 'hi'], 'display': 'terminal', 'confirm': False}
    """
    normalized = {
        "label": option.get("label", "Unnamed"),
        "description": option.get("description", ""),
    }

    # Add command or action
    if "command" in option:
        normalized["command"] = option["command"]
    if "action" in option:
        normalized["action"] = option["action"]

    # Add optional fields with defaults
    normalized["display"] = option.get("display", "terminal")
    normalized["confirm"] = option.get("confirm", False)

    # Add input field config if present
    if "input" in option:
        normalized["input"] = option["input"]
        normalized["input_label"] = option.get("input_label", "Enter value:")
        normalized["placeholder"] = option.get("placeholder", "")

    # Add confirmation message if present
    if "message" in option:
        normalized["message"] = option["message"]

    return normalized
