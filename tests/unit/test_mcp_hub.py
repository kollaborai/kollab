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
async def test_mcp_server_binding_rejects_stale_daemon():
    from kollabor.mcp_hub import _require_binding

    with pytest.raises(PermissionError):
        _require_binding(lambda: False)
