"""An RPC that waits never holds up the ones after it on the same attach socket.

Regression (#301): the attach read loop answered each rpc_request inline. A
talk-over's cut waits for its turn to end, so a cancel or a permission answer
sent after it on the Engine's one connection waited too: a turn blocked on a
prompt then never ended, and the connection stayed wedged.
"""

import asyncio
import json
import os
import secrets
from pathlib import Path

import pytest

from kollabor_rpc import RpcServer
from kollabor_tui.display_tap import DisplayTap
from plugins.hub.messenger import AgentSocketServer


@pytest.fixture
def socket_dir(monkeypatch):
    # AF_UNIX paths are length-limited: pytest's tmp_path is too deep.
    d = Path("/tmp") / f"kollab-rpc-test-{secrets.token_hex(4)}"
    d.mkdir(mode=0o700)
    monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: d)
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


async def _replies(reader, count):
    """The first `count` rpc replies, by request id, skipping display events."""
    got = {}
    while len(got) < count:
        msg = json.loads((await asyncio.wait_for(reader.readline(), 5)).decode())
        if msg.get("action") == "rpc_reply":
            got[msg["request_id"]] = msg
    return got


@pytest.mark.asyncio
async def test_a_waiting_rpc_does_not_hold_up_the_next_one(socket_dir):
    release = asyncio.Event()

    async def cut(params):
        await release.wait()  # waits out the turn
        return {"recorded": True}

    rpc = RpcServer()
    rpc.register("state.slow", cut)
    rpc.register("state.cancel", lambda params: {"cancelled": True})
    server = AgentSocketServer(
        agent_id="rpc-concurrency-test", on_message=None,
        socket_name="rpc-concurrency-" + os.urandom(4).hex(),
    )
    server._display_tap = DisplayTap(history_size=10)
    server._rpc_server = rpc
    reader, writer = await asyncio.open_unix_connection(await server.start())
    try:
        def send(frame):
            writer.write((json.dumps(frame) + "\n").encode())

        send({"action": "attach", "mode": "interactive", "client_id": "engine"})
        send({"action": "rpc_request", "request_id": "r1", "method": "state.slow", "params": {}})
        send({"action": "rpc_request", "request_id": "r2", "method": "state.cancel", "params": {}})
        await writer.drain()

        first = await _replies(reader, 1)
        assert list(first) == ["r2"] and first["r2"]["result"] == {"cancelled": True}

        release.set()  # the turn ends: the cut answers too
        assert (await _replies(reader, 1))["r1"]["result"] == {"recorded": True}

        # A request sent right before a detach still gets its answer.
        release.clear()
        send({"action": "rpc_request", "request_id": "r3", "method": "state.slow", "params": {}})
        send({"type": "detach"})
        await writer.drain()
        await asyncio.sleep(0.05)
        release.set()
        assert (await _replies(reader, 1))["r3"]["result"] == {"recorded": True}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await server.stop()
