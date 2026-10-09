"""An agent on another computer opens by agent@device, through a chat here that runs the network."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from kollabor_engine import daemon_pool, server
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import panels as panels_route
from kollabor_engine.routes import sessions as sessions_route
from kollabor_engine.session import EngineSession

HANDLE = "koordinator@server-box"


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [])
    sessions = server.get_session_registry()
    before = dict(sessions)
    sessions.clear()
    yield sessions
    sessions.clear()
    sessions.update(before)


@pytest.fixture
def sock_dir():
    # Short: unix socket paths are capped near 104 bytes.
    folder = tempfile.mkdtemp(prefix="kra-", dir="/tmp")
    yield Path(folder)
    shutil.rmtree(folder, ignore_errors=True)


def _chat(socket_path, device="", lists=(HANDLE,)):
    """A chat here whose network lists the agents ``lists`` names (relay directory rows)."""

    async def get_hub_state():
        rows = [dict(zip(("name", "device"), h.split("@")), handle=h) for h in lists]
        return SimpleNamespace(remote=rows)

    return SimpleNamespace(
        alive=True,
        solo=False,
        device=device,
        daemon=SimpleNamespace(socket_path=str(socket_path)),
        state=SimpleNamespace(get_hub_state=get_hub_state),
    )


async def _daemon_answering(path, reply, asked):
    """The daemon of a chat here, which runs the network: answers one network_attach."""

    async def serve(reader, writer):
        asked.append(json.loads(await reader.readline()))
        writer.write(json.dumps(reply).encode() + b"\n")
        await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(serve, path=str(path))


async def _open(session_id):
    await server.open_named_session(SimpleNamespace(path_params={"session_id": session_id}))


@pytest.mark.asyncio
async def test_an_agent_on_another_computer_opens_through_a_chat_here(monkeypatch, registry, sock_dir):
    asked, opened = [], []
    reply = {"type": "network_attach", "socket_path": "/tmp/one-shot.sock"}
    listener = await _daemon_answering(sock_dir / "d.sock", reply, asked)
    # Never asked: an agent opened on another computer, and a chat on another
    # folder's network, where server-box may be a different computer.
    registry["other@far-box"] = _chat(sock_dir / "far.sock", device="far-box")
    registry["elsewhere"] = _chat(sock_dir / "e.sock", lists=("lapis@far-box",))
    registry["here"] = _chat(sock_dir / "d.sock")

    async def adopt(found):
        opened.append(found)
        return SimpleNamespace(session_id=found["session_id"])

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    try:
        await _open(HANDLE)
    finally:
        listener.close()
        await listener.wait_closed()

    assert asked == [{"action": "network_attach", "to": HANDLE}]
    assert opened == [
        {
            "session_id": HANDLE,
            "identity": "koordinator",
            "agent": "koordinator",
            "name": "koordinator",
            "socket_path": "/tmp/one-shot.sock",
            "daemon_pid": 0,
            "device": "server-box",
        }
    ]
    assert registry[HANDLE].session_id == HANDLE


@pytest.mark.asyncio
async def test_a_refusal_answers_503_in_the_other_computers_words(registry, sock_dir):
    refusal = "server-box has not let mac-box open its agents. On server-box, run: /connect attach allow mac-box"
    asked = []
    listener = await _daemon_answering(sock_dir / "d.sock", {"type": "error", "msg": refusal}, asked)
    registry["here"] = _chat(sock_dir / "d.sock")
    try:
        with pytest.raises(HTTPException) as failed:
            await _open(HANDLE)
    finally:
        listener.close()
        await listener.wait_closed()

    assert failed.value.status_code == 503
    assert refusal in failed.value.detail
    assert HANDLE not in registry


@pytest.mark.asyncio
async def test_with_no_chat_here_a_remote_agent_answers_503(registry, sock_dir):
    registry["other@far-box"] = _chat(sock_dir / "far.sock", device="far-box")
    with pytest.raises(HTTPException) as failed:
        await _open(HANDLE)
    assert failed.value.status_code == 503
    assert "no chat here runs this folder's network yet" in failed.value.detail

    registry["elsewhere"] = _chat(sock_dir / "e.sock", lists=("lapis@far-box",))
    with pytest.raises(HTTPException) as failed:
        await _open(HANDLE)
    assert f"{HANDLE} is not on the network right now" in failed.value.detail

    await _open("not@a@handle")  # not a handle: the route's own 404
    assert "not@a@handle" not in registry


@pytest.mark.asyncio
async def test_an_agent_on_another_computer_is_alive_while_its_connection_is(sock_dir):
    hang_up = asyncio.Event()

    async def tunnel(reader, writer):  # the one-shot socket NetworkAttach hands out
        await reader.readline()
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        await hang_up.wait()
        writer.close()

    listener = await asyncio.start_unix_server(tunnel, path=str(sock_dir / "t.sock"))
    row = {"identity": "koordinator", "socket_path": str(sock_dir / "t.sock"), "daemon_pid": 0}
    handle = await daemon_pool.DaemonPool().adopt(HANDLE, row)
    try:
        assert handle.alive
        hang_up.set()
        for _ in range(100):
            if not handle.alive:
                break
            await asyncio.sleep(0.02)
        assert not handle.alive
    finally:
        hang_up.set()  # a failed assert must not leave wait_closed() waiting on it
        await handle.close()
        listener.close()
        await listener.wait_closed()


def _answering(name, device="", read_only=False):
    async def get_hub_state():
        return SimpleNamespace(device=name, remote=[])

    async def get_panel(_name, _params):
        return {"read_only": read_only, "summary": f"{name} network"}

    return SimpleNamespace(
        session_id=name,
        alive=True,
        solo=False,
        device=device,
        workspace="/w/same",
        state=SimpleNamespace(get_hub_state=get_hub_state, get_panel=get_panel),
    )


@pytest.mark.asyncio
async def test_an_agent_on_another_computer_never_answers_for_this_one(registry):
    registry["koordinator@server-box"] = _answering("server-box", device="server-box")
    registry["viewer"] = viewer = _answering("mac-box", read_only=True)

    assert (await sessions_route._network_snapshot(registry))["device"] == "mac-box"
    # A chat here with a read-only Network panel keeps it rather than show the server's.
    assert await panels_route._network_owner(viewer) is None

    registry["owner"] = _answering("mac-box")
    remote = _answering("lapis@server-box", device="server-box", read_only=True)
    registry["lapis@server-box"] = remote
    # And the remote agent keeps its own computer's read-only panel, never this one's.
    assert await panels_route._network_owner(remote) is None


@pytest.mark.asyncio
async def test_an_agent_on_another_computer_reports_its_own_folder(monkeypatch):
    asked = []

    class State:
        async def get_active_profile(self):
            return SimpleNamespace(name="default", provider="openai", model="gpt", effort="")

        async def get_permission_state(self):
            return SimpleNamespace(approval_mode="TRUST_ALL")

        async def get_system_info(self):
            asked.append("system")
            return SimpleNamespace(cwd="/home/someone/project")

        async def get_conversation(self):
            return SimpleNamespace(messages=[])

    async def adopt(self, session_id, found):
        return SimpleNamespace(
            state=State(), alive=True, pid=0, identity="koordinator", agent_name="", subscribe=asyncio.Queue
        )

    monkeypatch.setattr(daemon_pool.DaemonPool, "adopt", adopt)
    remote = await EngineSession.adopt(
        {"session_id": HANDLE, "identity": "koordinator", "name": "koordinator", "device": "server-box"}
    )
    local = await EngineSession.adopt(
        {"session_id": "a1b2c3", "identity": "lapis", "name": "lapis", "workspace": "/w/here", "daemon_pid": 4242}
    )
    try:
        assert (remote.to_dict()["device"], remote.workspace) == ("server-box", "/home/someone/project")
        assert (local.to_dict()["device"], local.workspace) == ("", "/w/here")
        assert asked == ["system"]
    finally:
        for session in (remote, local):
            session._event_task.cancel()
