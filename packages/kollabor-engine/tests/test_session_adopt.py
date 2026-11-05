"""Every live agent on this computer opens in the web UI; the engine never stops one it did not start."""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from kollabor_engine import daemon_pool, server
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import sessions as sessions_route
from kollabor_engine.session import EngineSession


@pytest.fixture
def registry():
    sessions = server.get_session_registry()
    before = dict(sessions)
    yield sessions
    sessions.clear()
    sessions.update(before)


def _row(session_id="a1b2c3", pid=4242, **extra):
    return {
        "session_id": session_id,
        "name": "koordinator",
        "identity": "koordinator",
        "agent": "default",
        "workspace": "/w/mentiko",
        "daemon_pid": pid,
        "socket_path": "",
        "created_at": 0,
        "active": True,
        "discovered": True,
        "external": True,
        **extra,
    }


def _attach_payload(session_id, mcp_servers):
    return {
        "session_id": session_id,
        "credentials": {
            "provider": "openai",
            "api_key": "test-api-key",
            "model": "test-model",
        },
        "mcp_servers": mcp_servers,
    }


def _mock_named_session(monkeypatch, session, opened=None):
    async def open_existing_session(_request, *, session_id=None):
        assert session_id == session.session_id
        if opened is not None:
            opened.set()
        return session

    monkeypatch.setattr(sessions_route, "open_named_session", open_existing_session)


@pytest.mark.asyncio
async def test_an_adopted_agent_is_detached_and_never_signalled(monkeypatch):
    # A real socket that speaks the attach handshake, standing in for a
    # terminal agent. Short dir: unix socket paths are capped near 104 bytes.
    sock_dir = tempfile.mkdtemp(prefix="kad-", dir="/tmp")
    sock = str(Path(sock_dir) / "k.sock")
    frames = []

    async def agent(reader, writer):
        frames.append(json.loads(await reader.readline()))
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        while line := await reader.readline():
            frames.append(json.loads(line))
        writer.close()

    listener = await asyncio.start_unix_server(agent, path=sock)
    signals = []
    real_kill = os.kill

    def kill(pid, sig):
        if sig:
            signals.append((pid, sig))
            return None
        return real_kill(pid, sig)

    monkeypatch.setattr(daemon_pool.os, "kill", kill)
    pool = daemon_pool.DaemonPool()
    try:
        handle = await pool.adopt("a1b2c3", _row(pid=os.getpid(), socket_path=sock))
        assert handle.alive
        assert frames[0]["action"] == "attach"
        assert frames[0]["client_id"] == f"engine-{os.getpid()}-a1b2c3"

        assert await pool.stop("a1b2c3")
        await asyncio.sleep(0.05)
    finally:
        listener.close()
        await listener.wait_closed()
        shutil.rmtree(sock_dir, ignore_errors=True)

    assert {"type": "detach"} in frames
    assert signals == []  # no SIGTERM, no SIGKILL: the agent is still its owner's


@pytest.mark.asyncio
async def test_concurrent_requests_open_an_agent_once(monkeypatch, registry):
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])
    opened = []

    async def adopt(found):
        opened.append(found["session_id"])
        await asyncio.sleep(0.05)  # both requests are in flight while it opens
        return SimpleNamespace(session_id=found["session_id"])

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    request = SimpleNamespace(path_params={"session_id": "a1b2c3"})

    await asyncio.gather(server.open_named_session(request), server.open_named_session(request))

    assert opened == ["a1b2c3"]
    assert registry["a1b2c3"].session_id == "a1b2c3"


def test_a_session_route_opens_the_agent_it_names(monkeypatch, registry):
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])

    async def get_history_nonblocking(limit=None):
        return {
            "session_id": "a1b2c3",
            "history": [{"role": "user", "content": "from the terminal"}],
            "last_turn_error": None,
            "source": "mirror",
            "stale": False,
        }

    async def adopt(found):
        return SimpleNamespace(
            session_id=found["session_id"],
            alive=True,
            last_turn_error=None,
            get_history_nonblocking=get_history_nonblocking,
        )

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    client = TestClient(server.create_app())

    history = client.get("/sessions/a1b2c3/history")
    unknown = client.get("/sessions/nobody/history")

    assert history.status_code == 200
    assert history.json()["history"] == [{"role": "user", "content": "from the terminal"}]
    assert unknown.status_code == 404


def test_an_agent_that_will_not_attach_answers_503(monkeypatch, registry):
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])

    async def adopt(found):
        raise ConnectionRefusedError("socket gone")

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    response = TestClient(server.create_app()).get("/sessions/a1b2c3/history")

    assert response.status_code == 503
    assert "could not open this agent" in response.json()["detail"]
    assert "a1b2c3" not in registry


