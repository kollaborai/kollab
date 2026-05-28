"""MCP (Model Context Protocol) plugin.

Provides slash commands and status views for MCP server management:
- /mcp show - Display MCP status panel
- /mcp list - List all MCP servers and tools
- /mcp servers - Show connected servers
- /mcp tools - Show available tools
"""

import logging
from typing import Any, Dict, List

from kollabor_tui.visual_effects import AgnosterSegment

logger = logging.getLogger(__name__)


class MCPPlugin:
    """Plugin for MCP (Model Context Protocol) integration."""

    def __init__(
        self, name: str = "mcp", event_bus=None, renderer=None, config=None
    ) -> None:
        """Initialize the MCP plugin.

        Args:
            name: Plugin name.
            event_bus: Event bus for event handling.
            renderer: Terminal renderer.
            config: Configuration manager.
        """
        self.name = name
        self.version = "1.0.0"
        self.description = "MCP server management and status display"
        self.enabled = True
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self.logger = logger
        self.mcp_integration = None
        self.mcp_command_handler: Any = None

    async def initialize(self, event_bus, config, **kwargs) -> None:
        """Initialize the plugin.

        Args:
            event_bus: Event bus instance
            config: Configuration instance
            **kwargs: Additional kwargs (may include renderer, app, etc.)
        """
        try:
            self.event_bus = event_bus
            self.config = config
            self.renderer = kwargs.get("renderer")
            llm_service = kwargs.get("llm_service")
            command_registry = kwargs.get("command_registry")

            # Get MCP integration from llm_service if available
            if llm_service and hasattr(llm_service, "mcp_integration"):
                self.mcp_integration = llm_service.mcp_integration

            # Register slash commands
            await self._register_commands(command_registry)

            self.logger.info("MCP plugin initialized successfully")
        except Exception as e:
            self.logger.error(f"Error initializing MCP plugin: {e}")
            raise

    async def _register_commands(self, command_registry) -> None:
        """Register MCP slash commands.

        Args:
            command_registry: Command registry instance from kwargs
        """
        try:
            from kollabor.commands.mcp_command import register_mcp_commands

            if command_registry:
                existing = command_registry.get_command("mcp")
                if existing and getattr(existing, "plugin_name", "") == "altview_integrator":
                    self.logger.info("MCP command already owned by AltView manager")
                    return

                # Register commands even without MCP integration - users need /mcp
                # Phase 4.5 step 10: pass event_bus via a lightweight app-like
                # object so _get_state_service can look up state_service. The
                # previous `app=None` prevented state_service access and broke
                # /mcp tools after phase 4.5 step 7 stripped its fallback.
                class _McpAppProxy:
                    def __init__(self, event_bus):
                        self.event_bus = event_bus

                self.mcp_command_handler = register_mcp_commands(
                    command_registry=command_registry,
                    mcp_integration=self.mcp_integration,  # Can be None
                    renderer=self.renderer,
                    app=_McpAppProxy(self.event_bus),
                )
                self.logger.info("MCP commands registered")
            else:
                self.logger.warning(
                    "Command registry not found, skipping MCP command registration"
                )

        except Exception as e:
            self.logger.error(f"Failed to register MCP commands: {e}")

    def _get_status_content(self) -> List[str]:
        """Get MCP status content for status bar display.

        Returns:
            List of formatted status lines
        """
        try:
            if not self.mcp_integration:
                seg = AgnosterSegment()
                seg.add_neutral("MCP: N/A", "dark")
                return [seg.render()]

            connections = self.mcp_integration.server_connections
            tool_registry = self.mcp_integration.tool_registry

            # Count connected servers and tools
            connected_count = len([c for c in connections.values() if c.initialized])
            tool_count = len(tool_registry)

            seg = AgnosterSegment()

            if connected_count > 0:
                seg.add_lime("MCP", "dark")
                seg.add_cyan(f"{connected_count} srv", "dark")
                seg.add_neutral(f"{tool_count} tools", "mid")
            else:
                seg.add_neutral("MCP: offline", "dark")

            return [seg.render()]

        except Exception as e:
            self.logger.error(f"Error getting MCP status content: {e}")
            seg = AgnosterSegment()
            seg.add_neutral("MCP: Error", "dark")
            return [seg.render()]

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        self.logger.info("MCP plugin shutdown completed")

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Get default configuration.

        Returns:
            Default configuration dictionary
        """
        return {
            "plugins": {
                "mcp": {
                    "enabled": True,
                    "status_display": True,
                    "commands": {"enabled": True},
                    "auto_grant_mcp_tools": True,
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "MCP Integration",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.mcp.enabled",
                    "help": "Enable the MCP plugin",
                },
                {
                    "type": "checkbox",
                    "label": "Status Display",
                    "config_path": "plugins.mcp.status_display",
                    "help": "Show MCP status in the status bar",
                },
                {
                    "type": "checkbox",
                    "label": "Commands Enabled",
                    "config_path": "plugins.mcp.commands.enabled",
                    "help": "Enable /mcp slash commands",
                },
                {
                    "type": "checkbox",
                    "label": "Auto Grant MCP Tools",
                    "config_path": "plugins.mcp.auto_grant_mcp_tools",
                    "help": (
                        "When an MCP server connects mid-session, fire "
                        "per-tool grant notifications to the agent. "
                        "Boot-time connects are always skipped (tools are "
                        "in the initial system prompt)."
                    ),
                },
            ],
        }
