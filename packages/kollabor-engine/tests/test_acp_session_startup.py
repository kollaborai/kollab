"""ACP session admission must wait for the adapter, not its daemon socket."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor.llm.acp_bridge import ACPError, ACPProfile
from kollabor.state import LocalStateService
from kollabor.state.snapshots import SystemInfoSnapshot
from kollabor_engine import session as session_module
from kollabor_engine.session import EngineSession


def session_with_daemon(info):
    state = SimpleNamespace(get_system_info=AsyncMock(side_effect=info))
    session = EngineSession("acp-startup", SimpleNamespace(name="proof", provider="acp"))
    session.daemon = SimpleNamespace(alive=True, state=state, startup_error="")
    return session


@pytest.mark.asyncio
async def test_socket_is_not_ready_until_deferred_startup_finishes():
    reached = asyncio.Event()
    ready = asyncio.Event()

    async def info():
        reached.set()
        return SystemInfoSnapshot(startup_state="ready" if ready.is_set() else "starting")

    session = session_with_daemon(info)
    wait = asyncio.create_task(session._await_acp_startup())
    try:
        await asyncio.wait_for(reached.wait(), 1)
        assert not wait.done()
        ready.set()
        await asyncio.wait_for(wait, 1)
    finally:
        if not wait.done():
            wait.cancel()
        await asyncio.gather(wait, return_exceptions=True)


@pytest.mark.asyncio
async def test_startup_failure_returns_actionable_daemon_error():
    async def info():
        return SystemInfoSnapshot(startup_state="failed", startup_error="Run claude and /login on this device.")

    with pytest.raises(RuntimeError, match="Run claude and /login"):
        await session_with_daemon(info)._await_acp_startup()


@pytest.mark.asyncio
async def test_failure_detail_survives_daemon_disconnect():
    async def info():
        raise ConnectionError("socket closed")

    session = session_with_daemon(info)
    session.daemon.startup_error = "ACP authentication required. Run codex login."
    with pytest.raises(RuntimeError, match="Run codex login"):
        await session._await_acp_startup()


@pytest.mark.asyncio
async def test_a_daemon_without_a_readiness_result_times_out(monkeypatch):
    monkeypatch.setattr(session_module, "ACP_STARTUP_TIMEOUT_SECONDS", 0.05)

    async def info():
        return SystemInfoSnapshot(startup_state="starting")

    with pytest.raises(TimeoutError, match="did not become ready"):
        await session_with_daemon(info)._await_acp_startup()


@pytest.mark.asyncio
async def test_missing_adapter_is_rejected_before_spawning(monkeypatch, tmp_path):
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    pool = SimpleNamespace(spawn=AsyncMock())
    monkeypatch.setattr(session_module, "get_daemon_pool", lambda: pool)
    session = EngineSession("missing-adapter", SimpleNamespace(
        name="proof", provider="acp", command=(str(tmp_path / "missing"),)
    ))
    with pytest.raises(ACPError, match="adapter executable not found"):
        await session.initialize()
    pool.spawn.assert_not_awaited()
    assert session.daemon is None


@pytest.mark.asyncio
async def test_readiness_failure_cleans_up_before_http_session_is_registered(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from kollabor_ai.profile_manager import ProfileManager
    from kollabor_engine.routes import sessions
    from starlette.requests import Request

    registry = {}
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    profile = SimpleNamespace(name="proof", provider="acp")
    daemon = SimpleNamespace(
        alive=True, startup_error="", state=SimpleNamespace(
            get_system_info=AsyncMock(return_value=SystemInfoSnapshot(
                startup_state="failed", startup_error="Claude login is required."
            ))
        )
    )
    pool = SimpleNamespace(spawn=AsyncMock(return_value=daemon), stop=AsyncMock())
    monkeypatch.setattr(session_module, "get_daemon_pool", lambda: pool)
    monkeypatch.setattr(sessions, "get_session_registry", lambda: registry)
    monkeypatch.setattr(ProfileManager, "get_profile", lambda self, name: profile)
    monkeypatch.setattr(ACPProfile, "from_dict", lambda *args: SimpleNamespace(resolve=lambda: None))
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/sessions"})
    with pytest.raises(HTTPException) as raised:
        await sessions.create_session(sessions.CreateSessionRequest(
            profile="proof", session_id="startup-failed"
        ), request)
    assert raised.value.status_code == 503
    assert "Claude login is required" in raised.value.detail
    assert registry == {}
    pool.stop.assert_awaited_once_with("startup-failed")


@pytest.mark.asyncio
async def test_system_info_round_trip_reads_current_startup_state():
    status = ["starting", ""]
    state = LocalStateService(llm_service=None, profile_manager=None,
                              startup_status=lambda: tuple(status))
    first = SystemInfoSnapshot.from_dict((await state.get_system_info()).to_dict())
    assert first.startup_state == "starting"
    status[:] = ["failed", "Adapter executable not found"]
    failed = SystemInfoSnapshot.from_dict((await state.get_system_info()).to_dict())
    assert (failed.startup_state, failed.startup_error) == tuple(status)