@pytest.mark.asyncio
async def test_list_sessions_lists_each_agent_once(monkeypatch, registry):
    # This engine's own daemon is in presence too, under its agent id.
    own = SimpleNamespace(
        session_id="griffin-born",
        alive=True,
        to_dict=lambda: {"session_id": "griffin-born", "daemon_pid": 111},
    )
    registry["griffin-born"] = own
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [_row("own-agent-id", pid=111), _row("terminal-id", pid=222)],
    )

    async def no_network(_registry):
        return {"device": "", "remote": []}

    monkeypatch.setattr(sessions_route, "_network_snapshot", no_network)
    result = await sessions_route.list_sessions()

    assert [item["session_id"] for item in result["sessions"]] == ["griffin-born", "terminal-id"]


@pytest.mark.asyncio
async def test_a_daemon_this_engine_is_starting_is_not_listed_as_another_agent(monkeypatch, registry):
    # A new daemon writes presence before the engine attaches and registers it.
    pool = daemon_pool.DaemonPool()
    pool._starting["new-chat"] = ("bismuth", os.path.realpath("/w/mentiko"))
    pool._daemons["attached-chat"] = SimpleNamespace(pid=333)
    monkeypatch.setattr(sessions_route, "get_daemon_pool", lambda: pool)
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [
            _row("starting-id", pid=0, identity="bismuth"),
            _row("attached-id", pid=333, identity="zircon"),
            _row("other-folder-id", pid=444, identity="bismuth", workspace="/w/other"),
        ],
    )

    async def no_network(_registry):
        return {"device": "", "remote": []}

    monkeypatch.setattr(sessions_route, "_network_snapshot", no_network)
    result = await sessions_route.list_sessions()

    assert [item["session_id"] for item in result["sessions"]] == ["other-folder-id"]


def test_a_daemon_this_engine_is_starting_does_not_open_as_another_agent(monkeypatch, registry):
    pool = daemon_pool.DaemonPool()
    pool._starting["new-chat"] = ("koordinator", os.path.realpath("/w/mentiko"))
    monkeypatch.setattr(server, "get_daemon_pool", lambda: pool)
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])

    async def adopt(found):
        raise AssertionError("must not adopt this engine's own daemon")

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    response = TestClient(server.create_app()).get("/sessions/a1b2c3/history")

    assert response.status_code == 404


def test_adopted_session_reports_itself_as_external():
    session = EngineSession(session_id="a1b2c3", profile=None, approval_mode="")
    session.external = True
    session.display_name = "2610082249-nexus-drift"
    session.daemon = SimpleNamespace(identity="koordinator", agent_name="default", pid=4242, alive=True)

    row = session.to_dict()

    assert row["external"] is True
    assert row["name"] == "2610082249-nexus-drift"
    assert row["identity"] == "koordinator"
    assert row["daemon_pid"] == 4242


@pytest.mark.asyncio
async def test_an_open_agent_keeps_the_name_of_its_current_conversation(monkeypatch, registry):
    # The terminal ran /new: presence names the new conversation's log.
    session = EngineSession(session_id="a1b2c3", profile=None, approval_mode="")
    session.external = True
    session.display_name = "2610082249-nexus-drift"
    session.daemon = SimpleNamespace(identity="koordinator", agent_name="", pid=4242, alive=True)
    registry["a1b2c3"] = session
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [_row("a1b2c3", pid=4242, name="2610090000-quiet-heron")],
    )

    async def no_network(_registry):
        return {"device": "", "remote": []}

    monkeypatch.setattr(sessions_route, "_network_snapshot", no_network)
    result = await sessions_route.list_sessions()

    assert [item["name"] for item in result["sessions"]] == ["2610090000-quiet-heron"]



@pytest.mark.asyncio
async def test_adopt_does_not_await_slow_history_refresh(monkeypatch):
    events = asyncio.Queue()

    class SilentState:
        async def get_conversation(self, **kwargs):
            await asyncio.sleep(10)

        async def get_active_profile(self):
            return SimpleNamespace(name="p", model="m", provider="openai")

        async def get_permission_state(self):
            return SimpleNamespace(approval_mode="TRUST_ALL")

    handle = SimpleNamespace(
        alive=True,
        launcher=None,
        state=SilentState(),
        subscribe=lambda: events,
        unsubscribe=lambda queue: None,
        fan_out=lambda event: None,
    )

    class Pool:
        async def adopt(self, session_id, found, *, owned=False, webui_owned=False):
            assert owned is False
            assert webui_owned is False
            return handle

        async def stop(self, session_id):
            return True

    monkeypatch.setattr("kollabor_engine.session.get_daemon_pool", lambda: Pool())
    started = asyncio.get_running_loop().time()
    session = await EngineSession.adopt(_row(session_id="silent"))
    elapsed = asyncio.get_running_loop().time() - started
    try:
        assert elapsed < 0.2
        assert session._history_refresh_task is not None
    finally:
        await session.shutdown()


