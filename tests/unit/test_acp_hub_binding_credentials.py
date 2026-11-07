"""#587: the ACP Hub child binding stays out of the MCP config an adapter puts on a command line.

The Claude ACP adapter serializes the ``session/new`` MCP servers, env included, into a
``--mcp-config`` process argument. The binding token must not be in that config, yet the
real ``kollab mcp hub`` child launched from it must still authenticate, and a rotated
binding must stop working. Assertions never print the synthetic credential.
"""

import json
import os
import secrets
import stat
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kollabor.llm import acp_orchestrator as mod
from kollabor.llm.acp_bridge import ACPProfile
from kollabor_config import local_ipc


class _Hub:
    """A Hub owner that mints synthetic bindings and checks them on a real local socket."""

    def __init__(self, socket_path: str) -> None:
        self._identity = None
        self._presence = None
        self._socket_server = SimpleNamespace(socket_path=socket_path)
        self.token = ""
        self.generation = 0

    def rotate_acp_hub_binding(self):
        self.generation += 1
        self.token = "synthetic-" + secrets.token_urlsafe(24)
        return self.token, self.generation

    async def handle(self, reader, writer):
        request = json.loads(await reader.readline())
        valid = request.get("token") == self.token and request.get("generation") == str(self.generation)
        reply = {"type": "acp_hub_status", "roster": [{"identity": "parent"}]} if valid else {
            "type": "acp_hub_status", "error": "stale"}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
        writer.close()


class _Runner:
    def __init__(self) -> None:
        self.profile = ACPProfile(name="adapter", command=("adapter",))
        self.session_id = None
        self.binding = None

    async def close(self) -> None:
        pass


def _adapter_mcp_config(runner) -> str:
    """The MCP server list as session/new sends it, JSON-encoded as an adapter's --mcp-config argument."""
    return json.dumps([server.model_dump(mode="json") for server in runner.profile.sdk_mcp_servers()])


@asynccontextmanager
async def _hub_child(entry):
    """Launch the real Hub MCP child exactly as the MCP config describes it."""
    params = StdioServerParameters(
        command=entry["command"], args=list(entry["args"]), env={**os.environ, **entry["env"]})
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def _authorized(session) -> bool:
    result = await session.call_tool("hub_status", {})
    return not result.isError and "parent" in result.content[0].text


@pytest.mark.asyncio
async def test_hub_binding_is_not_in_adapter_mcp_config_and_rotates():
    with tempfile.TemporaryDirectory(dir=None if os.name == "nt" else "/tmp") as directory:
        hub = _Hub(os.path.join(directory, "hub.sock"))
        endpoint = await local_ipc.serve(hub.handle, hub._socket_server.socket_path)
        try:
            orchestrator = mod.ACPOrchestrator(SimpleNamespace(get_service=lambda name: None), hub_plugin=hub)
            runner = _Runner()
            orchestrator._runner = runner

            await orchestrator._runner_lifecycle("starting", runner)
            first_token = hub.token
            leaked = first_token in _adapter_mcp_config(runner)
            assert not leaked, "Hub binding credential is in the adapter's MCP config"

            (first,) = runner.profile.mcp_servers
            async with _hub_child(first) as old_child:
                assert await _authorized(old_child), "a current binding must authorize hub_status"

                await orchestrator._runner_lifecycle("starting", runner)
                (second,) = runner.profile.mcp_servers
                config = _adapter_mcp_config(runner)
                leaked = hub.token in config or first_token in config
                assert not leaked, "Hub binding credential is in the adapter's MCP config"
                assert not await _authorized(old_child), "a rotated binding must be rejected"
                async with _hub_child(second) as new_child:
                    assert await _authorized(new_child), "the new binding must authorize hub_status"

            files = [Path(value) for value in second["env"].values() if os.path.isfile(value)]
            assert len(files) == 1, "the binding travels in one credential file"
            if os.name != "nt":
                assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
                assert stat.S_IMODE(files[0].parent.stat().st_mode) == 0o700

            await orchestrator.close()
            assert not files[0].exists(), "closing the session must remove the credential file"
        finally:
            endpoint.close()

