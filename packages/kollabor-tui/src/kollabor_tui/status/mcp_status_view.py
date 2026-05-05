"""MCP status view for displaying server and tool information."""

import logging
from typing import Any, Dict, List

from kollabor_tui.design_system import S

logger = logging.getLogger(__name__)


class MCPStatusView:
    """
    Status view for MCP (Model Context Protocol) integration.

    Displays connected MCP servers and their available tools
    in a formatted status panel.
    """

    def __init__(
        self,
        mcp_integration,
    ):
        """Initialize MCP status view.

        Args:
            mcp_integration: MCPIntegration instance
        """
        self._mcp_integration = mcp_integration

    def render(self) -> List[str]:
        """
        Render MCP status as plain text lines.

        Returns plain content -- the message display system (info_block)
        handles TagBox wrapping, so we must NOT wrap here too.

        Returns:
            List of plain text lines to display
        """
        lines = []

        # Get server information
        connections = self._mcp_integration.server_connections
        tool_registry = self._mcp_integration.tool_registry
        mcp_servers = self._mcp_integration.mcp_servers

        # Count statistics
        total_servers = len(mcp_servers)
        connected_servers = len(connections)
        total_tools = len(tool_registry)

        # Header
        lines.append(
            f"{S.BOLD}MCP SERVERS{S.RESET_BOLD}  "
            f"{connected_servers}/{total_servers} servers | {total_tools} tools"
        )

        # Server details
        if connections:
            # Group tools by server
            server_tools: Dict[str, List[str]] = {}
            for tool_name, tool_info in tool_registry.items():
                server_name = tool_info.get("server", "unknown")
                if server_name not in server_tools:
                    server_tools[server_name] = []
                server_tools[server_name].append(tool_name)

            for server_name, tools in sorted(server_tools.items()):
                connection = connections.get(server_name)
                if connection and connection.initialized:
                    tool_count = len(tools)
                    lines.append(f"+ {server_name}: {tool_count} tools")

                    for tool in sorted(tools):
                        lines.append(f"  - {tool}")

        elif total_servers == 0:
            lines.append("No MCP servers configured")
            lines.append("See: docs/mcp/MCP_SETUP.md")
        else:
            lines.append("No servers connected")
            lines.append("Check configuration")

        return lines


class MCPToolDetailView:
    """
    Detailed view for a specific MCP tool.

    Shows tool description, parameters, and server information.
    """

    def __init__(
        self,
        tool_name: str,
        tool_info: Dict[str, Any],
    ):
        """Initialize MCP tool detail view.

        Args:
            tool_name: Name of the tool
            tool_info: Tool information dictionary
        """
        self._tool_name = tool_name
        self._tool_info = tool_info

    def render(self) -> List[str]:
        """
        Render MCP tool detail as plain text lines.

        Returns plain content -- the message display system (info_block)
        handles TagBox wrapping, so we must NOT wrap here too.

        Returns:
            List of plain text lines to display
        """
        lines = []

        # Header
        server_name = self._tool_info.get("server", "unknown")
        lines.append(
            f"{S.BOLD}MCP TOOL{S.RESET_BOLD} {self._tool_name}  @ {server_name}"
        )

        # Tool description
        definition = self._tool_info.get("definition", {})
        description = definition.get("description", "No description")
        lines.append(description)

        # Tool parameters
        parameters = definition.get("parameters", {})
        if parameters and parameters.get("properties"):
            lines.append(f"{S.BOLD}Parameters:{S.RESET_BOLD}")

            for param_name, param_info in parameters.get("properties", {}).items():
                param_type = param_info.get("type", "unknown")
                param_desc = param_info.get("description", "")
                required = param_name in parameters.get("required", [])
                req_marker = "*" if required else ""

                lines.append(f"  {param_name}{req_marker}: {param_type}")
                if param_desc:
                    lines.append(f"    {param_desc}")

            if parameters.get("required"):
                lines.append("* = required")

        return lines


def render_mcp_status(mcp_integration) -> List[str]:
    """
    Convenience function to render MCP status.

    Args:
        mcp_integration: MCPIntegration instance

    Returns:
        List of formatted lines
    """
    view = MCPStatusView(mcp_integration)
    return view.render()


def render_mcp_tool_detail(tool_name: str, tool_info: Dict[str, Any]) -> List[str]:
    """
    Convenience function to render MCP tool detail.

    Args:
        tool_name: Name of the tool
        tool_info: Tool information dictionary

    Returns:
        List of formatted lines
    """
    view = MCPToolDetailView(tool_name, tool_info)
    return view.render()