@pytest.mark.asyncio
async def test_an_adopted_row_keeps_what_presence_says_until_the_daemon_answers(monkeypatch):
    """A busy daemon is slow to answer its profile; the row must not drop its model meanwhile."""
    answer = asyncio.Event()

    class BusyState:
        async def get_conversation(self, **kwargs):
            await asyncio.sleep(10)

        async def get_active_profile(self):
            await answer.wait()
            return SimpleNamespace(name="local", model="qwen", provider="custom", description="", effort="")

        async def get_permission_state(self):
            return SimpleNamespace(approval_mode="TRUST_ALL")

    handle = SimpleNamespace(
        alive=True,
        launcher=None,
        state=BusyState(),
        identity="koordinator",
        agent_name="default",
        pid=4242,
        subscribe=lambda: asyncio.Queue(),
        unsubscribe=lambda queue: None,
        fan_out=lambda event: None,
    )

    class Pool:
        async def adopt(self, session_id, found, *, owned=False, webui_owned=False):
            assert owned is False
            assert webui_owned is False
            return handle

        async def stop(self, session_id):
            return True

    monkeypatch.setattr("kollabor_engine.session.get_daemon_pool", lambda: Pool())
    found = _row(session_id="busy", profile="openai-oauth", model="gpt-6-luna", provider="openai")
    session = await EngineSession.adopt(found)
    try:
        row = session.to_dict()
        assert (row["profile"], row["model"], row["provider"], row["harness"]) == (
            "openai-oauth",
            "gpt-6-luna",
            "openai",
            "",
        )

        answer.set()
        await session._facts_task
        row = session.to_dict()
        assert (row["profile"], row["model"], row["provider"]) == ("local", "qwen", "custom")
    finally:
        await session.shutdown()


@pytest.mark.asyncio
async def test_an_adopted_harness_row_keeps_its_harness_name(monkeypatch):
    handle = SimpleNamespace(
        alive=True,
        launcher=None,
        state=SimpleNamespace(),
        identity="coder",
        agent_name="default",
        pid=4243,
        subscribe=lambda: asyncio.Queue(),
        unsubscribe=lambda queue: None,
        fan_out=lambda event: None,
    )

    class Pool:
        async def adopt(self, session_id, found, *, owned=False, webui_owned=False):
            assert owned is False
            assert webui_owned is False
            return handle

        async def stop(self, session_id):
            return True

    monkeypatch.setattr("kollabor_engine.session.get_daemon_pool", lambda: Pool())
    found = _row(session_id="acp", profile="claude-code", provider="acp", harness="Claude Code")
    session = await EngineSession.adopt(found)
    try:
        assert session.to_dict()["harness"] == "Claude Code"
    finally:
        await session.shutdown()


