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

            # Skip internal plugins (they're invoked by other plugins, not the user)
            if getattr(metadata, "category", "") == "internal":
                self._plugin_classes[plugin_name] = plugin_class
                logger.debug(
                    "Skipping command registration for internal AltView: %s",
                    plugin_name,
                )
                return True

            self._plugin_classes[plugin_name] = plugin_class

            # Skip if command already registered (e.g. by fullscreen_integrator)
            if self.command_registry.get_command(plugin_name):
                logger.debug(
                    "Skipping AltView command '%s' - already registered",
                    plugin_name,
                )
                return True

            command_def = CommandDefinition(
                name=plugin_name,
                aliases=getattr(metadata, "aliases", None) or [],
                description=metadata.description,
                category=CommandCategory.CUSTOM,
                mode=CommandMode.ALTVIEW,
                handler=self._create_plugin_handler(plugin_name),
                icon=getattr(metadata, "icon", ""),
                plugin_name="altview_integrator",
                cli_hidden=False,
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
                stack_mgr = self._get_stack_manager()

                session_name = self._parse_session_name(command, plugin_name)
                altview = self._get_or_create_altview(plugin_name, session_name)
                mcp_manager = None

                if hasattr(altview, "set_app") and self.app:
                    altview.set_app(self.app)

                if plugin_name == "mcp-wizard" and hasattr(altview, "set_config"):
                    mcp_manager = (
                        getattr(self.app, "mcp_manager", None) if self.app else None
                    )
                    if mcp_manager is None:
                        mcp_manager = MCPManager(mcp_dir=resolve_global_path("mcp"))
                    example_config = mcp_manager.load_example_config() or {}
                    current_config = mcp_manager.load_config() or {}
                    altview.set_config(example_config, current_config)

                if hasattr(altview, "set_managers"):
                    altview.set_managers(self.config, self.profile_manager)

                await stack_mgr.push(altview, session_name)

                if plugin_name == "mcp-wizard":
                    selected_servers = getattr(altview, "selected_servers", {}) or {}
                    if selected_servers:
                        if mcp_manager is None:
                            mcp_manager = (
                                getattr(self.app, "mcp_manager", None)
                                if self.app
                                else None
                            )
                            if mcp_manager is None:
                                mcp_manager = MCPManager(
                                    mcp_dir=resolve_global_path("mcp")
                                )
                        current_config = mcp_manager.load_config() or {}
                        configured_servers = self._merge_existing_mcp_env(
                            selected_servers, current_config
                        )
                        new_config = {"servers": configured_servers}
                        mcp_manager.save_config(new_config)
                        server_count = len(selected_servers)
                        server_label = "server" if server_count == 1 else "servers"
                        missing_env = self._find_missing_mcp_env(configured_servers)
                        detail = ""
                        if missing_env:
                            detail = (
                                f" {len(missing_env)} environment value(s) still "
                                "need to be filled in before those servers can run."
                            )
                        return CommandResult(
                            success=True,
                            message=(
                                f"Configured {server_count} MCP {server_label} and "
                                f"saved to disk.{detail}"
                            ),
                            display_type="success",
                        )

                    return CommandResult(
                        success=True,
                        message="MCP wizard cancelled; no changes saved",
                        display_type="info",
                    )

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

    def _merge_existing_mcp_env(
        self,
        selected_servers: Dict[str, Dict],
        current_config: Dict,
    ) -> Dict[str, Dict]:
        """Preserve existing MCP env values when the AltView wizard saves.

        The AltView wizard only handles server selection; it does not
        collect API keys. When a user re-saves an already configured
        server, keep any existing env values instead of replacing them
        with example placeholders.
        """
        current_servers = current_config.get("servers", {})
        configured_servers: Dict[str, Dict] = {}

        for server_name, server_config in selected_servers.items():
            configured = dict(server_config)
            existing_env = current_servers.get(server_name, {}).get("env", {})
            if existing_env:
                env = dict(configured.get("env", {}))
                for key, existing_value in existing_env.items():
                    candidate = env.get(key)
                    if existing_value and (
                        candidate is None or str(candidate).endswith("-here")
                    ):
                        env[key] = existing_value
                configured["env"] = env
            configured_servers[server_name] = configured

        return configured_servers

    def _find_missing_mcp_env(self, configured_servers: Dict[str, Dict]) -> List[str]:
        """Return missing or placeholder env entries without exposing values."""
        missing: List[str] = []
        for server_name, server_config in configured_servers.items():
            env = server_config.get("env", {})
            if not isinstance(env, dict):
                continue
            for key, value in env.items():
                if not value or str(value).endswith("-here"):
                    missing.append(f"{server_name}.{key}")
        return missing

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
