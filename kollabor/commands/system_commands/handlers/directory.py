"""Directory command handler for /cd."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
    UIConfig,
)

from ..base import BaseCommandHandler

logger = logging.getLogger(__name__)


class DirectoryCommandHandler(BaseCommandHandler):
    """Handles /cd command and directory modal actions.

    Provides directory navigation functionality with modal UI for
    browsing and changing working directories.
    """

    MODAL_ACTIONS: Set[str] = {
        "select_directory",
        "cwd_show",
        "cwd_parent",
        "cwd_home",
        "cwd_custom",
    }

    def __init__(
        self,
        command_registry,
        event_bus,
        config_manager,
    ):
        """Initialize directory command handler.

        Args:
            command_registry: Command registry for registration.
            event_bus: Event bus for hooks and service lookup.
            config_manager: Configuration manager.
        """
        super().__init__(command_registry, event_bus, config_manager)
        self._previous_directory: Optional[str] = None  # Handler owns its state
        self.logger = logging.getLogger(self.__class__.__name__)

    def register_commands(self) -> None:
        """Register /cd command."""
        cd_command = CommandDefinition(
            name="cd",
            description="Change working directory",
            handler=self.handle_cd,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.STATUS_TAKEOVER,
            aliases=["chdir", "dir"],
            icon="[DIR]",
            subcommands=[
                SubcommandInfo("", "<path>", "Change to specified directory"),
                SubcommandInfo("..", "", "Go to parent directory"),
                SubcommandInfo("-", "", "Go to previous directory"),
            ],
            ui_config=UIConfig(
                type="modal",
                navigation=["↑↓", "Enter", "Esc"],
                height=15,
                title="Directory Navigator",
                footer="↑↓ navigate • Enter select • Esc exit",
            ),
        )
        self.command_registry.register_command(cd_command)
        self.logger.info("Directory command registered")

    async def handle_cd(self, command: SlashCommand) -> CommandResult:
        """Handle /cd command - change working directory.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            args = command.args or []

            if not args:
                # Show directory navigator modal
                return await self._show_cd_modal()
            elif args[0] == "..":
                # Go to parent directory
                return await self._change_directory("..")
            elif args[0] == "-":
                # Go to previous directory
                prev_dir = self._previous_directory
                if prev_dir:
                    return await self._change_directory(prev_dir)
                return CommandResult(
                    success=False, message="No previous directory", display_type="error"
                )
            else:
                # Change to specified path
                target_path = " ".join(args)  # Handle paths with spaces
                return await self._change_directory(target_path)

        except Exception as e:
            self.logger.error(f"Error in cd command: {e}")
            return CommandResult(
                success=False,
                message=f"Error changing directory: {str(e)}",
                display_type="error",
            )

    async def handle_modal_action(self, action: str, data: Dict) -> Dict:
        """Handle directory modal actions. MUTATE data in-place.

        Args:
            action: The action name (e.g., "select_directory", "cwd_show").
            data: The event data dict to mutate.

        Returns:
            The mutated data dict.
        """
        if action == "select_directory":
            target_path = data.get("command", {}).get("target_path")
            if target_path:
                try:
                    self._previous_directory = os.getcwd()
                    os.chdir(target_path)
                    data["display_messages"] = [
                        ("system", f"[ok] Changed to: {target_path}", {}),
                    ]
                except PermissionError:
                    data["display_messages"] = [
                        ("error", f"[err] Permission denied: {target_path}", {}),
                    ]
                except Exception as e:
                    data["display_messages"] = [
                        ("error", f"[err] Failed to change directory: {e}", {}),
                    ]

        elif action == "cwd_show":
            # Just show current directory info
            cwd = os.getcwd()
            data["display_messages"] = [
                ("system", f"[dir] Current: {cwd}", {}),
            ]

        elif action == "cwd_parent":
            # Go to parent directory
            try:
                self._previous_directory = os.getcwd()
                parent = str(Path(self._previous_directory).parent)
                os.chdir(parent)
                data["display_messages"] = [
                    ("system", f"[ok] Changed to: {parent}", {}),
                ]
            except Exception as e:
                data["display_messages"] = [
                    ("error", f"[err] Failed to go to parent: {e}", {}),
                ]

        elif action == "cwd_home":
            # Go to home directory
            try:
                self._previous_directory = os.getcwd()
                home = str(Path.home())
                os.chdir(home)
                data["display_messages"] = [
                    ("system", f"[ok] Changed to home: {home}", {}),
                ]
            except Exception as e:
                data["display_messages"] = [
                    ("error", f"[err] Failed to go to home: {e}", {}),
                ]

        elif action == "cwd_custom":
            # Get custom path from form data
            form_data = data.get("command", {}).get("form_data", {})
            custom_path = form_data.get("custom_path", "").strip()
            if custom_path:
                try:
                    self._previous_directory = os.getcwd()
                    expanded = os.path.expanduser(custom_path)
                    resolved = str(Path(expanded).resolve())
                    os.chdir(resolved)
                    data["display_messages"] = [
                        ("system", f"[ok] Changed to: {resolved}", {}),
                    ]
                except Exception as e:
                    data["display_messages"] = [
                        ("error", f"[err] Failed to change directory: {e}", {}),
                    ]
            else:
                data["display_messages"] = [
                    ("error", "[err] No path provided", {}),
                ]
        return data

    def _get_cd_modal_definition(self) -> Dict[str, Any]:
        """Get modal definition for directory navigation.

        Returns:
            Modal definition dictionary with directory entries.
        """
        cwd = os.getcwd()
        parent = str(Path(cwd).parent)
        home = str(Path.home())

        # Build directory list
        dir_commands = []

        # Parent directory option
        if cwd != "/":
            dir_commands.append(
                {
                    "name": "    .. (parent)",
                    "description": parent,
                    "target_path": parent,
                    "action": "select_directory",
                }
            )

        # Home directory option
        if cwd != home:
            dir_commands.append(
                {
                    "name": "    ~ (home)",
                    "description": home,
                    "target_path": home,
                    "action": "select_directory",
                }
            )

        # List subdirectories
        try:
            entries = sorted(os.listdir(cwd))
            for entry in entries:
                if entry.startswith("."):
                    continue  # Skip hidden
                full_path = os.path.join(cwd, entry)
                if os.path.isdir(full_path):
                    dir_commands.append(
                        {
                            "name": f"    {entry}/",
                            "description": full_path,
                            "target_path": full_path,
                            "action": "select_directory",
                        }
                    )
        except PermissionError:
            pass

        return {
            "title": "Directory Navigator",
            "footer": "↑↓ navigate • Enter select • Esc close",
            "sections": [{"title": f"Current: {cwd}", "commands": dir_commands}],
            "actions": [
                {"key": "Enter", "label": "Select", "action": "select"},
                {"key": "Escape", "label": "Close", "action": "cancel"},
            ],
        }

    async def _show_cd_modal(self) -> CommandResult:
        """Show the directory navigator modal.

        Returns:
            Command result with modal definition.
        """
        modal_def = self._get_cd_modal_definition()

        return CommandResult(
            success=True,
            message="Select directory",
            ui_config=UIConfig(
                type="modal",
                title=modal_def["title"],
                height=15,
                modal_config=modal_def,
            ),
            display_type="modal",
        )

    async def _change_directory(self, target_path: str) -> CommandResult:
        """Change to specified directory.

        Args:
            target_path: Path to change to.

        Returns:
            Command result.
        """
        try:
            # Expand ~ and resolve path
            expanded = os.path.expanduser(target_path)
            resolved = str(Path(expanded).resolve())

            if not os.path.exists(resolved):
                return CommandResult(
                    success=False,
                    message=f"Directory not found: {target_path}",
                    display_type="error",
                )

            if not os.path.isdir(resolved):
                return CommandResult(
                    success=False,
                    message=f"Not a directory: {target_path}",
                    display_type="error",
                )

            # Save previous directory
            self._previous_directory = os.getcwd()

            # Change directory
            os.chdir(resolved)

            return CommandResult(
                success=True, message=f"Changed to: {resolved}", display_type="success"
            )
        except PermissionError:
            return CommandResult(
                success=False,
                message=f"Permission denied: {target_path}",
                display_type="error",
            )