@pytest.mark.asyncio
async def test_adopted_approval_mode_failure_is_unknown_and_recovers(monkeypatch):
    attempts = 0

    class State:
        async def get_conversation(self, **kwargs):
            return {"messages": []}

        async def get_active_profile(self):
            return SimpleNamespace(name="profile", model="model", provider="openai")

        async def get_permission_state(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("private permission transport detail")
            return SimpleNamespace(approval_mode="CONFIRM_ALL")

    handle = SimpleNamespace(
        alive=True,
        launcher=None,
        state=State(),
        identity="koordinator",
        agent_name="default",
        pid=4242,
        project="/tmp/workspace",
        subscribe=lambda: asyncio.Queue(),
        unsubscribe=lambda queue: None,
        fan_out=lambda event: None,
    )

    class Pool:
        async def adopt(self, session_id, found, *, owned=False, webui_owned=False):
            return handle

        async def stop(self, session_id):
            return True

        async def detach(self, session_id):
            return True

    monkeypatch.setattr("kollabor_engine.session.get_daemon_pool", lambda: Pool())
    async def no_track(self):
        return None

    monkeypatch.setattr(EngineSession, "_track_events", no_track)
    monkeypatch.setattr(EngineSession, "_schedule_refresh", lambda self: None)

    session = await EngineSession.adopt(_row(session_id="unknown-approval"))
    try:
        assert session._facts_task is not None
        await session._facts_task
        first = session.to_dict()
        assert first["approval_mode"] is None
        assert first["approval_mode_warning"]
        assert "private permission transport detail" not in repr(first)

        await session._read_agent_facts()
        recovered = session.to_dict()
        assert recovered["approval_mode"] == "confirm_all"
        assert recovered["approval_mode_warning"] is None
    finally:
        await session.shutdown(stop_daemon=False)


def test_persistence_warning_is_actionable_and_clears_after_retry():
    session = EngineSession(session_id="persist-warning", profile=None)
    attempts = 0

    def persist(_session):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("private manifest location")

    session.on_persistence_changed = persist
    session._notify_persistence_changed()
    failed = session.to_dict()
    assert failed["persistence_warning"]
    assert "private manifest location" not in repr(failed)

    # A later metadata change invokes the same callback again and clears the
    # warning once its durable write succeeds.
    session._notify_persistence_changed()
    assert session.to_dict()["persistence_warning"] is None
    assert attempts == 2


def test_mentiko_attach_resumes_live_daemon_without_second_system_prompt_root(
    monkeypatch, registry, tmp_path
):
    """A proxy reusing a live ID must attach, not append a second lineage root."""
    session_id = "a1b2c3"
    session_log = tmp_path / f"session_{session_id}.jsonl"
    initial_root = {
        "type": "system_prompt",
        "parentUuid": None,
        "message": {"role": "system", "content": "existing system prompt"},
    }
    session_log.write_text(json.dumps(initial_root) + "\n")
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [
            _row(session_id, session_log=str(session_log), workspace=str(tmp_path))
        ],
    )

    spawned = []
    adopted = []
    attached_mcp_servers = []

    async def fake_initialize(session):
        spawned.append(session.session_id)
        with session_log.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "system_prompt",
                        "parentUuid": None,
                        "message": {
                            "role": "system",
                            "content": "second daemon bootstrap with MCP tools",
                        },
                    }
                )
                + "\n"
            )
        await fake_apply_mcp_servers(session)
        session.daemon = SimpleNamespace(
            pid=5151, alive=True, launcher=None, state=SimpleNamespace()
        )

    async def fake_apply_mcp_servers(session, server_names=None):
        attached_mcp_servers.extend(
            session.mcp_server_names if server_names is None else server_names
        )

    async def fake_test_mcp_server(_name):
        return {"found": True, "enabled": False}

    async def fake_mcp_state_call(*_args):
        return None

    async def fake_adopt(cls, found, **kwargs):
        adopted.append(found["session_id"])
        session = cls(
            session_id=found["session_id"],
            profile=None,
            workspace=found.get("workspace"),
            mcp_server_names=[],
        )
        session.conversation_id = Path(found["session_log"]).stem
        session.daemon = SimpleNamespace(
            pid=found["daemon_pid"],
            alive=True,
            launcher=None,
            state=SimpleNamespace(
                test_mcp_server=fake_test_mcp_server,
                disable_mcp_server=fake_mcp_state_call,
                reload_mcp_servers=fake_mcp_state_call,
            ),
        )
        return session

    monkeypatch.setattr(EngineSession, "initialize", fake_initialize)
    monkeypatch.setattr(EngineSession, "adopt", classmethod(fake_adopt))
    monkeypatch.setattr(EngineSession, "_apply_mcp_servers", fake_apply_mcp_servers)
    monkeypatch.setattr(
        EngineSession,
        "to_dict",
        lambda session: {"session_id": session.session_id, "active": session.alive},
    )

    response = TestClient(server.create_app()).post(
        "/sessions",
        headers={"x-mentiko-session-token": "test-session-token"},
        json={
            "session_id": session_id,
            "credentials": {
                "provider": "openai",
                "api_key": "test-api-key",
                "model": "test-model",
            },
            "mcp_servers": ["mentiko", "marclone-memory"],
        },
    )

    assert response.status_code == 200, response.text
    records = [json.loads(line) for line in session_log.read_text().splitlines()]
    system_roots = [
        record
        for record in records
        if record.get("type") == "system_prompt" and record.get("parentUuid") is None
    ]
    assert len(system_roots) == 1
    assert spawned == []
    assert adopted == [session_id]
    assert attached_mcp_servers == ["mentiko", "marclone-memory"]
    assert registry[session_id].user_token == "test-session-token"

    async def fail_mcp_servers(_session, _server_names=None):
        raise RuntimeError("daemon temporarily unavailable")

    monkeypatch.setattr(EngineSession, "_apply_mcp_servers", fail_mcp_servers)
    failed_response = TestClient(server.create_app()).post(
        "/sessions",
        headers={"x-mentiko-session-token": "retry-session-token"},
        json={
            "session_id": session_id,
            "credentials": {
                "provider": "openai",
                "api_key": "test-api-key",
                "model": "test-model",
            },
            "mcp_servers": ["mentiko", "marclone-memory", "retry-server"],
        },
    )

    assert failed_response.status_code == 503
    assert "retry the attach" in failed_response.json()["detail"]
    assert registry[session_id].mcp_server_names == ["mentiko", "marclone-memory"]
    assert spawned == []
    assert adopted == [session_id]
    records = [json.loads(line) for line in session_log.read_text().splitlines()]
    system_roots = [
        record
        for record in records
        if record.get("type") == "system_prompt" and record.get("parentUuid") is None
    ]
    assert len(system_roots) == 1


