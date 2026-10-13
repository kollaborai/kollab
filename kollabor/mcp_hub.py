"""Authenticated stdio MCP server exposing only the ``hub_status`` tool."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Callable

MCP_PROTOCOL_VERSION = "2024-11-05"


def _status() -> dict[str, Any]:
    return {"status": "ok", "generation": os.environ.get("KOLLAB_ACP_GENERATION", "0")}


def _auth_valid(request: dict[str, Any], token: str) -> bool:
    params = request.get("params") or {}
    metadata = params.get("_meta") or request.get("_meta") or {}
    expected_generation = os.environ.get("KOLLAB_ACP_GENERATION")
    expected_socket = os.environ.get("KOLLAB_ACP_HUB_SOCKET")
    if metadata.get("token") != token:
        return False
    if expected_generation is not None and str(metadata.get("generation")) != expected_generation:
        return False
    if expected_socket is not None and metadata.get("socket") != expected_socket:
        return False
    return True


async def serve_hub_status(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    token: str,
    status: Callable[[], dict[str, Any]] = _status,
) -> None:
    """Serve MCP protocol on stdout; diagnostics never share the stream."""
    try:
        while line := await reader.readline():
            request: dict[str, Any] = {}
            try:
                request = json.loads(line)
                method = request.get("method")
                if method == "notifications/initialized":
                    continue
                if not _auth_valid(request, token):
                    raise PermissionError("invalid ACP hub token or binding")
                if method == "initialize":
                    result: dict[str, Any] = {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "kollab-hub", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [{
                            "name": "hub_status",
                            "description": "Return the current Kollab hub status.",
                            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                        }],
                    }
                elif method == "tools/call":
                    if (request.get("params") or {}).get("name") != "hub_status":
                        raise ValueError("unknown tool")
                    result = {
                        "content": [{"type": "text", "text": json.dumps(status(), separators=(",", ":"))}],
                        "isError": False,
                    }
                else:
                    raise ValueError("unsupported MCP method")
                response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
            except PermissionError as exc:
                response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32001, "message": str(exc)}}
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601, "message": str(exc)}}
            writer.write((json.dumps(response, separators=(",", ":")) + "\n").encode())
            await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (AttributeError, NotImplementedError):
            pass


async def _main_async(token: str) -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    await serve_hub_status(reader, writer, token=token)


def kollab_hub_argv() -> list[str]:
    """Return a source/wheel/binary-safe argv for launching this server."""
    from .binary import kollab_argv

    return [*kollab_argv(), "--mcp-hub"]


def main() -> None:
    token = os.environ.get("KOLLAB_ACP_HUB_TOKEN")
    if not token:
        print("KOLLAB_ACP_HUB_TOKEN is required", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_main_async(token))


if __name__ == "__main__":
    main()
