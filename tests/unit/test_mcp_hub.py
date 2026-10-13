import asyncio
import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_sdk_initialize_list_call_without_private_metadata():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kollabor.mcp_hub"],
        env={**os.environ, "KOLLAB_ACP_HUB_TOKEN": "current-token"},
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.protocolVersion
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["hub_status"]
            result = await session.call_tool("hub_status", {})
            assert result.isError is False
            assert "status" in result.content[0].text


@pytest.mark.asyncio
async def test_mcp_socket_binding_rechecks_current_generation(tmp_path, monkeypatch):
    socket_path = "/tmp/kollab-acp-hub-test.sock"
    current = {"valid": True}

    async def handler(reader, writer):
        await reader.readline()
        payload = {"type": "acp_hub_status"} if current["valid"] else {"type": "acp_hub_status", "error": "stale"}
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handler, path=socket_path)
    monkeypatch.setenv("KOLLAB_ACP_HUB_SOCKET", socket_path)
    monkeypatch.setenv("KOLLAB_ACP_HUB_TOKEN", "current")
    monkeypatch.setenv("KOLLAB_ACP_GENERATION", "3")
    from kollabor.mcp_hub import _binding_from_socket

    assert await _binding_from_socket() is True
    current["valid"] = False
    assert await _binding_from_socket() is False
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_mcp_server_binding_rejects_stale_daemon():
    from kollabor.mcp_hub import _require_binding

    with pytest.raises(PermissionError):
        await _require_binding(lambda: False)
