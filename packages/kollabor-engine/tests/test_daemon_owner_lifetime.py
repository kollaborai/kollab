"""Only explicitly parent-owned engine daemons receive a lifetime binding."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psutil
import pytest

from kollabor_engine import daemon_pool
from kollabor_engine import session as session_module
from kollabor_engine.session import EngineSession


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
async def test_pool_owner_binding_is_opt_in_and_overrides_stale_identity(monkeypatch, tmp_path, owned):
    monkeypatch.delenv("KOLLAB_PARENT_PID", raising=False)
    monkeypatch.delenv("KOLLAB_PARENT_CREATE_TIME", raising=False)
    pool = daemon_pool.DaemonPool()
    pool._assign_identity = lambda *_args: "owned-proof"
    handle = SimpleNamespace(connect=AsyncMock(), identity="owned-proof")
    with (
        patch.object(daemon_pool.subprocess, "Popen", return_value=SimpleNamespace()) as popen,
        patch.object(daemon_pool, "DaemonHandle", return_value=handle),
        patch.object(pool, "_await_socket", AsyncMock(return_value=str(tmp_path / "proof.sock"))),
    ):
        await pool.spawn(
            "owner-proof", workspace=str(tmp_path), stop_on_owner_exit=owned,
            profile_env={"KOLLAB_PARENT_PID": "stale", "KOLLAB_PARENT_CREATE_TIME": "stale"} if owned else {},
        )

    env = popen.call_args.kwargs["env"]
    if owned:
        assert int(env["KOLLAB_PARENT_PID"]) == os.getpid()
        assert float(env["KOLLAB_PARENT_CREATE_TIME"]) == psutil.Process(os.getpid()).create_time()
    else:
        assert "KOLLAB_PARENT_PID" not in env
        assert "KOLLAB_PARENT_CREATE_TIME" not in env


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
async def test_engine_forwards_only_its_explicit_lifetime_policy(monkeypatch, tmp_path, owned):
    pool = SimpleNamespace(spawn=AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(session_module, "get_daemon_pool", lambda: pool)
    session = EngineSession(
        "owner-proof", SimpleNamespace(name="proof", provider=""),
        workspace=str(tmp_path), approval_mode="", stop_on_owner_exit=owned,
    )
    session._track_events = AsyncMock()
    session.refresh_history = AsyncMock()

    await session.initialize()

    assert pool.spawn.call_args.kwargs["stop_on_owner_exit"] is owned
    await session._event_task


def test_ordinary_engine_session_does_not_bind_a_parent_lifetime(tmp_path):
    session = EngineSession(
        "ordinary", SimpleNamespace(name="proof", provider=""), workspace=str(tmp_path)
    )

    assert session.stop_on_owner_exit is False
