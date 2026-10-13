"""Minimal authenticated stdio MCP-style hub_status server for ACP adapters."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Callable


def _status() -> dict[str, Any]:
    return {"status": "ok", "generation": os.environ.get("KOLLAB_ACP_GENERATION", "0")}


async def serve_hub_status(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    token: str,
    status: Callable[[], dict[str, Any]] = _status,
) -> None:
    """Serve protocol-only stdout and diagnostics on stderr."""
    try:
        while line := await reader.readline():
            request: dict[str, Any] = {}
            try:
                request = json.loads(line)
                auth = request.get("auth")
                expected_generation = os.environ.get("KOLLAB_ACP_GENERATION")
                expected_socket = os.environ.get("KOLLAB_ACP_HUB_SOCKET")
                if isinstance(auth, dict):
                    valid = auth.get("token") == token
                    if expected_generation is not None:
                        valid = valid and str(auth.get("generation")) == expected_generation
                    if expected_socket is not None:
                        valid = valid and auth.get("socket") == expected_socket
                else:
                    valid = auth == token and expected_generation is None and expected_socket is None
                if not valid:
                    raise PermissionError("invalid ACP hub token or binding")
                method = request.get("method")
                if method != "hub_status":
                    raise ValueError("unsupported method")
                result = status()
                response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id") if isinstance(locals().get("request"), dict) else None,
                    "error": {"code": -32001, "message": str(exc)},
                }
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


def main() -> None:
    token = os.environ.get("KOLLAB_ACP_HUB_TOKEN")
    if not token:
        print("KOLLAB_ACP_HUB_TOKEN is required", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_main_async(token))


if __name__ == "__main__":
    main()


def kollab_hub_argv() -> list[str]:
    """Return a source/wheel/binary-safe argv for launching this server."""
    from .binary import kollab_argv

    return [*kollab_argv(), "--mcp-hub"]
