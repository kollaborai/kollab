import asyncio
import json
import os
import sys
import tempfile
import uuid

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_sdk_live_parent_binding_revoke():
    current = {"valid": True}
    with tempfile.TemporaryDirectory(prefix="acp-", dir="/tmp") as directory:
        socket_path = os.path.join(directory, "hub.sock")

        async def parent(reader, writer):
            try:
                request = json.loads((await reader.readline()).decode())
                valid = current["valid"] and request.get("token") == "current" and request.get("generation") == "3"
                if valid:
                    payload = {"type": "acp_hub_status", "roster": [{"identity": "parent-agent"}]}
                else:
                    payload = {"type": "acp_hub_status", "error": "stale"}
                writer.write((json.dumps(payload) + "\n").encode())
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(parent, path=socket_path)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "kollabor.mcp_hub"],
            env={
                **os.environ,
                "KOLLAB_ACP_HUB_TOKEN": "current",
                "KOLLAB_ACP_HUB_SOCKET": socket_path,
                "KOLLAB_ACP_GENERATION": "3",
            },
        )
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool("hub_status", {})
                    assert "parent-agent" in result.content[0].text
                    current["valid"] = False
                    revoked = await session.call_tool("hub_status", {})
                    assert revoked.isError is True
        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_agent_socket_acp_hub_action_validates_current_binding(tmp_path):
    from plugins.hub.messenger import AgentSocketServer

    current = {"valid": True}

    async def callback(*, token, generation):
        if current["valid"] and token == "token" and generation == "2":
            return {"generation": 2, "roster": [{"identity": "agent"}]}
        return {"error": "stale"}

    socket_name = f"acp-{uuid.uuid4().hex}"
    server = AgentSocketServer("agent", lambda message: None, on_acp_hub_status=callback, socket_name=socket_name)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write((json.dumps({"action": "acp_hub_status", "token": "token", "generation": "2"}) + "\n").encode())
        await writer.drain()
        response = json.loads((await reader.readline()).decode())
        assert response["roster"][0]["identity"] == "agent"
        writer.close()
        await writer.wait_closed()
        current["valid"] = False
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write((json.dumps({"action": "acp_hub_status", "token": "token", "generation": "2"}) + "\n").encode())
        await writer.drain()
        assert "error" in json.loads((await reader.readline()).decode())
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_mcp_socket_binding_fails_closed_when_fields_missing(monkeypatch):
    from kollabor.mcp_hub import _binding_from_socket

    monkeypatch.delenv("KOLLAB_ACP_HUB_SOCKET", raising=False)
    monkeypatch.setenv("KOLLAB_ACP_HUB_TOKEN", "token")
    monkeypatch.setenv("KOLLAB_ACP_GENERATION", "1")
    assert await _binding_from_socket() is None


@pytest.mark.asyncio
async def test_mcp_server_binding_rejects_stale_daemon():
    from kollabor.mcp_hub import _require_binding

    with pytest.raises(PermissionError):
        await _require_binding(lambda: None)
