"""A stdio MCP server whose tools/list response is deliberately delayed."""

import asyncio
import sys
from pathlib import Path

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool

server = Server("slow-discovery-test")
marker_dir = Path(sys.argv[1])
delay_seconds = float(sys.argv[2])
(marker_dir / "server-process-started").write_text("started", encoding="utf-8")


@server.list_tools()
async def list_tools() -> list[Tool]:
    (marker_dir / "tools-list-started").write_text("started", encoding="utf-8")
    await asyncio.sleep(delay_seconds)
    (marker_dir / "tools-list-finished").write_text("finished", encoding="utf-8")
    return [
        Tool(
            name="slow_mcp_probe",
            description="A tool exposed after delayed discovery.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        )
    ]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="slow-discovery-test",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
