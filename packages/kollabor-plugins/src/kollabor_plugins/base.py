"""Base plugin class for Kollab plugins.

This module defines the BasePlugin class that all plugins can inherit from.
It provides default implementations for plugin lifecycle methods including
CLI argument registration.
"""

import argparse
from typing import Any, Optional


class BasePlugin:
    """Base class for all Kollab plugins.

    Plugins can inherit from this class to get default implementations
    of common plugin methods. The class provides static methods for
    CLI argument registration and early argument handling.
    """

    @staticmethod
    def register_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register custom CLI arguments.

        Called during early plugin discovery, before app initialization.
        Use parser.add_argument_group() to organize args by plugin.

        Args:
            parser: The ArgumentParser instance to add arguments to.

        Example:
            @staticmethod
            def register_cli_args(parser):
                group = parser.add_argument_group("My Plugin")
                group.add_argument("--my-arg", type=str,
                                  help="My custom argument")
        """
        pass

    @staticmethod
    def handle_early_args(args: argparse.Namespace) -> bool:
        """Handle args that should exit before app starts.

        Called after argument parsing but before app initialization.
        Return True to exit immediately (e.g., --capture mode).
        Return False to continue normal startup.

        Args:
            args: The parsed CLI arguments namespace.

        Returns:
            True to exit immediately, False to continue normal startup.

        Example:
            @staticmethod
            def handle_early_args(args):
                if hasattr(args, 'my_flag') and args.my_flag:
                    print("Early exit triggered")
                    return True
                return False
        """
        return False

    async def initialize(
        self, args: Optional[argparse.Namespace] = None, **kwargs
    ) -> None:
        """Initialize the plugin.

        Called during app startup. The args parameter contains
        parsed CLI arguments including any plugin-registered args.

        Note: This method is async and will be awaited by the application.

        Args:
            args: Parsed CLI arguments (optional, None if not available).
            **kwargs: Additional initialization parameters including:
                - event_bus: Event bus for hook registration
                - config: Configuration manager
                - command_registry: Command registry for slash commands
                - input_handler: Input handler instance
                - renderer: Terminal renderer
                - llm_service: LLM service instance
                - conversation_logger: Conversation logger instance
                - conversation_manager: Conversation manager instance
        """
        pass

    async def register_hooks(self) -> None:
        """Register plugin hooks with the event bus.

        Called during plugin initialization after initialize().
        Override this method to register event hooks.

        Note: This method is async and will be awaited by the application.
        """
        pass

    async def shutdown(self) -> None:
        """Shutdown the plugin.

        Called during app shutdown. Override this method to
        perform cleanup tasks.

        Note: This method is async and will be awaited by the application.
        Use this to properly clean up async resources (tasks, connections, etc).
        """
        pass

    @staticmethod
    def get_default_config() -> dict[str, Any]:
        """Get default configuration for this plugin.

        Returns:
            Dictionary with default configuration values.
        """
        return {}

    @staticmethod
    def get_startup_info(config) -> list[str]:
        """Get startup information for this plugin.

        Args:
            config: Configuration manager instance.

        Returns:
            List of strings to display during startup.
        """
        return []

    @staticmethod
    def get_config_widgets() -> dict[str, Any]:
        """Get configuration widgets for this plugin.

        Returns:
            Widget section definition for the config modal.
        """
        return {}
