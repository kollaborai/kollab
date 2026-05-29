"""Fullscreen plugin command integration system.

This module handles automatic discovery and registration of slash commands
for fullscreen plugins, enabling dynamic plugin-to-command mapping.
"""

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

from kollabor_events.models import CommandCategory, CommandDefinition, CommandMode
from kollabor_tui.fullscreen.plugin import FullScreenPlugin

from ..commands.registry import SlashCommandRegistry

logger = logging.getLogger(__name__)


class FullScreenCommandIntegrator:
    """Integrates fullscreen plugins with the slash command system.

    This class:
    - Discovers fullscreen plugins in plugins/fullscreen/
    - Auto-registers slash commands based on plugin metadata
    - Handles dynamic plugin loading/unloading
    - Maps commands to plugin execution
    """

    def __init__(
        self,
        command_registry: SlashCommandRegistry,
        event_bus,
        config=None,
        profile_manager=None,
        terminal_renderer=None,
        app=None,
    ):
        """Initialize the fullscreen command integrator.

        Args:
            command_registry: Slash command registry for registration
            event_bus: Event bus for communication
            config: Optional config service for plugins that need it
            profile_manager: Optional profile manager for plugins that need it
            terminal_renderer: Optional terminal renderer for fullscreen manager
            app: Optional app reference for plugins that need full app access
        """
        self.command_registry = command_registry
        self.event_bus = event_bus
        self.config = config
        self.profile_manager = profile_manager
        self.terminal_renderer = terminal_renderer
        self.app = app  # Full app reference for plugins that need more services
        self.registered_plugins: Dict[str, Type[FullScreenPlugin]] = {}
        self.plugin_instances: Dict[str, FullScreenPlugin] = {}
        self._fullscreen_manager = None

        logger.info("FullScreen command integrator initialized")

    def discover_and_register_plugins(self, plugins_dir: Path) -> int:
        """Discover and register all fullscreen plugins.

        Args:
            plugins_dir: Base plugins directory

        Returns:
            Number of plugins registered
        """
        fullscreen_dir = plugins_dir / "fullscreen"
        if not fullscreen_dir.exists():
            logger.info("No fullscreen plugins directory found")
            return 0

        registered_count = 0

        # Plugins superseded by an AltView replacement: skip them here so the
        # AltView version (which pauses the render loop to prevent flicker)
        # wins the command instead of being shadowed by registration order.
        superseded_by_altview = {"conversations_plugin"}

        # Scan for Python files in fullscreen directory
        for plugin_file in fullscreen_dir.glob("*.py"):
            if plugin_file.name.startswith("__"):
                continue
            if plugin_file.stem in superseded_by_altview:
                logger.info(
                    f"Skipping fullscreen plugin '{plugin_file.stem}' "
                    "- superseded by AltView version"
                )
                continue

            try:
                plugin_class = self._load_plugin_class(plugin_file)
                if plugin_class:
                    if self._register_plugin_commands(plugin_class):
                        registered_count += 1
                        logger.info(
                            f"Registered commands for plugin: {plugin_class.__name__}"
                        )
            except Exception as e:
                logger.error(f"Failed to load plugin {plugin_file.name}: {e}")

        logger.info(f"Discovered and registered {registered_count} fullscreen plugins")
        return registered_count

    def _load_plugin_class(self, plugin_file: Path) -> Optional[Type[FullScreenPlugin]]:
        """Load a plugin class from a Python file.

        Args:
            plugin_file: Path to the plugin Python file

        Returns:
            Plugin class or None if not found/invalid
        """
        try:
            # Create module spec and load
            module_name = f"plugins.fullscreen.{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find FullScreenPlugin subclass
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, FullScreenPlugin)
                    and attr != FullScreenPlugin
                ):
                    return attr

            logger.warning(f"No FullScreenPlugin subclass found in {plugin_file.name}")
            return None

        except Exception as e:
            logger.error(f"Error loading plugin class from {plugin_file}: {e}")
            return None

    def _register_plugin_commands(self, plugin_class: Type[FullScreenPlugin]) -> bool:
        """Register slash commands for a plugin class.

        Args:
            plugin_class: The plugin class to register commands for

        Returns:
            True if registration successful
        """
        try:
            # Create temporary instance to get metadata
            temp_instance = plugin_class()  # type: ignore[call-arg]
            metadata = temp_instance.metadata

            if not metadata:
                logger.warning(f"Plugin {plugin_class.__name__} has no metadata")
                return False

            # Store plugin class for later instantiation
            self.registered_plugins[metadata.name] = plugin_class

            # Skip if command already registered (e.g. by altview_integrator)
            if self.command_registry.get_command(metadata.name):
                logger.debug(
                    f"Skipping fullscreen command '{metadata.name}' - already registered"
                )
                return True

            # Register primary command (plugin name)
            primary_command = CommandDefinition(
                name=metadata.name,
                aliases=metadata.aliases or [],
                description=metadata.description,
                category=CommandCategory.CUSTOM,  # Fullscreen plugins are custom/plugins
                mode=CommandMode.INSTANT,
                handler=self._create_plugin_handler(metadata.name),
                icon=metadata.icon,
                plugin_name="fullscreen_integrator",
                cli_hidden=False,  # Allow CLI invocation: kollab --matrix
            )

            success = self.command_registry.register_command(primary_command)
            if not success:
                logger.error(f"Failed to register primary command for {metadata.name}")
                return False

            # Aliases are stored in the primary command's aliases field
            # No need to register them separately - registry handles alias lookups
            if metadata.aliases:
                logger.debug(f"Command {metadata.name} has aliases: {metadata.aliases}")

            return True

        except Exception as e:
            logger.error(f"Error registering commands for {plugin_class.__name__}: {e}")
            return False

    def _create_plugin_handler(self, plugin_name: str):
        """Create a command handler for a specific plugin.

        Args:
            plugin_name: Name of the plugin to handle

        Returns:
            Async command handler function
        """

        async def handler(command):
            """Handle command execution for fullscreen plugin."""
            try:
                # Get or create fullscreen manager
                if not self._fullscreen_manager:
                    from kollabor_tui.fullscreen import FullScreenManager

                    self._fullscreen_manager = FullScreenManager(
                        self.event_bus, self.terminal_renderer
                    )

                # Get or create plugin instance
                if plugin_name not in self.plugin_instances:
                    plugin_class = self.registered_plugins.get(plugin_name)
                    if not plugin_class:
                        raise ValueError(f"Plugin class not found: {plugin_name}")

                    plugin_instance = plugin_class()

                    # Pass managers to plugins that need them (e.g., setup wizard)
                    if hasattr(plugin_instance, "set_managers"):
                        plugin_instance.set_managers(self.config, self.profile_manager)

                    self.plugin_instances[plugin_name] = plugin_instance
                    self._fullscreen_manager.register_plugin(plugin_instance)
                    logger.debug(
                        f"Created and registered plugin instance: {plugin_name}"
                    )

                # Always refresh widget system on each launch (in case it changed)
                plugin_instance = self.plugin_instances[plugin_name]
                if hasattr(plugin_instance, "set_widget_system") and self.app:
                    widget_registry = getattr(self.app, "widget_registry", None)
                    layout_manager = getattr(self.app, "layout_manager", None)
                    layout_renderer = getattr(self.app, "layout_renderer", None)
                    if widget_registry and layout_manager and layout_renderer:
                        plugin_instance.set_widget_system(
                            widget_registry, layout_manager, layout_renderer
                        )
                        logger.info(f"Widget system set for plugin: {plugin_name}")
                    else:
                        logger.warning(
                            f"Widget system components missing for plugin: {plugin_name}"
                        )

                # Set app reference for plugins that need it (e.g., conversations browser)
                if hasattr(plugin_instance, "set_app") and self.app:
                    plugin_instance.set_app(self.app)
                    logger.debug(f"App reference set for plugin: {plugin_name}")

                # Launch the plugin
                success = await self._fullscreen_manager.launch_plugin(plugin_name)

                if success:
                    # Resume a selected session if the browser picked one.
                    # Shared with the AltView conversations browser.
                    from kollabor.llm.session_resume import (
                        resume_selected_session,
                    )

                    outcome = await resume_selected_session(
                        self.app, self.event_bus, plugin_instance
                    )

                    from kollabor_events.models import CommandResult

                    if not outcome.success:
                        return CommandResult(
                            success=False,
                            message=outcome.error or "Failed to resume session",
                            display_type="error",
                        )

                    return CommandResult(
                        success=True,
                        message="",  # No message to avoid display artifacts
                        display_type="success",
                    )
                else:
                    from kollabor_events.models import CommandResult

                    return CommandResult(
                        success=False,
                        message=f"Failed to launch {plugin_name} plugin",
                        display_type="error",
                    )

            except Exception as e:
                logger.error(f"Error executing plugin {plugin_name}: {e}")
                from kollabor_events.models import CommandResult

                return CommandResult(
                    success=False,
                    message=f"Plugin error: {str(e)}",
                    display_type="error",
                )

        return handler

    def unregister_plugin(self, plugin_name: str) -> bool:
        """Unregister a plugin and its commands.

        Args:
            plugin_name: Name of plugin to unregister

        Returns:
            True if successful
        """
        try:
            # Remove from our tracking
            if plugin_name in self.registered_plugins:
                plugin_class = self.registered_plugins[plugin_name]
                temp_instance = plugin_class()  # type: ignore[call-arg]
                metadata = temp_instance.metadata

                # Unregister the primary command (aliases are handled by registry)
                self.command_registry.unregister_command(metadata.name)

                del self.registered_plugins[plugin_name]

                if plugin_name in self.plugin_instances:
                    del self.plugin_instances[plugin_name]

                logger.info(f"Unregistered plugin: {plugin_name}")
                return True

            return False

        except Exception as e:
            logger.error(f"Error unregistering plugin {plugin_name}: {e}")
            return False

    def get_registered_plugins(self) -> List[str]:
        """Get list of registered plugin names.

        Returns:
            List of plugin names
        """
        return list(self.registered_plugins.keys())

    def reload_plugins(self, plugins_dir: Path) -> int:
        """Reload all fullscreen plugins from directory.

        Args:
            plugins_dir: Base plugins directory

        Returns:
            Number of plugins reloaded
        """
        # Unregister all current plugins
        for plugin_name in list(self.registered_plugins.keys()):
            self.unregister_plugin(plugin_name)

        # Re-discover and register
        return self.discover_and_register_plugins(plugins_dir)
