"""Attach RPCs answer in the order they came, and a talk-over's cut holds none up.

The Engine sends a chat's RPCs over one attach connection and the daemon
answers them in order, so a Stop sent after a Send runs after it, and no
handler may wait on a turn. Regression (#301): a talk-over's cut waited for its
turn to end, so a cancel or a permission answer sent behind it waited too: a
turn blocked on a prompt then never ended, and the connection stayed wedged.
The cut is now queued on its turn and answered at once.
"""

import asyncio
import contextlib
import json
import os
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor_agent.queue_processor import QueueProcessor
from kollabor_events.data_models import ConversationMessage
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


@contextlib.asynccontextmanager
async def attached(rpc):
    """An Engine-style attach connection to a daemon answering on `rpc`."""
    server = AgentSocketServer(
        agent_id="rpc-order-test", on_message=None,
        socket_name="rpc-order-" + os.urandom(4).hex(),
    )
    server._display_tap = DisplayTap(history_size=10)
    server._rpc_server = rpc
    reader, writer = await asyncio.open_unix_connection(await server.start())

    async def send(*frames):
        for frame in frames:
            writer.write((json.dumps(frame) + "\n").encode())
        await writer.drain()

    try:
        await send({"action": "attach", "mode": "interactive", "client_id": "engine"})
        yield reader, send
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        await server.stop()


def request(request_id, method, **params):
    return {"action": "rpc_request", "request_id": request_id, "method": method, "params": params}


async def replies(reader, count, timeout=5):
    """The next `count` rpc replies in the order they came, skipping display events."""
    got = []
    while len(got) < count:
        msg = json.loads((await asyncio.wait_for(reader.readline(), timeout)).decode())
        if msg.get("action") == "rpc_reply":
            got.append(msg)
    return got


@pytest.mark.asyncio
async def test_a_stop_sent_after_a_send_runs_after_it(socket_dir):
    ran, admitted = [], asyncio.Event()

    async def send_turn(params):
        await admitted.wait()  # the turn takes a moment to queue
        ran.append("send")
        return {"queued": True}

    def stop(params):
        ran.append("stop")
        return {"cancelled": True}

    rpc = RpcServer()
    rpc.register("state.send", send_turn)
    rpc.register("state.cancel", stop)
    async with attached(rpc) as (reader, send):
        await send(request("r1", "state.send"), request("r2", "state.cancel"))
        await asyncio.sleep(0.05)
        assert ran == []  # the Stop never ran ahead of its Send
        admitted.set()
        assert [r["request_id"] for r in await replies(reader, 2)] == ["r1", "r2"]
        assert ran == ["send", "stop"]

        # A request sent right before a detach still gets its answer.
        await send(request("r3", "state.cancel"), {"type": "detach"})
        assert (await replies(reader, 1))[0]["result"] == {"cancelled": True}


class Turns(SimpleNamespace):
    """A queue processor's turn state with its real after_turn queue."""

    after_turn = QueueProcessor.after_turn
    _flush_after_turn = QueueProcessor._flush_after_turn
    _run_after_turn = QueueProcessor._run_after_turn


@pytest.mark.asyncio
async def test_a_cut_on_an_unwinding_turn_never_holds_up_the_answer_behind_it(socket_dir):
    turns = Turns(_turn_lock=asyncio.Lock(), _open_voice_turn="turn-1", _after_turn=[], _after_turn_flush=None)
    reply = ConversationMessage(
        role="assistant", content="Let me check the weather.", metadata={"voice_turn_id": "turn-1"}
    )
    llm = SimpleNamespace(
        _queue_processor=turns, conversation_history=[reply],
        conversation_logger=SimpleNamespace(), current_parent_uuid="parent",
    )
    rpc = RpcServer()
    register_state_handlers(rpc, LocalStateService(llm, profile_manager=object(), event_bus=None))
    rpc.register("permission.respond", lambda params: {"ok": True})  # the prompt's answer
    await turns._turn_lock.acquire()  # the turn waits on a prompt or a slow tool
    async with attached(rpc) as (reader, send):
        await send(
            request("r1", "state.voice_interrupted_reply", turn_id="turn-1", text="Let me"),
            request("r2", "permission.respond", tool_id="t1", control="approve_once"),
        )
        first, second = await replies(reader, 2, timeout=1)
    assert first["result"] == {"recorded": False, "pending": True}
    assert second["result"] == {"ok": True}
    assert reply.content == "Let me check the weather."  # nothing cut mid-turn
    turns._open_voice_turn = None  # the turn unwinds; the next round starts
    turns._turn_lock.release()
    async with turns._turn_lock:
        await turns._run_after_turn()
    assert reply.content == "Let me"
