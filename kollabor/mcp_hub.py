"""Authenticated stdio MCP server exposing only the ``hub_status`` tool."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Callable

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

MCP_PROTOCOL_VERSION = "2024-11-05"


def _status() -> dict[str, Any]:
    return {"status": "ok", "generation": os.environ.get("KOLLAB_ACP_GENERATION", "0")}


async def _require_binding(binding: Callable[[], bool | Any]) -> None:
    result = binding()
    if hasattr(result, "__await__"):
        result = await result
    if not result:
        raise PermissionError("stale or unavailable ACP hub binding")


def _binding_from_environment() -> bool:
    # The hosting daemon should replace this with a current-generation socket
    # check. Presence of a token alone is intentionally not a client credential.
    return bool(os.environ.get("KOLLAB_ACP_HUB_TOKEN"))


async def _binding_from_socket() -> bool:
    socket_path = os.environ.get("KOLLAB_ACP_HUB_SOCKET")
    token = os.environ.get("KOLLAB_ACP_HUB_TOKEN")
    generation = os.environ.get("KOLLAB_ACP_GENERATION")
    if not socket_path or not token or generation is None:
        return _binding_from_environment()
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
        request = {"action": "acp_hub_status", "token": token, "generation": generation}
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        response = json.loads((await asyncio.wait_for(reader.readline(), 2)).decode())
        writer.close()
        await writer.wait_closed()
        return response.get("type") == "acp_hub_status" and "error" not in response
    except (OSError, asyncio.TimeoutError, ValueError):
        return False


def build_hub_server(
    *,
    status: Callable[[], dict[str, Any]] = _status,
    binding: Callable[[], bool | Any] = _binding_from_environment,
) -> Server:
    server = Server("kollab-hub", version="1")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [Tool(
            name="hub_status",
            description="Return the current Kollab hub status.",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        )]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        await _require_binding(binding)
        if name != "hub_status":
            raise ValueError(f"unknown tool: {name}")
        import json

        return [TextContent(type="text", text=json.dumps(status(), separators=(",", ":")))]

    return server


async def serve_hub_status(
    *,
    status: Callable[[], dict[str, Any]] = _status,
    binding: Callable[[], bool | Any] = _binding_from_environment,
) -> None:
    """Run the standard MCP stdio transport; stdout is protocol-only."""
    server = build_hub_server(status=status, binding=binding)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def kollab_hub_argv() -> list[str]:
    from .binary import kollab_argv

    return [*kollab_argv(), "--mcp-hub"]


def main() -> None:
    if not os.environ.get("KOLLAB_ACP_HUB_TOKEN"):
        print("KOLLAB_ACP_HUB_TOKEN is required", file=sys.stderr)
        raise SystemExit(2)
    anyio.run(lambda: serve_hub_status(binding=_binding_from_socket))


if __name__ == "__main__":
    main()
