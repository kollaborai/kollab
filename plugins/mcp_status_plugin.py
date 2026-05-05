"""MCP Status Plugin for monitoring Model Context Protocol servers.

This plugin provides real-time status monitoring for MCP server connections,
displaying connection status, tool counts, and server health information in
the terminal UI status areas.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from kollabor_events import Event, EventType, Hook, HookPriority  # noqa: E402

logger = logging.getLogger(__name__)


class MCPStatusPlugin:
    """Plugin for monitoring and displaying MCP server status.

    Tracks MCP server connections, tool availability, and errors,
    displaying this information in the terminal UI status areas.
    """

    def __init__(self, name: str, event_bus, renderer, config) -> None:
        """Initialize the MCP status plugin.

        Args:
            name: Plugin name.
            event_bus: Event bus for hook registration.
            renderer: Terminal renderer.
            config: Configuration manager.
        """
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config

        # MCP server tracking
        self.mcp_servers: Dict[str, Any] = {}  # server_name -> server_info
        self.connected_servers = 0
        self.total_tools = 0
        self.error_count = 0
        self.connecting_count = 0

        # Status cache for display
        self.last_status = "Initializing"

        logger.info(f"MCP Status Plugin initialized: {name}")

    def get_status_lines(self) -> Dict[str, List[str]]:
        """Get status lines for the MCP status plugin organized by area.

        Returns:
            Dictionary with status lines organized by areas A, B, C.
        """
        # Check if status display is enabled
        show_status = self.config.get("plugins.mcp_status.show_status", True)
        if not show_status:
            return {"A": [], "B": [], "C": []}

        enabled = self.config.get("plugins.mcp_status.enabled", True)

        if not enabled:
            return {"A": [], "B": [], "C": []}

        # Build status lines for area B (system monitoring)
        status_lines = []

        if self.connected_servers > 0:
            if self.error_count > 0:
                status_lines.append(
                    f"MCP: {self.connected_servers} servers, {self.error_count} error(s)"
                )
            else:
                status_lines.append(
                    f"MCP: {self.connected_servers} servers, {self.total_tools} tools"
                )
        elif self.connecting_count > 0:
            status_lines.append(f"MCP: Connecting ({self.connecting_count} servers)")
        elif len(self.mcp_servers) > 0:
            status_lines.append(f"MCP: {len(self.mcp_servers)} servers, not connected")
        else:
            # No MCP servers configured
            status_lines.append("")

        return {
            "A": [],  # No area A content
            "B": status_lines,  # MCP status in area B
            "C": [],  # No area C content
        }

    async def initialize(self) -> None:
        """Initialize the MCP status plugin."""
        logger.info("Starting MCP Status Plugin initialization...")

        # Initialize from config
        self.enabled = self.config.get("plugins.mcp_status.enabled", True)
        self.show_errors = self.config.get("plugins.mcp_status.show_errors", True)

        logger.info("MCP Status Plugin initialization complete")

    async def register_hooks(self) -> None:
        """Register MCP status plugin hooks for MCP lifecycle events."""
        if not self.config.get("plugins.mcp_status.enabled", True):
            logger.info("MCP status plugin disabled, not registering hooks")
            return

        logger.info("MCP Status: Registering hooks for MCP lifecycle monitoring")

        # Register hooks for all MCP events
        hooks = [
            # Server lifecycle
            Hook(
                name="mcp_status_server_connect",
                plugin_name=self.name,
                event_type=EventType.MCP_SERVER_CONNECT,
                priority=HookPriority.POSTPROCESSING.value,
                callback=self._on_server_connect,
            ),
            Hook(
                name="mcp_status_server_connected",
                plugin_name=self.name,
                event_type=EventType.MCP_SERVER_CONNECTED,
                priority=HookPriority.POSTPROCESSING.value,
                callback=self._on_server_connected,
            ),
            Hook(
                name="mcp_status_server_disconnect",
                plugin_name=self.name,
                event_type=EventType.MCP_SERVER_DISCONNECT,
                priority=HookPriority.POSTPROCESSING.value,
                callback=self._on_server_disconnect,
            ),
            Hook(
                name="mcp_status_server_error",
                plugin_name=self.name,
                event_type=EventType.MCP_SERVER_ERROR,
                priority=HookPriority.POSTPROCESSING.value,
                callback=self._on_server_error,
            ),
            Hook(
                name="mcp_status_server_discovered",
                plugin_name=self.name,
                event_type=EventType.MCP_SERVER_DISCOVERED,
                priority=HookPriority.POSTPROCESSING.value,
                callback=self._on_server_discovered,
            ),
            # Tool registration
            Hook(
                name="mcp_status_tool_register",
                plugin_name=self.name,
                event_type=EventType.MCP_TOOL_REGISTER,
                priority=HookPriority.POSTPROCESSING.value,
                callback=self._on_tool_register,
            ),
            Hook(
                name="mcp_status_tool_unregister",
                plugin_name=self.name,
                event_type=EventType.MCP_TOOL_UNREGISTER,
                priority=HookPriority.POSTPROCESSING.value,
                callback=self._on_tool_unregister,
            ),
        ]

        for hook in hooks:
            try:
                await self.event_bus.register_hook(hook)
                logger.debug(
                    f"MCP Status: Registered {hook.name} for {getattr(hook.event_type, 'value', hook.event_type)}"
                )
            except Exception as e:
                logger.error(f"MCP Status: Failed to register {hook.name}: {e}")

        logger.info("MCP Status: Hook registration complete - monitoring active")

    async def shutdown(self) -> None:
        """Shutdown the MCP status plugin."""
        logger.info(
            f"MCP Status: Shutting down - tracked {len(self.mcp_servers)} servers"
        )

    # ========================================================================
    # MCP EVENT HANDLERS
    # ========================================================================

    def _validate_event_data(self, data: Any, event_type: str) -> Dict[str, Any]:
        """Validate and normalize event data.

        Args:
            data: Event data (should be dict, but may be malformed)
            event_type: Type of event for error messages

        Returns:
            Validated dict, or empty dict if data is invalid
        """
        if isinstance(data, dict):
            return data

        # Handle malformed data
        logger.error(
            f"MCP Status: Invalid data type for {event_type} event: "
            f"expected dict, got {type(data).__name__}. "
            f"Data value: {data!r}"
        )

        # Try to recover if data is a JSON string
        if isinstance(data, str):
            try:
                import json

                result: Dict[str, Any] = json.loads(data)
                return result
            except json.JSONDecodeError:
                logger.error("MCP Status: Could not recover - data is not valid JSON")

        # Return empty dict as fallback
        return {}

    async def _on_server_connect(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle MCP server connection initiated."""
        data = self._validate_event_data(data, "MCP_SERVER_CONNECT")
        server_name = data.get("server_name", "unknown")
        logger.debug(f"MCP Status: Server connecting - {server_name}")

        # Update tracking
        self.connecting_count += 1
        self.last_status = "Connecting"

        # Add to server tracking if not exists
        if server_name not in self.mcp_servers:
            self.mcp_servers[server_name] = {
                "status": "connecting",
                "tools": [],
                "error": None,
            }

        return {"status": "monitored"}

    async def _on_server_connected(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle MCP server successfully connected."""
        data = self._validate_event_data(data, "MCP_SERVER_CONNECTED")
        server_name = data.get("server_name", "unknown")
        tools = data.get("tools", [])

        logger.debug(
            f"MCP Status: Server connected - {server_name} with {len(tools)} tools"
        )

        # Update tracking
        if self.connecting_count > 0:
            self.connecting_count -= 1

        self.connected_servers += 1
        self.last_status = "Connected"

        # Update server info
        if server_name in self.mcp_servers:
            self.mcp_servers[server_name]["status"] = "connected"
            self.mcp_servers[server_name]["tools"] = tools
        else:
            self.mcp_servers[server_name] = {
                "status": "connected",
                "tools": tools,
                "error": None,
            }

        # Update tool count
        self._recalculate_tool_count()

        return {"status": "monitored"}

    async def _on_server_disconnect(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle MCP server disconnected."""
        data = self._validate_event_data(data, "MCP_SERVER_DISCONNECT")
        server_name = data.get("server_name", "unknown")
        logger.debug(f"MCP Status: Server disconnected - {server_name}")

        # Update tracking
        if self.connected_servers > 0:
            self.connected_servers -= 1

        if server_name in self.mcp_servers:
            self.mcp_servers[server_name]["status"] = "disconnected"

        self.last_status = "Disconnected"
        self._recalculate_tool_count()

        return {"status": "monitored"}

    async def _on_server_error(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle MCP server connection error."""
        data = self._validate_event_data(data, "MCP_SERVER_ERROR")
        server_name = data.get("server_name", "unknown")
        error = data.get("error", "Unknown error")

        logger.warning(f"MCP Status: Server error - {server_name}: {error}")

        # Update tracking
        if self.connecting_count > 0:
            self.connecting_count -= 1

        self.error_count += 1
        self.last_status = "Error"

        # Update server info
        if server_name in self.mcp_servers:
            self.mcp_servers[server_name]["status"] = "error"
            self.mcp_servers[server_name]["error"] = error
        else:
            self.mcp_servers[server_name] = {
                "status": "error",
                "tools": [],
                "error": error,
            }

        return {"status": "monitored"}

    async def _on_server_discovered(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle MCP server discovered (from config)."""
        data = self._validate_event_data(data, "MCP_SERVER_DISCOVERED")
        servers = data.get("servers", {})
        logger.debug(f"MCP Status: Discovered {len(servers)} MCP servers from config")

        # Track all discovered servers (servers is a dict keyed by server name)
        for server_name, server_info in servers.items():
            if server_name not in self.mcp_servers:
                self.mcp_servers[server_name] = {
                    "status": server_info.get("status", "discovered"),
                    "tools": server_info.get("tools", []),
                    "error": None,
                }

        return {"status": "monitored"}

    async def _on_tool_register(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle MCP tool registered."""
        data = self._validate_event_data(data, "MCP_TOOL_REGISTER")
        tool_name = data.get("tool_name", "unknown")
        server_name = data.get("server_name", "unknown")

        logger.debug(f"MCP Status: Tool registered - {tool_name} from {server_name}")

        # Add tool to server tracking
        if server_name in self.mcp_servers:
            if tool_name not in self.mcp_servers[server_name]["tools"]:
                self.mcp_servers[server_name]["tools"].append(tool_name)

        self._recalculate_tool_count()

        return {"status": "monitored"}

    async def _on_tool_unregister(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle MCP tool unregistered."""
        data = self._validate_event_data(data, "MCP_TOOL_UNREGISTER")
        tool_name = data.get("tool_name", "unknown")
        server_name = data.get("server_name", "unknown")

        logger.debug(f"MCP Status: Tool unregistered - {tool_name} from {server_name}")

        # Remove tool from server tracking
        if server_name in self.mcp_servers:
            if tool_name in self.mcp_servers[server_name]["tools"]:
                self.mcp_servers[server_name]["tools"].remove(tool_name)

        self._recalculate_tool_count()

        return {"status": "monitored"}

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _recalculate_tool_count(self) -> None:
        """Recalculate total tool count from all connected servers."""
        total = 0
        for server_name, server_info in self.mcp_servers.items():
            if server_info["status"] == "connected":
                total += len(server_info["tools"])

        self.total_tools = total

    # ========================================================================
    # CONFIGURATION
    # ========================================================================

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Get default configuration for MCP status plugin."""
        return {
            "plugins": {
                "mcp_status": {
                    "enabled": True,
                    "show_status": True,
                    "show_errors": True,
                    "update_interval": 1,  # seconds
                }
            }
        }

    @staticmethod
    def get_startup_info(config) -> List[str]:
        """Get startup information for MCP status plugin.

        Args:
            config: Configuration manager instance.

        Returns:
            List of strings to display during startup.
        """
        return [
            f"MCP Status: {config.get('plugins.mcp_status.enabled')}",
            f"Show Status: {config.get('plugins.mcp_status.show_status')}",
        ]

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        """Get configuration widgets for MCP status plugin.

        Returns:
            Widget section definition for the config modal.
        """
        return {
            "title": "MCP Status Plugin",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enable MCP Status",
                    "config_path": "plugins.mcp_status.enabled",
                    "help": "Monitor and display MCP server status",
                },
                {
                    "type": "checkbox",
                    "label": "Show Status",
                    "config_path": "plugins.mcp_status.show_status",
                    "help": "Display MCP status in terminal UI",
                },
                {
                    "type": "checkbox",
                    "label": "Show Errors",
                    "config_path": "plugins.mcp_status.show_errors",
                    "help": "Show MCP connection errors in status",
                },
            ],
        }
