import asyncio
import json
import os
import sys
import tempfile

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
async def test_mcp_server_binding_rejects_stale_daemon():
    from kollabor.mcp_hub import _require_binding

    with pytest.raises(PermissionError):
        await _require_binding(lambda: None)
