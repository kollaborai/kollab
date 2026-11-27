"""#1459: old agents restart in place through the engine's existing launcher."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_engine import session as engine_session
from kollabor_engine.routes import sessions as session_routes
from kollabor_engine.session import EngineSession

OLD_AGENT_ERROR = "this agent is too old to restart itself: quit it and start it again"


@pytest.mark.asyncio
async def test_restart_route_uses_engine_fallback_for_only_the_old_agent_error(monkeypatch):
    legacy_restart = AsyncMock(side_effect=ValueError(OLD_AGENT_ERROR))
    engine_restart = AsyncMock(return_value={"restarting": True, "waiting_for_turn": False})
    session = SimpleNamespace(
        state=SimpleNamespace(restart_agent=legacy_restart),
        restart_outdated_agent=engine_restart,
    )
    monkeypatch.setattr(session_routes, "get_session_registry", lambda: {"s1": session})

    result = await session_routes.restart_agent("s1")

    assert result == {
        "ok": True,
        "session_id": "s1",
        "restarting": True,
        "waiting_for_turn": False,
    }
    engine_restart.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_engine_fallback_signals_the_selected_generation_then_reuses_session_start(
    monkeypatch, tmp_path
):
    class Handle:
        identity = "lapis"
        project = str(tmp_path)
        socket_path = str(tmp_path / "lapis.sock")
        pid = 1234
        process_started_at = 12.5
        _daemon_image = "image-1"
        stopped = False

        @property
        def alive(self):
            return not self.stopped

    handle = Handle()
    presence = {
        "identity": "lapis",
        "agent_id": "image-1",
        "pid": 1234,
        "started_at": 12.5,
    }
    bridge = SimpleNamespace(
        get_agent_by_identity=lambda identity, use_cache, project: presence
    )
    monkeypatch.setattr(engine_session, "HubBridge", lambda: bridge, raising=False)

    async def signal_shutdown(socket_path, **kwargs):
        assert socket_path == handle.socket_path
        handle.stopped = True
        return True

    from plugins.hub.messenger import AgentMessenger

    signal = AsyncMock(side_effect=signal_shutdown)
    monkeypatch.setattr(AgentMessenger, "signal_shutdown", signal)
    monkeypatch.setattr(
        AgentMessenger,
        "request_status",
        AsyncMock(return_value={"type": "status", "state": "idle"}),
    )
    session = EngineSession(
        session_id="s1",
        profile=SimpleNamespace(name="default", provider="custom"),
        workspace=str(tmp_path),
        identity="lapis",
        webui_owned=True,
    )
    session.conversation_id = "conversation-existing"
    session.daemon = handle
    session._borrowed_daemon = True
    session.external = True
    session.shutdown = AsyncMock()
    session.initialize = AsyncMock()

    result = await session.restart_outdated_agent()

    assert result == {"restarting": True, "waiting_for_turn": False}
    signal.assert_awaited_once_with(
        handle.socket_path,
        reason="Restart Agent requested by the web UI",
        timeout=3.0,
        expected_agent_id="image-1",
        expected_pid=1234,
        expected_started_at=12.5,
        generation_check=False,
    )
    session.shutdown.assert_awaited_once_with()
    assert session.initialize.await_args.kwargs["resume_conversation_id"] == (
        "conversation-existing"
    )
    assert session._borrowed_daemon is False
    assert session.external is False


@pytest.mark.asyncio
async def test_legacy_shutdown_action_keeps_generation_fields_without_preflight(monkeypatch):
    from plugins.hub.messenger import AgentMessenger

    requests = []

    class Reader:
        async def readline(self):
            return b'{"type":"ack"}\n'

    class Writer:
        def write(self, data):
            requests.append(json.loads(data.decode()))

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    monkeypatch.setattr(
        AgentMessenger,
        "_open",
        AsyncMock(return_value=(Reader(), Writer())),
    )

    accepted = await AgentMessenger.signal_shutdown(
        "/tmp/old-agent.sock",
        reason="Restart Agent requested by the web UI",
        expected_agent_id="image-1",
        expected_pid=1234,
        expected_started_at=12.5,
        generation_check=False,
    )

    assert accepted is True
    assert requests == [
        {
            "action": "shutdown",
            "reason": "Restart Agent requested by the web UI",
            "expected_agent_id": "image-1",
            "expected_pid": 1234,
            "expected_started_at": 12.5,
        }
    ]


@pytest.mark.asyncio
async def test_engine_fallback_persists_replacement_as_soon_as_it_spawns(
    monkeypatch, tmp_path
):
    class Handle:
        identity = "lapis"
        project = str(tmp_path)
        socket_path = str(tmp_path / "lapis.sock")
        pid = 1234
        process_started_at = 12.5
        _daemon_image = "image-1"
        stopped = False

        @property
        def alive(self):
            return not self.stopped

    handle = Handle()
    presence = {
        "identity": "lapis",
        "agent_id": "image-1",
        "pid": 1234,
        "started_at": 12.5,
    }
    bridge = SimpleNamespace(
        get_agent_by_identity=lambda identity, use_cache, project: presence
    )
    monkeypatch.setattr(engine_session, "HubBridge", lambda: bridge, raising=False)

    from plugins.hub.messenger import AgentMessenger

    monkeypatch.setattr(
        AgentMessenger,
        "request_status",
        AsyncMock(return_value={"type": "status", "state": "idle"}),
    )
    monkeypatch.setattr(
        AgentMessenger,
        "signal_shutdown",
        AsyncMock(side_effect=lambda *_args, **_kwargs: setattr(handle, "stopped", True) or True),
    )

    session = EngineSession(
        session_id="s1",
        profile=SimpleNamespace(name="default", provider="custom"),
        workspace=str(tmp_path),
        identity="lapis",
        webui_owned=True,
    )
    session.daemon = handle
    session._borrowed_daemon = True
    persisted_pids = []
    session.on_persistence_changed = lambda candidate: persisted_pids.append(
        candidate.daemon.pid
    )
    session.shutdown = AsyncMock()

    async def initialize(*, on_spawned=None, **_kwargs):
        session.daemon = SimpleNamespace(pid=5678)
        if on_spawned is not None:
            await on_spawned(session)

    session.initialize = AsyncMock(side_effect=initialize)

    await session.restart_outdated_agent()

    assert persisted_pids == [5678]


@pytest.mark.asyncio
async def test_engine_fallback_waits_for_active_turn_before_signalling_shutdown(
    monkeypatch, tmp_path
):
    class Handle:
        identity = "lapis"
        project = str(tmp_path)
        socket_path = str(tmp_path / "lapis.sock")
        pid = 1234
        process_started_at = 12.5
        _daemon_image = "image-1"
        stopped = False

        @property
        def alive(self):
            return not self.stopped

    handle = Handle()
    presence = {
        "identity": "lapis",
        "agent_id": "image-1",
        "pid": 1234,
        "started_at": 12.5,
    }
    bridge = SimpleNamespace(
        get_agent_by_identity=lambda identity, use_cache, project: presence
    )
    monkeypatch.setattr(engine_session, "HubBridge", lambda: bridge, raising=False)

    from plugins.hub.messenger import AgentMessenger

    states = iter(("working", "idle"))
    events = []

    async def request_status(socket_path, **kwargs):
        state = next(states)
        events.append(f"status:{state}")
        return {"type": "status", "state": state}

    async def signal_shutdown(socket_path, **kwargs):
        events.append("shutdown")
        handle.stopped = True
        return True

    monkeypatch.setattr(AgentMessenger, "request_status", AsyncMock(side_effect=request_status))
    monkeypatch.setattr(AgentMessenger, "signal_shutdown", AsyncMock(side_effect=signal_shutdown))

    session = EngineSession(
        session_id="s1",
        profile=SimpleNamespace(name="default", provider="custom"),
        workspace=str(tmp_path),
        identity="lapis",
    )
    session.daemon = handle
    session._borrowed_daemon = True
    session.shutdown = AsyncMock()
    session.initialize = AsyncMock()

    result = await session.restart_outdated_agent()

    assert result == {"restarting": True, "waiting_for_turn": True}
    assert events == ["status:working", "status:idle", "shutdown"]


@pytest.mark.asyncio
async def test_engine_fallback_refuses_a_replaced_hub_identity(monkeypatch, tmp_path):
    class Handle:
        identity = "lapis"
        project = str(tmp_path)
        socket_path = str(tmp_path / "lapis.sock")
        pid = 1234
        process_started_at = 12.5
        _daemon_image = "image-1"
        alive = True

    session = EngineSession(
        session_id="s1",
        profile=SimpleNamespace(name="default", provider="custom"),
        workspace=str(tmp_path),
        identity="lapis",
    )
    session.daemon = Handle()
    session.shutdown = AsyncMock()
    session.initialize = AsyncMock()
    bridge = SimpleNamespace(
        get_agent_by_identity=lambda identity, use_cache, project: {
            "identity": "lapis",
            "agent_id": "replacement-image",
            "pid": 4321,
            "started_at": 98.7,
        }
    )
    monkeypatch.setattr(engine_session, "HubBridge", lambda: bridge, raising=False)

    with pytest.raises(ValueError, match="changed"):
        await session.restart_outdated_agent()

    session.shutdown.assert_not_awaited()
    session.initialize.assert_not_awaited()
