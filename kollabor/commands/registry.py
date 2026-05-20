"""Command registry for slash command management."""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set

from kollabor_events.models import CommandCategory, CommandDefinition  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Reserved command names that cannot be overridden by plugins
RESERVED_COMMANDS: Set[str] = {
    "help",
    "version",
    "config",
    "doctor",
    "status",
    "permissions",
    "profile",
    "agent",
    "skill",
    "model",
    "cd",
}


class SlashCommandRegistry:
    """Registry for managing slash command definitions.

    Handles command registration, conflict detection, and organization
    by plugin and category for efficient discovery and execution.
    """

    def __init__(self) -> None:
        """Initialize the command registry."""
        self._commands: Dict[str, CommandDefinition] = {}
        self._core_commands: Dict[str, CommandDefinition] = {}  # Internal core commands
        self._aliases: Dict[str, str] = {}  # alias -> command_name mapping
        self._plugin_commands: Dict[str, List[str]] = defaultdict(list)
        self._category_commands: Dict[CommandCategory, List[str]] = defaultdict(list)
        self._reserved: Set[str] = RESERVED_COMMANDS.copy()
        self.logger = logger

    def _register_core_command(self, command_def: CommandDefinition) -> None:
        """Register a core system command (internal use only).

        Core commands can use reserved names and are stored separately.
        """
        self._core_commands[command_def.name] = command_def
        for alias in command_def.aliases:
            self._core_commands[alias] = command_def
        self.logger.debug(f"Registered core command: /{command_def.name}")

    def register_command(self, command_def: CommandDefinition) -> bool:
        """Register a slash command with the registry.

        Args:
            command_def: Command definition to register.

        Returns:
            True if registration successful, False if conflicts exist.

        Raises:
            ValueError: If command conflicts with reserved namespace.
        """
        try:
            # Validate command definition
            validation_errors = self._validate_command_definition(command_def)
            if validation_errors:
                self.logger.error(
                    f"Command validation failed for {command_def.name}: {validation_errors}"
                )
                return False

            # Check reserved namespace (no exceptions - core is core)
            if command_def.name in self._reserved:
                # Core commands use separate internal registry
                if command_def.plugin_name == "system":
                    self._register_core_command(command_def)
                    return True
                else:
                    self.logger.error(
                        f"Command '/{command_def.name}' is reserved and cannot be "
                        f"overridden by plugin '{command_def.plugin_name}'"
                    )
                    return False

            # Check for name conflicts
            if command_def.name in self._commands:
                existing = self._commands[command_def.name]
                self.logger.error(
                    f"Command name conflict: '{command_def.name}' already registered by plugin '{existing.plugin_name}'"
                )
                return False

            # Check for alias conflicts
            for alias in command_def.aliases:
                if alias in self._commands or alias in self._aliases:
                    self.logger.error(f"Alias conflict: '{alias}' already in use")
                    return False

            # Register the command
            self._commands[command_def.name] = command_def

            # Register aliases
            for alias in command_def.aliases:
                self._aliases[alias] = command_def.name

            # Track by plugin
            self._plugin_commands[command_def.plugin_name].append(command_def.name)

            # Track by category
            self._category_commands[command_def.category].append(command_def.name)

            self.logger.info(
                f"Registered command '{command_def.name}' from plugin '{command_def.plugin_name}' "
                f"with {len(command_def.aliases)} aliases"
            )
            return True

        except Exception as e:
            self.logger.error(f"Error registering command '{command_def.name}': {e}")
            return False

    def unregister_command(self, command_name: str) -> bool:
        """Unregister a command and its aliases.

        Args:
            command_name: Name of command to unregister.

        Returns:
            True if successfully unregistered, False if not found.
        """
        if command_name not in self._commands:
            return False

        try:
            command_def = self._commands[command_name]

            # Remove from main registry
            del self._commands[command_name]

            # Remove aliases
            for alias in command_def.aliases:
                if alias in self._aliases:
                    del self._aliases[alias]

            # Remove from plugin tracking
            if command_def.plugin_name in self._plugin_commands:
                plugin_commands = self._plugin_commands[command_def.plugin_name]
                if command_name in plugin_commands:
                    plugin_commands.remove(command_name)

            # Remove from category tracking
            category_commands = self._category_commands[command_def.category]
            if command_name in category_commands:
                category_commands.remove(command_name)

            self.logger.info(f"Unregistered command '{command_name}'")
            return True

        except Exception as e:
            self.logger.error(f"Error unregistering command '{command_name}': {e}")
            return False

    def unregister_plugin_commands(self, plugin_name: str) -> int:
        """Unregister all commands from a specific plugin.

        Args:
            plugin_name: Name of plugin whose commands to unregister.

        Returns:
            Number of commands unregistered.
        """
        if plugin_name not in self._plugin_commands:
            return 0

        # Get list of commands to unregister (copy to avoid modification during iteration)
        commands_to_remove = list(self._plugin_commands[plugin_name])

        unregistered_count = 0
        for command_name in commands_to_remove:
            if self.unregister_command(command_name):
                unregistered_count += 1

        # Clean up empty plugin entry
        if (
            plugin_name in self._plugin_commands
            and not self._plugin_commands[plugin_name]
        ):
            del self._plugin_commands[plugin_name]

        self.logger.info(
            f"Unregistered {unregistered_count} commands from plugin '{plugin_name}'"
        )
        return unregistered_count

    def get_command(self, name_or_alias: str) -> Optional[CommandDefinition]:
        """Get command definition by name or alias.

        Args:
            name_or_alias: Command name or alias to look up.

        Returns:
            Command definition if found, None otherwise.
        """
        # Check core commands first
        if name_or_alias in self._core_commands:
            return self._core_commands[name_or_alias]

        # Direct name lookup
        if name_or_alias in self._commands:
            return self._commands[name_or_alias]

        # Alias lookup
        if name_or_alias in self._aliases:
            command_name = self._aliases[name_or_alias]
            return self._commands.get(command_name)

        return None

    def get_all_commands(self, include_hidden: bool = False) -> List[CommandDefinition]:
        """Get all registered commands (plugin + core).

        Args:
            include_hidden: Whether to include hidden commands.

        Returns:
            List of all command definitions.
        """
        # Combine core and plugin commands, deduplicate by name
        all_commands: Dict[str, CommandDefinition] = {}

        # Add core commands
        for cmd in self._core_commands.values():
            if cmd.name not in all_commands:
                all_commands[cmd.name] = cmd

        # Add plugin commands
        for cmd in self._commands.values():
            if cmd.name not in all_commands:
                all_commands[cmd.name] = cmd

        commands = list(all_commands.values())

        if not include_hidden:
            commands = [cmd for cmd in commands if not cmd.hidden]

        return sorted(commands, key=lambda cmd: (cmd.category.value, cmd.name))

    def list_commands(self, include_hidden: bool = False) -> List[CommandDefinition]:
        """List all registered commands.

        Args:
            include_hidden: Whether to include hidden commands.

        Returns:
            List of command definitions.
        """
        commands = list(self._commands.values())

        if not include_hidden:
            commands = [cmd for cmd in commands if not cmd.hidden]

        # Sort by category, then by name
        return sorted(commands, key=lambda cmd: (cmd.category.value, cmd.name))

    def get_commands_by_plugin(self, plugin_name: str) -> List[CommandDefinition]:
        """Get all commands registered by a specific plugin.

        Args:
            plugin_name: Name of plugin to get commands for.

        Returns:
            List of command definitions from the plugin.
        """
        if plugin_name not in self._plugin_commands:
            return []

        command_names = self._plugin_commands[plugin_name]
        return [
            self._commands[name] for name in command_names if name in self._commands
        ]

    def get_commands_by_category(
        self, category: CommandCategory
    ) -> List[CommandDefinition]:
        """Get all commands in a specific category.

        Args:
            category: Category to get commands for.

        Returns:
            List of command definitions in the category.
        """
        if category not in self._category_commands:
            return []

        command_names = self._category_commands[category]
        return [
            self._commands[name] for name in command_names if name in self._commands
        ]

    def get_plugin_categories(self) -> Dict[str, List[CommandCategory]]:
        """Get categories used by each plugin.

        Returns:
            Dictionary mapping plugin names to their used categories.
        """
        plugin_categories = defaultdict(set)

        for command_def in self._commands.values():
            plugin_categories[command_def.plugin_name].add(command_def.category)

        # Convert sets to sorted lists
        return {
            plugin: sorted(categories, key=lambda c: c.value)
            for plugin, categories in plugin_categories.items()
        }

    def search_commands(self, query: str) -> List[CommandDefinition]:
        """Search commands by name, description, or aliases.

        Args:
            query: Search query string.

        Returns:
            List of matching command definitions.
        """
        query_lower = query.lower()
        name_matches = []
        alias_matches = []
        substring_matches = []

        # Combine core and plugin commands, deduplicate by name
        all_commands: Dict[str, CommandDefinition] = {}
        for cmd in self._core_commands.values():
            if cmd.name not in all_commands:
                all_commands[cmd.name] = cmd
        for cmd in self._commands.values():
            if cmd.name not in all_commands:
                all_commands[cmd.name] = cmd

        for command_def in all_commands.values():
            # Skip hidden commands in search
            if command_def.hidden:
                continue

            # Priority 1: Name starts with query (prefix match on name)
            if command_def.name.lower().startswith(query_lower):
                name_matches.append(command_def)
                continue

            # Priority 2: Alias starts with query (prefix match on alias)
            if any(
                alias.lower().startswith(query_lower) for alias in command_def.aliases
            ):
                alias_matches.append(command_def)
                continue

            # Priority 3: Query is substring of name
            if query_lower in command_def.name.lower():
                substring_matches.append(command_def)
                continue

            # Priority 4: Query is substring of description
            if query_lower in command_def.description.lower():
                substring_matches.append(command_def)
                continue

            # Priority 5: Query is substring of alias
            if any(query_lower in alias.lower() for alias in command_def.aliases):
                substring_matches.append(command_def)
                continue

        # Return matches in priority order: name matches first, then alias matches, then substrings
        # For command menu filtering, prioritize exact prefix matches
        if name_matches:
            return sorted(name_matches, key=lambda cmd: cmd.name)
        elif alias_matches:
            return sorted(alias_matches, key=lambda cmd: cmd.name)
        else:
            return sorted(substring_matches, key=lambda cmd: cmd.name)

    def get_registry_stats(self) -> Dict[str, int]:
        """Get registry statistics for debugging.

        Returns:
            Dictionary with registry statistics.
        """
        all_commands = self.get_all_commands(include_hidden=True)
        return {
            "total_commands": len(all_commands),
            "plugin_commands": len(self._commands),
            "core_commands": len(set(cmd.name for cmd in self._core_commands.values())),
            "total_aliases": len(self._aliases),
            "plugins": len(self._plugin_commands),
            "categories": len(
                [cat for cat in self._category_commands if self._category_commands[cat]]
            ),
            "hidden_commands": len([cmd for cmd in all_commands if cmd.hidden]),
            "enabled_commands": len([cmd for cmd in all_commands if cmd.enabled]),
            "cli_enabled_commands": len(
                [cmd for cmd in all_commands if not cmd.cli_hidden]
            ),
        }

    def _validate_command_definition(self, command_def: CommandDefinition) -> List[str]:
        """Validate a command definition for correctness.

        Args:
            command_def: Command definition to validate.

        Returns:
            List of validation errors, empty if valid.
        """
        errors = []

        # Validate name
        if not command_def.name:
            errors.append("Command name cannot be empty")
        elif not command_def.name.replace("-", "").replace("_", "").isalnum():
            errors.append(f"Invalid command name format: {command_def.name}")

        # Validate description
        if not command_def.description:
            errors.append("Command description cannot be empty")

        # Validate handler
        if not callable(command_def.handler):
            errors.append("Command handler must be callable")

        # Validate plugin name
        if not command_def.plugin_name:
            errors.append("Plugin name cannot be empty")

        # Validate aliases (allow special characters like ?)
        for alias in command_def.aliases:
            if not alias or len(alias.strip()) == 0:
                errors.append("Empty alias not allowed")
            elif not (
                alias.replace("-", "").replace("_", "").isalnum() or alias in ["?"]
            ):
                errors.append(f"Invalid alias format: {alias}")

        return errors
