"""F1: the MCP schema ACP agents see must accept hub_spawn identity (#928)."""
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from kollabor import mcp_hub


@pytest.mark.asyncio
async def test_acp_hub_spawn_schema_accepts_identity():
    forwarded = {}

    async def call(name, arguments):
        forwarded.update(arguments)
        return "ok"

    server = mcp_hub.build_hub_server(binding=lambda: {"roster": []}, call=call)
    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
        spawn = next(t for t in listed.tools if t.name == "hub_spawn")
        result = await session.call_tool(
            "hub_spawn",
            {"name": "coder", "identity": "jasper", "task": "t", "llm": "codex-cli"},
        )
        assert "identity" in spawn.inputSchema["properties"]
        assert result.isError is False
        assert forwarded.get("identity") == "jasper"
