"""MCP (Model Context Protocol) configuration management.

Provides pure business logic for MCP server management without UI dependencies.
This can be used by command handlers, plugins, or any code that needs to
manage MCP configuration.

Separated from mcp_command.py to enable reusability across the codebase.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_config.config_utils import resolve_global_path

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages MCP server configuration and state.

    Provides pure business logic for:
    - Loading/saving MCP configuration files
    - Enabling/disabling servers
    - Testing server connections
    - Listing servers and tools

    This class has NO UI dependencies and can be used from any context.
    """

    def __init__(
        self,
        mcp_dir: Optional[Path] = None,
        example_config_path: Optional[Path] = None,
    ):
        """Initialize MCP manager.

        Args:
            mcp_dir: Directory for MCP configuration (defaults to ~/.kollab/mcp)
            example_config_path: Path to example configuration file
        """
        self.mcp_dir = mcp_dir or resolve_global_path("mcp")
        self.config_path = self.mcp_dir / "mcp_settings.json"
        self.example_config_path = example_config_path or (
            Path.cwd() / "docs" / "mcp" / "mcp_settings.example.json"
        )

    def ensure_directory(self) -> None:
        """Ensure MCP configuration directory exists."""
        self.mcp_dir.mkdir(parents=True, exist_ok=True)

    def load_example_config(self) -> Optional[Dict]:
        """Load example configuration.

        Returns:
            Example config dict or None if not found
        """
        if not self.example_config_path.exists():
            logger.warning(f"Example config not found: {self.example_config_path}")
            return None

        try:
            with open(self.example_config_path, "r") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Error loading example config: {e}")
            return None

    def load_config(self) -> Optional[Dict]:
        """Load current MCP configuration.

        Returns:
            Current config dict or None if not exists
        """
        if not self.config_path.exists():
            return None

        try:
            with open(self.config_path, "r") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"Error loading MCP config: {e}")
            return None

    def save_config(self, config: Dict) -> None:
        """Save configuration to file.

        Args:
            config: Configuration dictionary to save

        Raises:
            IOError: If unable to write config file
        """
        self.ensure_directory()

        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=2)

        logger.info(f"MCP configuration saved to {self.config_path}")

    def get_server_status(self, server_name: str, mcp_integration) -> Dict[str, Any]:
        """Get status of a specific MCP server.

        Args:
            server_name: Name of the server
            mcp_integration: MCPIntegration instance

        Returns:
            Status dictionary with keys:
            - found: bool - whether server exists in config
            - enabled: bool - whether server is enabled
            - connected: bool - whether server is connected
            - tool_count: int - number of available tools
            - error: str - error message if any
        """
        config = self.load_config()
        if not config:
            return {"found": False, "error": "No configuration found"}

        servers = config.get("servers", {})
        if server_name not in servers:
            return {"found": False, "error": f"Server '{server_name}' not found"}

        server_config = servers[server_name]
        enabled = server_config.get("enabled", False)

        # Check connection status via mcp_integration
        connected = False
        tool_count = 0

        if mcp_integration:
            connection = mcp_integration.server_connections.get(server_name)
            connected = connection is not None and connection.initialized

            # Count tools from this server
            for tool_info in mcp_integration.tool_registry.values():
                if tool_info.get("server") == server_name:
                    tool_count += 1

        return {
            "found": True,
            "enabled": enabled,
            "connected": connected,
            "tool_count": tool_count,
        }

    def enable_server(self, server_name: str) -> Dict[str, Any]:
        """Enable a specific MCP server.

        Args:
            server_name: Name of the server to enable

        Returns:
            Result dictionary with keys:
            - success: bool - whether operation succeeded
            - error: str - error message if failed
        """
        config = self.load_config()
        if not config:
            return {"success": False, "error": "No MCP configuration found"}

        servers = config.get("servers", {})
        if server_name not in servers:
            return {"success": False, "error": f"Server '{server_name}' not found"}

        servers[server_name]["enabled"] = True
        self.save_config(config)

        logger.info(f"MCP server '{server_name}' enabled")
        return {"success": True}

    def disable_server(self, server_name: str) -> Dict[str, Any]:
        """Disable a specific MCP server.

        Args:
            server_name: Name of the server to disable

        Returns:
            Result dictionary with keys:
            - success: bool - whether operation succeeded
            - error: str - error message if failed
        """
        config = self.load_config()
        if not config:
            return {"success": False, "error": "No MCP configuration found"}

        servers = config.get("servers", {})
        if server_name not in servers:
            return {"success": False, "error": f"Server '{server_name}' not found"}

        servers[server_name]["enabled"] = False
        self.save_config(config)

        logger.info(f"MCP server '{server_name}' disabled")
        return {"success": True}

    def configure_server_keys(
        self,
        selected_servers: Dict[str, Dict],
        current_config: Dict,
        key_values: Dict[str, Dict[str, str]],
    ) -> Dict[str, Dict]:
        """Configure API keys for servers.

        This is the pure management logic - it takes key values
        and applies them to the server configuration.

        Args:
            selected_servers: Servers user selected
            current_config: Current configuration (for existing API keys)
            key_values: Dict of {server_name: {env_key: value}} with user-provided values

        Returns:
            Configured server dictionary
        """
        configured_servers = selected_servers.copy()

        for server_name, keys in key_values.items():
            if server_name in configured_servers:
                if "env" not in configured_servers[server_name]:
                    configured_servers[server_name]["env"] = {}

                # Apply user-provided keys
                for env_key, value in keys.items():
                    if value:  # Only set if user provided a value
                        configured_servers[server_name]["env"][env_key] = value

        return configured_servers

    def list_servers(self, mcp_integration) -> Dict[str, Any]:
        """List all MCP servers with their status.

        Args:
            mcp_integration: MCPIntegration instance

        Returns:
            Dictionary with keys:
            - servers: dict of {server_name: {enabled, connected, tool_count}}
            - total_servers: int
            - connected_servers: int
            - total_tools: int
        """
        config = self.load_config()
        servers = {}

        mcp_servers = mcp_integration.mcp_servers if mcp_integration else {}
        tool_registry = mcp_integration.tool_registry if mcp_integration else {}
        connections = mcp_integration.server_connections if mcp_integration else {}

        # Group tools by server
        server_tools: Dict[str, List[str]] = {}
        for tool_name, tool_info in tool_registry.items():
            server_name = tool_info.get("server", "unknown")
            if server_name not in server_tools:
                server_tools[server_name] = []
            server_tools[server_name].append(tool_name)

        # List each configured server
        for server_name, server_config in mcp_servers.items():
            connection = connections.get(server_name)
            connected = connection is not None and connection.initialized
            tools = server_tools.get(server_name, [])

            servers[server_name] = {
                "enabled": server_config.get("enabled", False),
                "connected": connected,
                "tool_count": len(tools),
                "tools": tools,
            }

        # Also include servers from config that aren't in mcp_servers
        if config:
            for server_name, server_config in config.get("servers", {}).items():
                if server_name not in servers:
                    servers[server_name] = {
                        "enabled": server_config.get("enabled", False),
                        "connected": False,
                        "tool_count": 0,
                        "tools": [],
                    }

        total_servers = len(servers)
        connected_servers = sum(1 for s in servers.values() if s["connected"])
        total_tools = sum(s["tool_count"] for s in servers.values())

        return {
            "servers": servers,
            "total_servers": total_servers,
            "connected_servers": connected_servers,
            "total_tools": total_tools,
        }

    def list_tools(
        self, mcp_integration, server_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """List MCP tools.

        Args:
            mcp_integration: MCPIntegration instance
            server_filter: Optional server name to filter by

        Returns:
            Dictionary with keys:
            - tools: dict of {server_name: [{name, description}]}
            - total_tools: int
        """
        tool_registry = mcp_integration.tool_registry if mcp_integration else {}
        server_tools: Dict[str, List[Dict]] = {}

        for tool_name, tool_info in tool_registry.items():
            server_name = tool_info.get("server", "unknown")
            if server_filter and server_name != server_filter:
                continue

            if server_name not in server_tools:
                server_tools[server_name] = []

            server_tools[server_name].append(
                {
                    "name": tool_name,
                    "description": tool_info.get("definition", {}).get(
                        "description", ""
                    ),
                }
            )

        total_tools = sum(len(tools) for tools in server_tools.values())

        return {
            "tools": server_tools,
            "total_tools": total_tools,
        }

    def get_servers_needing_keys(self, selected_servers: Dict[str, Dict]) -> List[str]:
        """Get list of servers that require API key configuration.

        Args:
            selected_servers: Servers user selected

        Returns:
            List of server names that need API keys
        """
        servers_needing_keys = []
        for name, config in selected_servers.items():
            if config.get("env"):
                servers_needing_keys.append(name)
        return servers_needing_keys

    def get_server_env_template(
        self, server_name: str, selected_servers: Dict[str, Dict]
    ) -> List[str]:
        """Get environment variable keys for a server.

        Args:
            server_name: Name of the server
            selected_servers: Selected server configurations

        Returns:
            List of environment variable keys
        """
        if server_name not in selected_servers:
            return []

        server_config = selected_servers[server_name]
        env_vars = server_config.get("env", {})
        return list(env_vars.keys())

    def get_existing_key_value(
        self, server_name: str, env_key: str, current_config: Dict
    ) -> Optional[str]:
        """Get existing API key value for a server.

        Args:
            server_name: Name of the server
            env_key: Environment variable key
            current_config: Current configuration

        Returns:
            Existing value or None
        """
        current_servers = current_config.get("servers", {})
        return current_servers.get(server_name, {}).get("env", {}).get(env_key, None)  # type: ignore[no-any-return]
