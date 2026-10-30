"""Hub IPC clients connect through a real kollabor_config.local_ipc server."""

import asyncio
import json
import os
import tempfile

import pytest

from kollabor import mcp_hub
from kollabor.state.hub_client import HubStateClient
from kollabor_config import local_ipc
from plugins.hub.presence import get_presence_dir


@pytest.fixture
def hub_sock(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("KOLLAB_ACP_HUB_TOKEN", "tok")
    monkeypatch.setenv("KOLLAB_ACP_GENERATION", "3")
    # macOS caps AF_UNIX paths near 104 bytes; pytest's tmp_path is longer.
    with tempfile.TemporaryDirectory(dir=tmp_path if os.name == "nt" else "/tmp") as d:
        sock = os.path.join(d, "hub.sock")
        monkeypatch.setenv("KOLLAB_ACP_HUB_SOCKET", sock)
        yield sock


def _reply_once(payload, seen):
    async def handler(reader, writer):
        seen.append(json.loads(await reader.readline()))
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        writer.close()

    return handler


def test_call_socket_sends_request_and_returns_result(hub_sock):
    seen = []
    reply = {"type": "acp_hub_call", "result": {"agents": ["lapis"]}}
    request = {
        "action": "acp_hub_call",
        "name": "hub_list",
        "arguments": {"limit": 2},
        "token": "tok",
        "generation": "3",
    }

    async def scenario():
        endpoint = await local_ipc.serve(_reply_once(reply, seen), hub_sock)
        try:
            return await mcp_hub._call_socket("hub_list", {"limit": 2})
        finally:
            endpoint.close()

    assert asyncio.run(scenario()) == {"agents": ["lapis"]}
    assert seen == [request]


def test_binding_from_socket_returns_status_reply(hub_sock):
    seen = []
    reply = {"type": "acp_hub_status", "status": "ok", "generation": "3"}

    async def scenario():
        endpoint = await local_ipc.serve(_reply_once(reply, seen), hub_sock)
        try:
            return await mcp_hub._binding_from_socket()
        finally:
            endpoint.close()

    assert asyncio.run(scenario()) == reply
    assert seen == [{"action": "acp_hub_status", "token": "tok", "generation": "3"}]


def test_hub_state_client_connect_reaches_peer_socket(hub_sock):
    presence = {"identity": "peer", "socket_path": hub_sock}
    (get_presence_dir() / "peer.json").write_text(json.dumps(presence))

    async def scenario():
        accepted = asyncio.Event()

        async def handler(reader, writer):
            accepted.set()
            await reader.read()

        endpoint = await local_ipc.serve(handler, hub_sock)
        try:
            async with HubStateClient.connect("peer"):
                await asyncio.wait_for(accepted.wait(), 2)
        finally:
            endpoint.close()

    asyncio.run(scenario())
