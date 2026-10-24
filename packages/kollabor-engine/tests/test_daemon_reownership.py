"""Reowned WebUI daemons can be detached and explicitly stopped later."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_engine.daemon_pool import DaemonHandle, DaemonPool
from kollabor_engine.session import EngineSession


def _sleeping_process() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _found(process: subprocess.Popen, tmp_path: Path) -> dict:
    return {
        "session_id": "web-session-2",
        "daemon_pid": process.pid,
        "identity": "lapis",
        "agent": "default",
        "workspace": str(tmp_path),
        "socket_path": str(tmp_path / "agent.sock"),
        "session_log": "",
        "created_at": datetime.now(timezone.utc).timestamp(),
        "conversation_id": "conversation-xyz",
    }


@pytest.mark.asyncio
async def test_reowned_pool_handle_detaches_on_restart_and_stops_on_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _sleeping_process()
    monkeypatch.setattr(DaemonHandle, "connect", AsyncMock())
    pool = DaemonPool()
    found = _found(process, tmp_path)
    try:
        handle = await pool.adopt(
            found["session_id"], found, owned=True, webui_owned=True
        )
        assert handle.owned is True
        assert handle.webui_owned is True
        assert await pool.detach(found["session_id"]) is True
        assert process.poll() is None

        # A fresh pool reowns the same PID from the durable row. Explicit stop
        # (the WebUI Delete path) now has authority to terminate it.
        next_pool = DaemonPool()
        reowned = await next_pool.adopt(
            found["session_id"], found, owned=True, webui_owned=True
        )
        assert reowned.pid == process.pid
        assert await next_pool.stop(found["session_id"]) is True
        process.wait(timeout=5)
    finally:
        _stop_process(process)


@pytest.mark.asyncio
async def test_ordinary_external_adoption_remains_detach_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _sleeping_process()
    monkeypatch.setattr(DaemonHandle, "connect", AsyncMock())
    pool = DaemonPool()
    found = _found(process, tmp_path)
    try:
        handle = await pool.adopt(found["session_id"], found)
        assert handle.owned is False
        assert handle.webui_owned is False
        await pool.stop(found["session_id"])
        assert process.poll() is None
    finally:
        _stop_process(process)


@pytest.mark.asyncio
async def test_engine_session_reown_delete_stops_owned_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    created_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    daemon = SimpleNamespace(launcher=None)
    pool = SimpleNamespace(
        adopt=AsyncMock(return_value=daemon),
        stop=AsyncMock(return_value=True),
        detach=AsyncMock(return_value=True),
    )

    async def noop(self) -> None:
        return None

    monkeypatch.setattr("kollabor_engine.session.get_daemon_pool", lambda: pool)
    monkeypatch.setattr(EngineSession, "_read_agent_facts", noop)
    monkeypatch.setattr(EngineSession, "_track_events", noop)
    monkeypatch.setattr(EngineSession, "_schedule_refresh", lambda self: None)

    found = {
        "session_id": "web-session-3",
        "identity": "lapis",
        "agent": "default",
        "workspace": str(Path.cwd()),
        "daemon_pid": 12345,
        "socket_path": "/tmp/agent.sock",
        "created_at": created_at.timestamp(),
        "conversation_id": "conversation-xyz",
    }
    session = await EngineSession.adopt(
        found,
        owned=True,
        webui_owned=True,
        created_at=created_at,
    )
    try:
        assert session.webui_owned is True
        assert session.external is False
        assert session.created_at == created_at
        assert session.conversation_id == "conversation-xyz"
        assert session._borrowed_daemon is False
        await session.shutdown()
        pool.stop.assert_awaited_once_with("web-session-3")
        pool.detach.assert_not_awaited()
    finally:
        for task in (session._facts_task, session._event_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


@pytest.mark.asyncio
async def test_lifespan_detaches_only_webui_owned_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    from kollabor_engine import server

    persistent = SimpleNamespace(
        session_id="ui-session",
        webui_owned=True,
        shutdown=AsyncMock(),
    )
    ordinary = SimpleNamespace(
        session_id="cli-session",
        webui_owned=False,
        shutdown=AsyncMock(),
    )
    monkeypatch.setattr(server, "_sessions", {"ui-session": persistent, "cli-session": ordinary})
    app = SimpleNamespace(state=SimpleNamespace(handover_store=None))

    async with server._engine_lifespan(app):
        assert set(server._sessions) == {"ui-session", "cli-session"}

    persistent.shutdown.assert_awaited_once_with(stop_daemon=False)
    ordinary.shutdown.assert_awaited_once_with(stop_daemon=True)
    assert server._sessions == {}


@pytest.mark.asyncio
async def test_lifespan_reowns_manifest_sessions_before_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    from kollabor_engine import server
    from kollabor_engine.hub_bridge import HubBridge

    row = {
        "session_id": "persisted-ui-session",
        "daemon_pid": 24680,
        "identity": "lapis",
    }
    adopted = SimpleNamespace(
        session_id=row["session_id"],
        webui_owned=True,
        shutdown=AsyncMock(),
    )

    class Store:
        closed = False

        def acquire(self):
            return [row]

        def prune_dead_sessions(self):
            return 0


        def register_session(self, session):
            return None

        def reown_rows(self, discovered):
            assert discovered == []
            return [row]

        def close(self):
            self.closed = True

    store = Store()
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self: [])
    adopt = AsyncMock(return_value=adopted)
    monkeypatch.setattr("kollabor_engine.session.EngineSession.adopt", adopt)
    app = SimpleNamespace(state=SimpleNamespace(handover_store=store))

    async with server._engine_lifespan(app):
        assert server._sessions[row["session_id"]] is adopted
        adopt.assert_awaited_once_with(row, owned=True, webui_owned=True)

    adopted.shutdown.assert_awaited_once_with(stop_daemon=False)
    assert store.closed is True


@pytest.mark.asyncio
async def test_reowned_handle_never_signals_a_recycled_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _sleeping_process()
    monkeypatch.setattr(DaemonHandle, "connect", AsyncMock())
    pool = DaemonPool()
    found = _found(process, tmp_path)
    try:
        handle = await pool.adopt(
            found["session_id"], found, owned=True, webui_owned=True
        )
        assert handle.process_started_at is not None
        handle.process_started_at -= 10

        await pool.stop(found["session_id"])
        assert process.poll() is None
    finally:
        _stop_process(process)


@pytest.mark.asyncio
async def test_reown_attach_failure_detaches_without_stopping_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _sleeping_process()

    async def fail_connect(_handle, _socket_path):
        raise RuntimeError("simulated attach failure")

    monkeypatch.setattr(DaemonHandle, "connect", fail_connect)
    pool = DaemonPool()
    found = _found(process, tmp_path)
    try:
        with pytest.raises(RuntimeError, match="simulated attach failure"):
            await pool.adopt(
                found["session_id"], found, owned=True, webui_owned=True
            )
        assert process.poll() is None
    finally:
        _stop_process(process)
