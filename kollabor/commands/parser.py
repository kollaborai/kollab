"""Slash command parser for Kollab."""

import logging
import shlex
from datetime import datetime
from typing import Any, Dict, List, Optional

from kollabor_events.models import SlashCommand  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class SlashCommandParser:
    """Parses user input for slash commands.

    Handles command detection, parsing, and argument extraction
    with proper validation and error handling.
    """

    def __init__(self) -> None:
        """Initialize the slash command parser."""
        self.logger = logger

    def is_slash_command(self, input_text: str) -> bool:
        """Check if input starts with a slash command.

        Args:
            input_text: User input to check.

        Returns:
            True if input is a slash command, False otherwise.
        """
        if not input_text:
            return False

        # Check if input starts with '/' and has content after
        stripped = input_text.strip()
        return stripped.startswith("/") and len(stripped) > 1

    def parse_command(self, input_text: str) -> Optional[SlashCommand]:
        """Parse slash command from user input.

        Args:
            input_text: Raw user input containing slash command.

        Returns:
            Parsed SlashCommand object or None if parsing fails.
        """
        if not self.is_slash_command(input_text):
            return None

        try:
            # Remove leading slash and strip whitespace
            command_text = input_text.strip()[1:]

            if not command_text:
                return None

            # Split command and arguments using shell-like parsing
            # This handles quoted arguments properly: /save "my file.txt"
            try:
                parts = shlex.split(command_text)
            except ValueError as e:
                # Handle malformed quotes gracefully
                self.logger.warning(f"Quote parsing failed, using simple split: {e}")
                parts = command_text.split()

            if not parts:
                return None

            command_name = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []

            # Create parsed command
            slash_command = SlashCommand(
                name=command_name,
                args=args,
                raw_input=input_text,
                timestamp=datetime.now(),
            )

            # Extract parameters for known parameter patterns
            slash_command.parameters = self._extract_parameters(args)

            self.logger.debug(f"Parsed command: {command_name} with {len(args)} args")
            return slash_command

        except Exception as e:
            self.logger.error(f"Error parsing slash command '{input_text}': {e}")
            return None

    def _extract_parameters(self, args: List[str]) -> Dict[str, Any]:
        """Extract parameters from command arguments.

        Supports patterns like:
        - /config set theme dark
        - /save --format json filename.json
        - /load -f "my file.txt"

        Args:
            args: List of command arguments.

        Returns:
            Dictionary of extracted parameters.
        """
        parameters: Dict[str, Any] = {}

        i = 0
        while i < len(args):
            arg = args[i]

            # Handle --key=value format
            if "=" in arg and arg.startswith("--"):
                key, value = arg[2:].split("=", 1)
                parameters[key] = value

            # Handle --key value format
            elif arg.startswith("--") and i + 1 < len(args):
                key = arg[2:]
                value = args[i + 1]
                parameters[key] = value
                i += 1  # Skip next arg since we consumed it

            # Handle -k value format (short flags)
            elif arg.startswith("-") and len(arg) == 2 and i + 1 < len(args):
                key = arg[1:]
                value = args[i + 1]
                parameters[key] = value
                i += 1  # Skip next arg since we consumed it

            # Handle boolean flags
            elif arg.startswith("--"):
                key = arg[2:]
                parameters[key] = True

            elif arg.startswith("-") and len(arg) == 2:
                key = arg[1:]
                parameters[key] = True

            i += 1

        return parameters

    def validate_command(self, command: SlashCommand) -> List[str]:
        """Validate a parsed command for basic correctness.

        Args:
            command: Parsed slash command to validate.

        Returns:
            List of validation errors, empty if valid.
        """
        errors = []

        # Validate command name
        if not command.name:
            errors.append("Command name cannot be empty")
        elif (
            not command.name.isalnum()
            and "-" not in command.name
            and "_" not in command.name
        ):
            errors.append(f"Invalid command name: {command.name}")

        # Validate raw input
        if not command.raw_input.strip():
            errors.append("Raw input cannot be empty")

        # Validate arguments don't contain control characters
        for i, arg in enumerate(command.args):
            if any(ord(c) < 32 for c in arg if c != "\t"):
                errors.append(f"Argument {i+1} contains invalid control characters")

        return errors


