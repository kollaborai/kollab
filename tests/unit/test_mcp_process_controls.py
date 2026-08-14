"""Regression tests for process-local MCP safety controls."""

import asyncio
from types import SimpleNamespace

from kollabor_agent.mcp_integration import MCPIntegration


def _disabled_integration() -> MCPIntegration:
    integration = MCPIntegration.__new__(MCPIntegration)
    integration.event_bus = SimpleNamespace(
        config={"plugins": {"mcp": {"enabled": False}}}
    )
    integration.mcp_servers = {"stale": {"type": "stdio", "command": "bad"}}
    integration.tool_registry = {
        "repo_search": {
            "server": "stale",
            "enabled": True,
            "definition": {},
        }
    }
    integration.server_connections = {}
    integration.shutdown = _async_noop
    return integration


async def _async_noop() -> None:
    return None


def test_disabled_mcp_skips_discovery_and_schema_exposure():
    integration = _disabled_integration()

    assert asyncio.run(integration.discover_mcp_servers()) == {}
    assert integration.list_available_tools() == []
    assert integration.get_tool_definitions_for_api() == []


def test_disabled_mcp_rejects_calls_and_reload_without_connecting():
    integration = _disabled_integration()

    result = asyncio.run(integration.call_mcp_tool("repo_search", {}))
    assert result == {"error": "MCP is disabled for this process"}

    reload_result = asyncio.run(integration.reload_mcp_servers())
    assert reload_result == {
        "configured": 1,
        "discovered": 0,
        "reconnected": 0,
    }
