"""Full-screen manager for plugin coordination and modal integration."""

import asyncio


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

import logging
from typing import Any, Dict, List, Optional

from kollabor_events.models import EventType

from .plugin import FullScreenPlugin, PluginMetadata
from .session import FullScreenSession

logger = logging.getLogger(__name__)


class FullScreenManager:
    """Manages full-screen plugins and their integration with the modal system.

    This class coordinates between plugins, the event bus, and the modal system
    to provide seamless full-screen experiences that properly pause the main
    application interface.
    """

    def __init__(self, event_bus, terminal_renderer):
        """Initialize the full-screen manager.

        Args:
            event_bus: Event bus for modal system integration.
            terminal_renderer: Main terminal renderer (for compatibility).
        """
        self.event_bus = event_bus
        self.terminal_renderer = terminal_renderer

        # Plugin registry
        self.plugins: Dict[str, FullScreenPlugin] = {}
        self.plugin_aliases: Dict[str, str] = {}

        # Session management
        self.current_session: Optional[FullScreenSession] = None
        self.session_history: List[Dict[str, Any]] = []

        logger.info("FullScreenManager initialized")

    def register_plugin(self, plugin: FullScreenPlugin) -> bool:
        """Register a full-screen plugin.

        Args:
            plugin: The plugin to register.

        Returns:
            True if registration was successful, False otherwise.
        """
        try:
            if plugin.name in self.plugins:
                logger.warning(f"Plugin {plugin.name} already registered, overriding")

            # Register main name
            self.plugins[plugin.name] = plugin

            # Register aliases
            for alias in plugin.metadata.aliases:
                if alias in self.plugin_aliases:
                    logger.warning(f"Alias {alias} already registered, overriding")
                self.plugin_aliases[alias] = plugin.name

            logger.info(
                f"Registered plugin: {plugin.name} with {len(plugin.metadata.aliases)} aliases"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to register plugin {plugin.name}: {e}")
            return False

    def unregister_plugin(self, name: str) -> bool:
        """Unregister a plugin.

        Args:
            name: Name of the plugin to unregister.

        Returns:
            True if unregistration was successful, False otherwise.
        """
        try:
            if name not in self.plugins:
                logger.warning(f"Plugin {name} not found for unregistration")
                return False

            plugin = self.plugins[name]

            # Remove aliases
            for alias in plugin.metadata.aliases:
                if alias in self.plugin_aliases:
                    del self.plugin_aliases[alias]

            # Remove main registration
            del self.plugins[name]

            logger.info(f"Unregistered plugin: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to unregister plugin {name}: {e}")
            return False

    def get_plugin(self, name: str) -> Optional[FullScreenPlugin]:
        """Get a plugin by name or alias.

        Args:
            name: Plugin name or alias.

        Returns:
            Plugin instance if found, None otherwise.
        """
        # Check direct name first
        if name in self.plugins:
            return self.plugins[name]

        # Check aliases
        if name in self.plugin_aliases:
            actual_name = self.plugin_aliases[name]
            return self.plugins.get(actual_name)

        return None

    def list_plugins(self) -> List[PluginMetadata]:
        """List all registered plugins.

        Returns:
            List of plugin metadata.
        """
        return [plugin.metadata for plugin in self.plugins.values()]

    async def launch_plugin(self, name: str, **kwargs) -> bool:
        """Launch a full-screen plugin with modal integration.

        Args:
            name: Plugin name or alias to launch.
            **kwargs: Additional arguments for plugin configuration.

        Returns:
            True if plugin launched successfully, False otherwise.
        """
        try:
            # Check if session is already active
            if self.current_session and self.current_session.running:
                logger.warning("Cannot launch plugin - session already active")
                return False

            # Get plugin
            plugin = self.get_plugin(name)
            if not plugin:
                logger.error(f"Plugin not found: {name}")
                return False

            logger.info(f"Launching full-screen plugin: {plugin.name}")

            # Enter modal mode using the proven modal system
            await self._enter_modal_mode(plugin)

            try:
                # Create and run session
                logger.info(f"Creating session for plugin: {plugin.name}")
                self.current_session = FullScreenSession(
                    plugin, self.event_bus, **kwargs
                )
                logger.info(f"Running session for plugin: {plugin.name}")
                success = await self.current_session.run()
                logger.info(f"Session completed with success: {success}")

                # Record session in history
                self._record_session(plugin, success)

                return success

            finally:
                # Always exit modal mode
                await self._exit_modal_mode(plugin)
                self.current_session = None

        except Exception as e:
            logger.error(f"Failed to launch plugin {name}: {e}")
            return False

    async def stop_current_session(self) -> bool:
        """Stop the currently running session.

        Returns:
            True if session was stopped, False if no session active.
        """
        if not self.current_session:
            return False

        try:
            self.current_session.stop()
            return True
        except Exception as e:
            logger.error(f"Error stopping session: {e}")
            return False

    def is_session_active(self) -> bool:
        """Check if a full-screen session is currently active.

        Returns:
            True if session is active, False otherwise.
        """
        return self.current_session is not None and self.current_session.running

    def get_current_plugin(self) -> Optional[FullScreenPlugin]:
        """Get the currently running plugin.

        Returns:
            Current plugin if session is active, None otherwise.
        """
        if self.current_session:
            return self.current_session.plugin
        return None

    async def _enter_modal_mode(self, plugin: FullScreenPlugin):
        """Enter modal mode for full-screen plugin.

        Uses the same modal system as /config command for consistent behavior.

        Args:
            plugin: The plugin being launched.
        """
        try:
            # Emit MODAL_TRIGGER event (same as Matrix effect)
            await self.event_bus.emit_with_hooks(
                EventType.MODAL_TRIGGER,
                {
                    "trigger_source": "fullscreen_plugin",
                    "plugin_name": plugin.name,
                    "mode": "fullscreen",
                    "fullscreen_plugin": True,
                },
                "fullscreen_manager",
            )

            # Emit MODAL_SHOW for config hooks
            await self.event_bus.emit_with_hooks(
                EventType.MODAL_SHOW,
                {
                    "type": "fullscreen_plugin",
                    "action": plugin.name,
                    "source": "fullscreen_manager",
                },
                "fullscreen_manager",
            )
            logger.info(f"Entered modal mode for plugin: {plugin.name}")

        except Exception as e:
            logger.error(f"Failed to enter modal mode for {plugin.name}: {e}")
            raise

    async def _exit_modal_mode(self, plugin: FullScreenPlugin):
        """Exit modal mode after plugin finishes.

        Args:
            plugin: The plugin that finished.
        """
        try:
            # Emit MODAL_HIDE event
            await self.event_bus.emit_with_hooks(
                EventType.MODAL_HIDE,
                {
                    "source": "fullscreen_plugin",
                    "plugin_name": plugin.name,
                    "completed": True,
                },
                "fullscreen_manager",
            )
            logger.info(f"🔄 Exited modal mode for plugin: {plugin.name}")

        except Exception as e:
            logger.error(f"Failed to exit modal mode for {plugin.name}: {e}")

    def _record_session(self, plugin: FullScreenPlugin, success: bool):
        """Record session in history.

        Args:
            plugin: The plugin that ran.
            success: Whether the session completed successfully.
        """
        session_record = {
            "plugin_name": plugin.name,
            "success": success,
            "stats": self.current_session.get_stats() if self.current_session else None,
            "timestamp": _get_loop().time(),
        }
        self.session_history.append(session_record)

        # Keep only last 100 sessions
        if len(self.session_history) > 100:
            self.session_history = self.session_history[-100:]

    def get_session_history(self) -> List[Dict[str, Any]]:
        """Get session execution history.

        Returns:
            List of session records.
        """
        return self.session_history.copy()

    def get_manager_stats(self) -> Dict[str, Any]:
        """Get manager statistics.

        Returns:
            Dictionary with manager statistics.
        """
        current_plugin = self.get_current_plugin()
        return {
            "plugins_registered": len(self.plugins),
            "aliases_registered": len(self.plugin_aliases),
            "session_active": self.is_session_active(),
            "current_plugin": current_plugin.name if current_plugin else None,
            "total_sessions": len(self.session_history),
            "successful_sessions": sum(1 for s in self.session_history if s["success"]),
        }