@pytest.mark.asyncio
async def test_mentiko_attach_rolls_back_persisted_mcp_settings_and_preserves_history(
    monkeypatch, registry, tmp_path, caplog
):
    """Real adoption must roll back partial MCP writes and preserve disk history."""
    import httpx

    from kollabor.state import LocalStateService
    from kollabor.state.handlers import register_state_handlers
    from kollabor_agent import MCPManager
    from kollabor_rpc import RpcServer

    session_id = "mcp-attach"
    conversation_id = f"session_{session_id}"
    session_log = tmp_path / f"{conversation_id}.jsonl"
    records = [
        {
            "type": "system_prompt",
            "parentUuid": None,
            "message": {
                "role": "system",
                "content": "existing system prompt",
                "metadata": {"uuid": "system-root"},
            },
        },
        {
            "type": "user",
            "parentUuid": "system-root",
            "timestamp": "2026-11-03T12:00:00Z",
            "message": {
                "role": "user",
                "content": "hello from the existing transcript",
                "metadata": {"uuid": "turn-1", "parentUuid": "system-root"},
            },
        },
        {
            "type": "assistant",
            "parentUuid": "turn-1",
            "timestamp": "2026-11-03T12:00:01Z",
            "message": {
                "role": "assistant",
                "content": "existing reply",
                "metadata": {"uuid": "turn-2", "parentUuid": "turn-1"},
            },
        },
    ]
    original_log = "\n".join(json.dumps(record) for record in records) + "\n"
    session_log.write_text(original_log)

    mcp_manager = MCPManager(mcp_dir=tmp_path / "mcp")
    mcp_manager.save_config(
        {
            "servers": {
                "already-enabled": {"command": "unused", "enabled": True},
                "retry-server": {"command": "unused", "enabled": False},
            }
        }
    )

    class FailingMCPIntegration:
        def __init__(self):
            self.reload_calls = 0
            self.server_connections = {}
            self.tool_registry = {}

        async def reload_mcp_servers(self):
            self.reload_calls += 1
            # Model reload tearing down active connections before discovery.
            self.server_connections.clear()
            if self.reload_calls <= 2:
                raise RuntimeError(f"simulated MCP reload failure {self.reload_calls}")
            return {"configured": 2, "discovered": 2, "reconnected": 0}

    mcp_integration = FailingMCPIntegration()
    conversation_history = [
        SimpleNamespace(
            role="user",
            content="hello from the existing transcript",
            timestamp="2026-11-03T12:00:00Z",
            metadata={"uuid": "turn-1", "parentUuid": "system-root"},
        ),
        SimpleNamespace(
            role="assistant",
            content="existing reply",
            timestamp="2026-11-03T12:00:01Z",
            metadata={"uuid": "turn-2", "parentUuid": "turn-1"},
        ),
    ]
    llm_service = SimpleNamespace(
        config={},
        conversation_history=conversation_history,
        mcp_integration=mcp_integration,
    )
    state_service = LocalStateService(llm_service, SimpleNamespace())
    # Keep MCPManager real but scope its persistent settings to this test.
    state_service._mcp_manager = lambda: mcp_manager
    rpc_server = RpcServer()
    register_state_handlers(rpc_server, state_service)

    socket_dir = tempfile.mkdtemp(prefix="kad-", dir="/tmp")
    socket_path = str(Path(socket_dir) / "daemon.sock")
    attach_frames = []

    async def fake_daemon(reader, writer):
        try:
            attach_frames.append(json.loads(await reader.readline()))
            writer.write(b'{"type":"attach_ack"}\n')
            await writer.drain()
            while line := await reader.readline():
                frame = json.loads(line)
                if frame.get("action") == "rpc_request":
                    reply = await rpc_server.handle_wire(frame)
                    writer.write((json.dumps(reply, default=str) + "\n").encode())
                    await writer.drain()
        except (BrokenPipeError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionError):
                pass

    listener = await asyncio.start_unix_server(fake_daemon, path=socket_path)
    monkeypatch.setattr(EngineSession, "_disk_conversation_dir", lambda _self: tmp_path)
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [
            _row(
                session_id,
                pid=os.getpid(),
                socket_path=socket_path,
                session_log=str(session_log),
                workspace=str(tmp_path),
            )
        ],
    )

    payload = {
        "session_id": session_id,
        "credentials": {
            "provider": "openai",
            "api_key": "test-api-key",
            "model": "test-model",
        },
        "mcp_servers": ["already-enabled", "retry-server"],
    }
    headers = {"x-mentiko-session-token": "test-session-token"}
    app = server.create_app()

    async def history(client):
        response = await client.get(f"/sessions/{session_id}/history")
        assert response.status_code == 200, response.text
        return response.json()["history"]

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                failed = await client.post("/sessions", headers=headers, json=payload)
                assert failed.status_code == 503, failed.text
                assert "retry the attach" in failed.json()["detail"]
                assert "rollback was incomplete" in failed.json()["detail"]

                # The persisted snapshot restores both values, not just wrapper
                # metadata, even though the rollback reload also failed.
                assert mcp_manager.get_server_status(
                    "already-enabled", mcp_integration
                )["enabled"] is True
                assert mcp_manager.get_server_status(
                    "retry-server", mcp_integration
                )["enabled"] is False
                assert any(
                    "failed to reload MCP servers after rollback" in record.getMessage()
                    for record in caplog.records
                )

                failed_history = await history(client)
                assert [entry["content"] for entry in failed_history] == [
                    "hello from the existing transcript",
                    "existing reply",
                ]
                assert failed_history[0]["metadata"]["parentUuid"] == "system-root"
                assert failed_history[1]["metadata"]["parentUuid"] == "turn-1"
                assert session_log.read_text() == original_log

                retried = await client.post("/sessions", headers=headers, json=payload)
                assert retried.status_code == 200, retried.text
                retry_history = await history(client)
                assert retry_history == failed_history
                assert session_log.read_text() == original_log

                repeated = await client.post("/sessions", headers=headers, json=payload)
                assert repeated.status_code == 200, repeated.text
                assert await history(client) == failed_history
                assert session_log.read_text() == original_log

        assert len(attach_frames) == 1
        assert attach_frames[0]["action"] == "attach"
        assert mcp_integration.reload_calls == 3
    finally:
        session = registry.get(session_id)
        if session is not None:
            await session.shutdown(stop_daemon=False)
        listener.close()
        await listener.wait_closed()
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_concurrent_attaches_serialize_mcp_snapshot_and_rollback(monkeypatch, tmp_path):
    """A later failed attach must not undo the first attach's successful MCP."""
    import httpx

    class GatedMCPState:
        def __init__(self):
            self.enabled = {"server-a": False, "server-b": False}
            self.reload_calls = 0
            self.first_a_snapshot_started = asyncio.Event()
            self.release_first_a_snapshot = asyncio.Event()
            self.server_b_snapshot_started = asyncio.Event()
            self.release_server_b_snapshot = asyncio.Event()

        async def test_mcp_server(self, name):
            status = {"found": True, "enabled": self.enabled[name]}
            if name == "server-a" and not self.first_a_snapshot_started.is_set():
                self.first_a_snapshot_started.set()
                await self.release_first_a_snapshot.wait()
            if name == "server-b":
                self.server_b_snapshot_started.set()
                await self.release_server_b_snapshot.wait()
            return status

        async def enable_mcp_server(self, name):
            self.enabled[name] = True

        async def disable_mcp_server(self, name):
            self.enabled[name] = False

        async def reload_mcp_servers(self):
            self.reload_calls += 1
            if self.reload_calls == 2:
                raise RuntimeError("second attach reload failed")

    state = GatedMCPState()
    session = EngineSession(
        session_id="mcp-concurrent",
        profile=None,
        workspace=str(tmp_path),
        mcp_server_names=[],
    )
    session.daemon = SimpleNamespace(
        state=state,
        identity="koordinator",
        agent_name="default",
        pid=5151,
        alive=True,
        launcher=None,
    )
    second_opened = asyncio.Event()
    _mock_named_session(monkeypatch, session, opened=second_opened)
    app = server.create_app()

    async def attach(names):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                "/sessions",
                headers={"x-mentiko-session-token": "test-session-token"},
                json=_attach_payload(session.session_id, names),
            )

    first_task = None
    second_task = None
    try:
        async with app.router.lifespan_context(app):
            first_task = asyncio.create_task(attach(["server-a"]), name="attach-a")
            await asyncio.wait_for(state.first_a_snapshot_started.wait(), timeout=2)
            second_opened.clear()
            second_task = asyncio.create_task(attach(["server-a", "server-b"]), name="attach-b")
            # The second request must remain outside the transaction while
            # the first request is blocked reading its persisted snapshot.
            await asyncio.wait_for(second_opened.wait(), timeout=2)
            overlapping_snapshot = state.server_b_snapshot_started.is_set()

            state.release_first_a_snapshot.set()
            first_response = await asyncio.wait_for(first_task, timeout=2)
            await asyncio.wait_for(state.server_b_snapshot_started.wait(), timeout=2)
            state.release_server_b_snapshot.set()
            second_response = await asyncio.wait_for(second_task, timeout=2)
    finally:
        state.release_first_a_snapshot.set()
        state.release_server_b_snapshot.set()
        tasks = [task for task in (first_task, second_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    assert not overlapping_snapshot
    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 503, second_response.text
    assert state.enabled == {"server-a": True, "server-b": False}
    assert session.mcp_server_names == ["server-a"]
    assert state.reload_calls == 3  # first apply, failed apply, rollback reload


@pytest.mark.asyncio
async def test_cancelled_mcp_attach_finishes_rollback_before_retry(monkeypatch, tmp_path):
    """Cancellation after enable restores both persisted and wrapper state."""
    import httpx

    class BlockingMCPState:
        def __init__(self):
            self.enabled = {"server-a": False}
            self.reload_calls = 0
            self.first_reload_started = asyncio.Event()
            self.release_first_reload = asyncio.Event()
            self.rollback_started = asyncio.Event()
            self.release_rollback = asyncio.Event()

        async def test_mcp_server(self, name):
            return {"found": True, "enabled": self.enabled[name]}

        async def enable_mcp_server(self, name):
            self.enabled[name] = True

        async def disable_mcp_server(self, name):
            self.rollback_started.set()
            await self.release_rollback.wait()
            self.enabled[name] = False

        async def reload_mcp_servers(self):
            self.reload_calls += 1
            if self.reload_calls == 1:
                self.first_reload_started.set()
                await self.release_first_reload.wait()

    state = BlockingMCPState()
    session = EngineSession(
        session_id="mcp-cancelled",
        profile=None,
        workspace=str(tmp_path),
        mcp_server_names=[],
    )
    session.daemon = SimpleNamespace(
        state=state,
        identity="koordinator",
        agent_name="default",
        pid=5152,
        alive=True,
        launcher=None,
    )
    _mock_named_session(monkeypatch, session)
    app = server.create_app()
    payload = _attach_payload(session.session_id, ["server-a"])
    headers = {"x-mentiko-session-token": "test-session-token"}

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                request_task = asyncio.create_task(client.post("/sessions", headers=headers, json=payload))
                await asyncio.wait_for(state.first_reload_started.wait(), timeout=2)
                assert state.enabled["server-a"] is True

                request_task.cancel()
                await asyncio.wait_for(state.rollback_started.wait(), timeout=2)
                assert session.mcp_server_names == []
                assert state.enabled["server-a"] is True

                # A second cancellation while rollback is in-flight must not
                # cancel the shielded state restore.
                request_task.cancel()
                await asyncio.sleep(0)
                assert not request_task.done()
                state.release_rollback.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request_task, timeout=2)

                assert state.enabled["server-a"] is False
                assert state.reload_calls == 2  # canceled apply + completed rollback

                retried = await client.post("/sessions", headers=headers, json=payload)
                assert retried.status_code == 200, retried.text
                assert state.enabled["server-a"] is True
                assert session.mcp_server_names == ["server-a"]
                assert state.reload_calls == 3
    finally:
        state.release_first_reload.set()
        state.release_rollback.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_attach", [False, True], ids=["success", "rollback"])
async def test_attach_does_not_reenable_disconnected_mcp_server(
    monkeypatch, tmp_path, registry, fail_attach
):
    """Adding another server must not undo an explicit MCP disconnect."""
    import httpx

    from kollabor.state.snapshots import McpServerInfo, McpSnapshot

    class MCPState:
        def __init__(self):
            self.enabled = {"server-a": True, "server-b": False}
            self.connected = {"server-a": True, "server-b": False}
            self.reload_calls = 0

        async def get_mcp_state(self):
            servers = [
                McpServerInfo(
                    name=name,
                    enabled=self.enabled[name],
                    connected=self.connected[name],
                    tool_count=0,
                    tools=[],
                )
                for name in self.enabled
            ]
            return McpSnapshot(
                total_servers=len(servers),
                connected_servers=sum(server.connected for server in servers),
                servers=servers,
            )

        async def test_mcp_server(self, name):
            return {"found": True, "enabled": self.enabled[name]}

        async def enable_mcp_server(self, name):
            self.enabled[name] = True

        async def disable_mcp_server(self, name):
            self.enabled[name] = False
            self.connected[name] = False

        async def reload_mcp_servers(self):
            self.reload_calls += 1
            if fail_attach and self.reload_calls == 1:
                raise RuntimeError("simulated attach reload failure")
            self.connected = dict(self.enabled)
            return {}

    state = MCPState()
    session = EngineSession(
        session_id="mcp-disconnect-attach",
        profile=None,
        workspace=str(tmp_path),
        mcp_server_names=["server-a"],
    )
    session.daemon = SimpleNamespace(
        state=state,
        identity="koordinator",
        agent_name="default",
        pid=5153,
        alive=True,
        launcher=None,
    )
    registry[session.session_id] = session
    _mock_named_session(monkeypatch, session)
    app = server.create_app()
    headers = {"x-mentiko-session-token": "test-session-token"}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            disconnected = await client.post(
                f"/sessions/{session.session_id}/mcp/server-a/disconnect"
            )
            assert disconnected.status_code == 200, disconnected.text
            assert state.enabled["server-a"] is False

            response = await client.post(
                "/sessions",
                headers=headers,
                json=_attach_payload(session.session_id, ["server-b"]),
            )

    assert response.status_code == (503 if fail_attach else 200), response.text
    assert state.enabled["server-a"] is False
    assert state.enabled["server-b"] is (not fail_attach)
    assert session.mcp_server_names == ([] if fail_attach else ["server-b"])


@pytest.mark.asyncio
async def test_connect_waits_until_attach_rollback_finishes(
    monkeypatch, tmp_path, registry
):
    """A successful connect cannot be undone by an overlapping attach rollback."""
    import httpx

    from kollabor.state.snapshots import McpServerInfo, McpSnapshot

    class GatedMCPState:
        def __init__(self):
            self.enabled = {"server-b": False}
            self.connected = {"server-b": False}
            self.reload_calls = 0
            self.attach_reload_started = asyncio.Event()
            self.release_attach_reload = asyncio.Event()
            self.connect_snapshot_started = asyncio.Event()

        async def get_mcp_state(self):
            self.connect_snapshot_started.set()
            server_info = McpServerInfo(
                name="server-b",
                enabled=self.enabled["server-b"],
                connected=self.connected["server-b"],
                tool_count=0,
                tools=[],
            )
            return McpSnapshot(
                total_servers=1,
                connected_servers=int(server_info.connected),
                servers=[server_info],
            )

        async def test_mcp_server(self, name):
            return {"found": True, "enabled": self.enabled[name]}

        async def enable_mcp_server(self, name):
            self.enabled[name] = True

        async def disable_mcp_server(self, name):
            self.enabled[name] = False
            self.connected[name] = False

        async def reload_mcp_servers(self):
            self.reload_calls += 1
            if self.reload_calls == 1:
                self.attach_reload_started.set()
                await self.release_attach_reload.wait()
                raise RuntimeError("simulated attach reload failure")
            self.connected = dict(self.enabled)
            return {}

    state = GatedMCPState()
    session = EngineSession(
        session_id="mcp-connect-rollback",
        profile=None,
        workspace=str(tmp_path),
        mcp_server_names=[],
    )
    session.daemon = SimpleNamespace(
        state=state,
        identity="koordinator",
        agent_name="default",
        pid=5154,
        alive=True,
        launcher=None,
    )
    registry[session.session_id] = session
    _mock_named_session(monkeypatch, session)
    app = server.create_app()
    headers = {"x-mentiko-session-token": "test-session-token"}
    attach_task = None
    connect_task = None

    async def attach(client):
        return await client.post(
            "/sessions",
            headers=headers,
            json=_attach_payload(session.session_id, ["server-b"]),
        )

    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                attach_task = asyncio.create_task(attach(client))
                await asyncio.wait_for(state.attach_reload_started.wait(), timeout=2)

                connect_task = asyncio.create_task(
                    sessions_route.connect_server(session.session_id, "server-b")
                )
                # Give the route one event-loop turn. With the shared lock it
                # waits before reading or mutating persisted MCP state.
                await asyncio.sleep(0)
                connect_overlapped = state.connect_snapshot_started.is_set()

                state.release_attach_reload.set()
                attach_response = await asyncio.wait_for(attach_task, timeout=2)
                connect_response = await asyncio.wait_for(connect_task, timeout=2)
    finally:
        state.release_attach_reload.set()
        for task in (attach_task, connect_task):
            if task is not None and not task.done():
                task.cancel()
        pending = [task for task in (attach_task, connect_task) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    assert not connect_overlapped
    assert attach_response.status_code == 503, attach_response.text
    assert connect_response["ok"] is True
    assert state.enabled["server-b"] is True
    assert state.connected["server-b"] is True
    assert session.mcp_server_names == ["server-b"]
    assert state.reload_calls == 3  # failed apply, rollback, then serialized connect
