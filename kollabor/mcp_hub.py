"""Harness-facing MCP tools, authenticated against the owning Hub daemon."""
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

from kollabor_config import local_ipc

MCP_PROTOCOL_VERSION = "2024-11-05"


def _status() -> dict[str, Any]:
    return {"status": "ok", "generation": os.environ.get("KOLLAB_ACP_GENERATION", "0")}


async def _require_binding(binding: Callable[[], bool | Any]) -> Any:
    result = binding()
    if hasattr(result, "__await__"):
        result = await result
    if not result:
        raise PermissionError("stale or unavailable ACP hub binding")
    return result


async def _binding_from_socket() -> dict[str, Any] | None:
    socket_path = os.environ.get("KOLLAB_ACP_HUB_SOCKET")
    token = os.environ.get("KOLLAB_ACP_HUB_TOKEN")
    generation = os.environ.get("KOLLAB_ACP_GENERATION")
    if not socket_path or not token or generation is None:
        return None
    writer = None
    try:
        reader, writer = await local_ipc.connect(socket_path)
        request = {"action": "acp_hub_status", "token": token, "generation": generation}
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        response = json.loads((await asyncio.wait_for(reader.readline(), 2)).decode())
        if response.get("type") != "acp_hub_status" or "error" in response:
            return None
        return response
    except (OSError, asyncio.TimeoutError, ValueError):
        return None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass


async def _call_socket(name: str, arguments: dict[str, Any]) -> Any:
    socket_path = os.environ.get("KOLLAB_ACP_HUB_SOCKET", "")
    reader, writer = await local_ipc.connect(socket_path)
    try:
        request = {
            "action": "acp_hub_call", "name": name, "arguments": arguments,
            "token": os.environ.get("KOLLAB_ACP_HUB_TOKEN"),
            "generation": os.environ.get("KOLLAB_ACP_GENERATION"),
        }
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        timeout = min(600, max(1, float(arguments.get("timeout", 300)))) + 10 if name == "hub_ask" else 30
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout))
        if response.get("type") != "acp_hub_call" or response.get("error"):
            raise RuntimeError(response.get("error") or "invalid Hub tool response")
        return response.get("result")
    finally:
        writer.close()
        await writer.wait_closed()


def _tools() -> list[Tool]:
    def tool(name: str, description: str, properties: dict[str, Any], required: list[str] = []) -> Tool:
        return Tool(name=name, description=description, inputSchema={
            "type": "object", "properties": properties,
            "required": required, "additionalProperties": False,
        })
    text = {"type": "string"}
    address = {"type": "string", "description": "Agent name or name@device."}
    thread = {"type": "string", "description": "A thread received or started by this agent."}
    return [
        tool("hub_status", "Online agents on this device and its network.", {}),
        tool("hub_agents", "Names of agents this agent can message.", {}),
        tool("hub_msg", "Send a message; the final answer to an incoming request is returned automatically.",
             {"to": address, "message": text, "thread": thread}, ["to", "message"]),
        tool("hub_ask", "Ask an agent and wait for its final turn, or a question back. Circular asks return busy.",
             {"to": address, "question": text, "thread": thread,
              "timeout": {"type": "number", "minimum": 1, "maximum": 600, "default": 300}}, ["to", "question"]),
        tool("hub_spawn", "Start an agent on a named LLM profile or loadout with an initial task.",
             {"name": text, "task": text, "llm": text}, ["name", "task", "llm"]),
    ]


def build_hub_server(
    *,
    status: Callable[[], dict[str, Any]] = _status,
    binding: Callable[[], dict[str, Any] | None | Any] = _binding_from_socket,
    call: Callable[..., Any] = _call_socket,
) -> Server:
    server = Server("kollab-hub", version="1")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        parent = await _require_binding(binding)
        if name not in {tool.name for tool in _tools()}:
            raise ValueError(f"unknown tool: {name}")
        if name == "hub_status":
            payload = parent.get("roster", parent) if isinstance(parent, dict) else status()
        else:
            payload = await call(name, arguments)
        return [TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]

    return server


async def serve_hub_status(
    *,
    status: Callable[[], dict[str, Any]] = _status,
    binding: Callable[[], dict[str, Any] | None | Any] = _binding_from_socket,
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

    return [*kollab_argv(), "mcp", "hub"]


def main(*, protocol: Any = None) -> None:
    if not os.environ.get("KOLLAB_ACP_HUB_TOKEN"):
        print("KOLLAB_ACP_HUB_TOKEN is required", file=sys.stderr)
        raise SystemExit(2)
    from .acp_stdio import isolated_stdout
    protocol = protocol or isolated_stdout()
    anyio.run(lambda: serve_hub_status_isolated(protocol))


async def serve_hub_status_isolated(protocol: Any) -> None:
    server = build_hub_server()
    async with stdio_server(stdout=anyio.wrap_file(protocol)) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    main()
