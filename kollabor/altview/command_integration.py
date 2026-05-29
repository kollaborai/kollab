"""AltView plugin command integration system.

Discovers AltView plugins from plugins/altview/, registers them as slash
commands, and manages the AltViewStackManager lifecycle. Modeled after
FullScreenCommandIntegrator but adapted for persistent, stackable views
that support suspend/resume with display queue replay.
"""

import hashlib
import importlib.util
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Type

from kollabor_agent.mcp_manager import MCPManager
from kollabor_config.config_utils import resolve_global_path
from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
)

from ..commands.registry import SlashCommandRegistry

if TYPE_CHECKING:
    from kollabor_tui.altview.stack_manager import AltViewStackManager

logger = logging.getLogger(__name__)


class AltViewCommandIntegrator:
    """Integrates AltView plugins with the slash command system.

    Discovers plugins from plugins/altview/ directory, registers them
    as slash commands, and manages the AltViewStackManager lifecycle.

    Unlike the FullScreenCommandIntegrator which creates single-instance
    plugins, this integrator supports multiple named sessions per plugin
    class. For example, ``/research my-topic`` and ``/research other-topic``
    create two independent AltView instances of the same plugin.
    """

    def __init__(
        self,
        command_registry: SlashCommandRegistry,
        event_bus,
        terminal_renderer,
        config=None,
        profile_manager=None,
        app=None,
    ):
        """Initialize the AltView command integrator.

        Args:
            command_registry: Slash command registry for registration.
            event_bus: Event bus for communication.
            terminal_renderer: Terminal renderer for stack manager.
            config: Optional config service for plugins that need it.
            profile_manager: Optional profile manager for plugins that need it.
            app: Optional app reference for plugins that need full access.
        """
        self.command_registry = command_registry
        self.event_bus = event_bus
        self.terminal_renderer = terminal_renderer
        self.config = config
        self.profile_manager = profile_manager
        self.app = app
        self._stack_manager: Optional["AltViewStackManager"] = None
        self._plugin_classes: Dict[str, Type] = {}
        self._plugin_instances: Dict[str, object] = {}
        self._command_delegates: Dict[str, CommandDefinition] = {}
        self._single_session_plugins: set[str] = set()

        logger.info("AltView command integrator initialized")

    def _get_stack_manager(self) -> "AltViewStackManager":
        """Lazy-create and register the stack manager.

        The stack manager is created on first use rather than at init time
        so that the TUI altview package is only imported when actually needed.

        Returns:
            The singleton AltViewStackManager instance.
        """
        if not self._stack_manager:
            from kollabor_tui.altview.stack_manager import AltViewStackManager

            self._stack_manager = AltViewStackManager(
                self.event_bus, self.terminal_renderer
            )
            self.event_bus.register_service(
                "altview_stack_manager", self._stack_manager
            )
        return self._stack_manager

    def discover_and_register_plugins(self, plugins_dir: Path) -> int:
        """Discover and register all AltView plugins.

        Scans plugins/altview/ for Python files containing AltView
        subclasses. Each discovered plugin is registered as a slash
        command using its metadata.

        Args:
            plugins_dir: Base plugins directory (parent of altview/).

        Returns:
            Number of plugins registered.
        """
        altview_dir = plugins_dir / "altview"
        if not altview_dir.exists():
            logger.info("No altview plugins directory found")
            return 0

        registered_count = 0

        for plugin_file in altview_dir.glob("*.py"):
            if plugin_file.name.startswith("__"):
                continue

            try:
                plugin_class = self._load_plugin_class(plugin_file)
                if plugin_class:
                    if self._register_plugin_commands(plugin_class):
                        registered_count += 1
                        logger.info(
                            "Registered AltView commands for: %s",
                            plugin_class.__name__,
                        )
            except Exception as e:
                logger.error(
                    "Failed to load AltView plugin %s: %s", plugin_file.name, e
                )

        logger.info("Discovered and registered %d AltView plugins", registered_count)
        return registered_count

    def _load_plugin_class(self, plugin_file: Path) -> Optional[Type]:
        """Load an AltView subclass from a plugin file.

        Uses importlib to dynamically load the module and scan for a
        class that inherits from the AltView base.

        Args:
            plugin_file: Path to the plugin Python file.

        Returns:
            Plugin class or None if not found/invalid.
        """
        try:
            # Lazy import - AltView base class lives in the TUI package
            from kollabor_tui.altview.base import AltView

            module_name = f"plugins.altview.{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find AltView subclass (skip the base class itself)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, AltView)
                    and attr is not AltView
                ):
                    return attr

            logger.warning("No AltView subclass found in %s", plugin_file.name)
            return None

        except Exception as e:
            logger.error("Error loading AltView class from %s: %s", plugin_file, e)
            return None

    def _register_plugin_commands(self, plugin_class: Type) -> bool:
        """Register slash commands for a plugin based on its metadata.

        Creates a temporary instance to read metadata, then registers
        the command with CommandMode.ALTVIEW.

        Args:
            plugin_class: The AltView subclass to register commands for.

        Returns:
            True if registration successful.
        """
        try:
            temp_instance = plugin_class()
            metadata = temp_instance.metadata

            if not metadata:
                logger.warning(
                    "AltView plugin %s has no metadata", plugin_class.__name__
                )
                return False

            plugin_name = metadata.plugin_type
            if not getattr(metadata, "supports_named_sessions", True):
                self._single_session_plugins.add(plugin_name)

            # Skip internal plugins (they're invoked by other plugins, not the user)
            if getattr(metadata, "category", "") == "internal":
                self._plugin_classes[plugin_name] = plugin_class
                logger.debug(
                    "Skipping command registration for internal AltView: %s",
                    plugin_name,
                )
                return True

            self._plugin_classes[plugin_name] = plugin_class

            existing_command = self.command_registry.get_command(plugin_name)

            # The MCP AltView intentionally owns bare /mcp while preserving the
            # existing /mcp subcommands through the prior command handler.
            if plugin_name == "mcp" and existing_command is not None:
                self._command_delegates[plugin_name] = existing_command
                self.command_registry.unregister_command(plugin_name)
                logger.info(
                    "AltView command '%s' replacing existing command", plugin_name
                )

            # Skip if command already registered (e.g. by fullscreen_integrator)
            elif existing_command is not None:
                logger.debug(
                    "Skipping AltView command '%s' - already registered",
                    plugin_name,
                )
                return True

            delegate = self._command_delegates.get(plugin_name)
            command_def = CommandDefinition(
                name=plugin_name,
                aliases=(
                    getattr(delegate, "aliases", None)
                    or getattr(metadata, "aliases", None)
                    or []
                ),
                description=metadata.description,
                category=getattr(delegate, "category", CommandCategory.CUSTOM),
                mode=CommandMode.ALTVIEW,
                handler=self._create_plugin_handler(plugin_name),
                icon=getattr(metadata, "icon", ""),
                plugin_name="altview_integrator",
                cli_hidden=False,
                subcommands=getattr(delegate, "subcommands", None) or [],
            )

            success = self.command_registry.register_command(command_def)
            if not success:
                logger.error("Failed to register AltView command for %s", plugin_name)
                return False

            if getattr(metadata, "aliases", None):
                logger.debug(
                    "AltView command %s has aliases: %s", plugin_name, metadata.aliases
                )

            return True

        except Exception as e:
            logger.error(
                "Error registering AltView commands for %s: %s",
                plugin_class.__name__,
                e,
            )
            return False

    def _create_plugin_handler(self, plugin_name: str):
        """Create an async command handler for a plugin.

        The handler parses the session name from command args, gets or
        creates the plugin instance, optionally injects the app
        reference, and pushes the view onto the stack manager.

        Args:
            plugin_name: Name of the plugin this handler is for.

        Returns:
            Async command handler function.
        """

        async def handler(command):
            """Handle command execution for AltView plugin."""
            try:
                args = getattr(command, "args", None) or []
                delegate = self._command_delegates.get(plugin_name)
                if (
                    plugin_name == "mcp"
                    and delegate is not None
                    and args
                    and str(args[0]).lower() != "setup"
                ):
                    return await delegate.handler(command)

                stack_mgr = self._get_stack_manager()

                session_name = (
                    plugin_name
                    if plugin_name in self._single_session_plugins
                    else self._parse_session_name(command, plugin_name)
                )
                altview = self._get_or_create_altview(plugin_name, session_name)
                mcp_manager = None

                if hasattr(altview, "set_app") and self.app:
                    altview.set_app(self.app)

                if plugin_name == "mcp" and hasattr(altview, "set_config"):
                    mcp_manager = (
                        getattr(self.app, "mcp_manager", None) if self.app else None
                    )
                    if mcp_manager is None:
                        mcp_manager = MCPManager(mcp_dir=resolve_global_path("mcp"))
                    example_config = mcp_manager.load_example_config() or {}
                    current_config = mcp_manager.load_config() or {}
                    altview.set_config(example_config, current_config)
                    if hasattr(altview, "set_context"):
                        event_bus = (
                            getattr(self.app, "event_bus", None) if self.app else None
                        )
                        state_service = (
                            event_bus.get_service("state_service")
                            if event_bus and hasattr(event_bus, "get_service")
                            else None
                        )
                        llm_service = (
                            getattr(self.app, "llm_service", None) if self.app else None
                        )
                        mcp_integration = None
                        if self.app:
                            mcp_integration = getattr(
                                llm_service, "mcp_integration", None
                            ) or getattr(self.app, "mcp_integration", None)
                        altview.set_context(
                            mcp_manager=mcp_manager,
                            state_service=state_service,
                            mcp_integration=mcp_integration,
                            config_service=self.config,
                            event_bus=event_bus,
                            app=self.app,
                        )

                if hasattr(altview, "set_managers"):
                    altview.set_managers(self.config, self.profile_manager)

                await stack_mgr.push(altview, session_name)

                return CommandResult(
                    success=True,
                    message="",
                    display_type="success",
                )

            except Exception as e:
                logger.error("Error executing AltView plugin %s: %s", plugin_name, e)
                return CommandResult(
                    success=False,
                    message=f"AltView plugin error: {e}",
                    display_type="error",
                )

        return handler

    def _parse_session_name(self, command, plugin_name: str) -> str:
        """Extract session name from command args or generate one.

        If the command has arguments, the first argument is used as the
        session name. Otherwise a short hash based on the plugin name
        and current time is generated.

        Args:
            command: The parsed SlashCommand with args.
            plugin_name: Fallback plugin name for auto-generation.

        Returns:
            Session name string.
        """
        args = getattr(command, "args", None) or []
        if args:
            return str(args[0])

        # Auto-generate a unique session name
        short_hash = hashlib.sha256(
            f"{plugin_name}-{time.monotonic()}".encode()
        ).hexdigest()[:8]
        return f"{plugin_name}-{short_hash}"

    def _get_or_create_altview(self, plugin_name: str, session_name: str):
        """Get existing or create new AltView instance for a session.

        Instances are cached by session_name so that resuming a named
        session (e.g. ``/research my-topic``) returns the same view
        with its state intact.

        Args:
            plugin_name: Name of the plugin class to instantiate.
            session_name: Unique session identifier for caching.

        Returns:
            AltView plugin instance.

        Raises:
            ValueError: If plugin_name is not registered.
        """
        if session_name in self._plugin_instances:
            return self._plugin_instances[session_name]

        plugin_class = self._plugin_classes.get(plugin_name)
        if not plugin_class:
            raise ValueError(f"AltView plugin class not found: {plugin_name}")

        instance = plugin_class()
        self._plugin_instances[session_name] = instance
        logger.debug(
            "Created AltView instance: %s (session: %s)", plugin_name, session_name
        )
        return instance

    def unregister_plugin(self, plugin_name: str) -> bool:
        """Unregister a plugin and its commands.

        Also removes all cached session instances for this plugin.

        Args:
            plugin_name: Name of plugin to unregister.

        Returns:
            True if successful.
        """
        try:
            if plugin_name not in self._plugin_classes:
                return False

            plugin_class = self._plugin_classes[plugin_name]
            temp_instance = plugin_class()
            metadata = temp_instance.metadata

            self.command_registry.unregister_command(metadata.plugin_type)

            del self._plugin_classes[plugin_name]
            self._single_session_plugins.discard(plugin_name)

            # Remove all session instances for this plugin
            to_remove = [
                key
                for key, inst in self._plugin_instances.items()
                if type(inst) is plugin_class
            ]
            for key in to_remove:
                del self._plugin_instances[key]

            logger.info("Unregistered AltView plugin: %s", plugin_name)
            return True

        except Exception as e:
            logger.error("Error unregistering AltView plugin %s: %s", plugin_name, e)
            return False

    def get_registered_plugins(self) -> List[str]:
        """Get list of registered plugin names.

        Returns:
            List of plugin names.
        """
        return list(self._plugin_classes.keys())

    def reload_plugins(self, plugins_dir: Path) -> int:
        """Reload all AltView plugins from directory.

        Unregisters all current plugins and re-discovers from disk.

        Args:
            plugins_dir: Base plugins directory.

        Returns:
            Number of plugins reloaded.
        """
        for name in list(self._plugin_classes.keys()):
            self.unregister_plugin(name)

        return self.discover_and_register_plugins(plugins_dir)
