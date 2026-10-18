"""Daemon-pool lifecycle regressions."""

import asyncio
import os
import subprocess
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from kollabor_engine import daemon_pool


def _close_current_event_loop() -> None:
    """Close and clear the non-running loop left on the main-thread policy."""
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        return
    if not loop.is_running():
        loop.close()
        policy.set_event_loop(None)


@pytest.fixture(scope="module", autouse=True)
def _release_pytest_asyncio_replacement_loop() -> Iterator[None]:
    """Release the clean loop pytest-asyncio 0.21 leaves after async tests."""
    yield
    _close_current_event_loop()


def test_close_current_event_loop_releases_policy_resource() -> None:
    """Module cleanup closes and clears its pytest-asyncio replacement loop."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    policy.set_event_loop(loop)

    _close_current_event_loop()

    assert loop.is_closed()
    with pytest.raises(RuntimeError, match="no current event loop"):
        policy.get_event_loop()


def test_web_sessions_use_the_hub_identity_pool() -> None:
    pool = daemon_pool.DaemonPool()
    here = os.getcwd()
    pool._bridge.get_agents = lambda use_cache=False: [
        {"identity": "lapis", "project": here},
        {"identity": "sapphire", "project": here},
    ]

    identity = pool._assign_identity()

    assert identity not in {"lapis", "sapphire"}
    assert not identity.startswith("web-")


def test_requested_busy_identity_is_rejected() -> None:
    pool = daemon_pool.DaemonPool()
    pool._bridge.get_agents = lambda use_cache=False: [{"identity": "lapis", "project": os.getcwd()}]

    with pytest.raises(ValueError, match="lapis.*already in use"):
        pool._assign_identity("lapis")


@pytest.mark.parametrize("name", ["koordinator", "lapis"])
def test_every_identity_is_held_per_folder(name, tmp_path) -> None:
    pool = daemon_pool.DaemonPool()
    elsewhere = {"identity": name, "project": str(tmp_path / "other")}
    pool._bridge.get_agents = lambda use_cache=False: [elsewhere]

    assert pool._assign_identity(name, str(tmp_path)) == name
    # "Next Free Gem" in this folder does not skip a gem that runs elsewhere.
    assert pool._assign_identity(None, str(tmp_path)) == "lapis"

    here = {"identity": name, "project": str(tmp_path)}
    pool._bridge.get_agents = lambda use_cache=False: [elsewhere, here]
    with pytest.raises(ValueError, match=f"{name}.*already in use"):
        pool._assign_identity(name, str(tmp_path))


def test_a_folder_inside_a_repo_shares_the_repos_hub(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    pool = daemon_pool.DaemonPool()
    pool._bridge.get_agents = lambda use_cache=False: [{"identity": "lapis", "project": str(repo)}]

    with pytest.raises(ValueError, match="lapis.*already in use"):
        pool._assign_identity("lapis", str(repo / "pkg"))


def test_a_global_hub_holds_every_name_for_every_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "false")
    pool = daemon_pool.DaemonPool()
    pool._bridge.get_agents = lambda use_cache=False: [{"identity": "lapis", "project": str(tmp_path / "other")}]

    with pytest.raises(ValueError, match="lapis.*already in use"):
        pool._assign_identity("lapis", str(tmp_path))


@pytest.mark.asyncio
async def test_spawn_closes_dead_existing_handle_before_replacement():
    pool = daemon_pool.DaemonPool()
    stale = SimpleNamespace(alive=False, close=AsyncMock())
    pool._daemons["sess_stale"] = stale

    replacement = SimpleNamespace(
        connect=AsyncMock(),
        identity="web-stale",
    )
    process = SimpleNamespace()

    async def await_socket(handle):
        assert "sess_stale" not in pool._daemons
        assert stale.close.await_count == 1
        return "/tmp/kollab-stale.sock"

    with (
        patch.object(daemon_pool.subprocess, "Popen", return_value=process),
        patch.object(daemon_pool, "DaemonHandle", return_value=replacement),
        patch.object(pool, "_await_socket", side_effect=await_socket),
    ):
        result = await pool.spawn("sess_stale", workspace="/tmp")

    assert result is replacement
    assert pool._daemons["sess_stale"] is replacement
    stale.close.assert_awaited_once()
    replacement.connect.assert_awaited_once_with("/tmp/kollab-stale.sock")


@pytest.mark.asyncio
@pytest.mark.parametrize(("user_token", "solo"), [("jwt-abc", True), (None, False)])
async def test_spawn_exports_session_env(user_token, solo, monkeypatch):
    # The daemon's MCP layer reads these from its env; without them the
    # mentiko MCP server dispatches UI effects to "global" and has no auth.
    # KOLLAB_HUB_SOLO keeps product sessions off the hub mesh.
    monkeypatch.setenv("MENTIKO_SESSION_TOKEN", "ambient-other-identity")
    monkeypatch.setenv("KOLLAB_HUB_SOLO", "1")
    pool = daemon_pool.DaemonPool()
    handle = SimpleNamespace(connect=AsyncMock(), identity="web-env")

    with (
        patch.object(daemon_pool.subprocess, "Popen", return_value=SimpleNamespace()) as popen,
        patch.object(daemon_pool, "DaemonHandle", return_value=handle),
        patch.object(pool, "_await_socket", AsyncMock(return_value="/tmp/k.sock")),
    ):
        await pool.spawn("sess_env", workspace="/tmp", user_token=user_token, solo=solo)

    env = popen.call_args.kwargs["env"]
    assert env["MENTIKO_SESSION_ID"] == "sess_env"
    assert env.get("MENTIKO_SESSION_TOKEN") == user_token
    assert (env.get("KOLLAB_HUB_SOLO") == "1") is solo


@pytest.mark.asyncio
async def test_inline_credentials_reach_the_daemon(monkeypatch):
    # POST /sessions with credentials builds a profile no config holds; the
    # daemon gets `--llm app-inline` and must rebuild it from its env, or it
    # fails "Profile not found" and the request times out 45s later.
    from kollabor_engine.session import INLINE_PROFILE, inline_profile_env

    from kollabor_ai.profile_manager import LLMProfile, ProfileManager

    inline = LLMProfile(
        name=INLINE_PROFILE, provider="openai", model="gpt-test", api_key="sk-test",
        base_url="http://127.0.0.1:9/v1", max_tokens=512, streaming=False,
    )
    assert inline_profile_env(LLMProfile(name="openai-oauth", provider="openai")) == {}
    pool = daemon_pool.DaemonPool()
    handle = SimpleNamespace(connect=AsyncMock(), identity="web-inline")
    with (
        patch.object(daemon_pool.subprocess, "Popen", return_value=SimpleNamespace()) as popen,
        patch.object(daemon_pool, "DaemonHandle", return_value=handle),
        patch.object(pool, "_await_socket", AsyncMock(return_value="/tmp/k.sock")),
    ):
        await pool.spawn(
            "sess_inline", workspace="/tmp", profile=INLINE_PROFILE,
            profile_env=inline_profile_env(inline),
        )

    argv, env = popen.call_args.args[0], popen.call_args.kwargs["env"]
    assert argv[argv.index("--llm") + 1] == INLINE_PROFILE
    for key, value in env.items():
        if key.startswith("KOLLAB_APP_INLINE_"):
            monkeypatch.setenv(key, value)
    manager = SimpleNamespace(_profiles={})
    assert ProfileManager._try_create_profile_from_env(manager, INLINE_PROFILE)
    rebuilt = manager._profiles[INLINE_PROFILE]
    for field in ("provider", "model", "api_key", "base_url", "max_tokens", "streaming", "supports_tools"):
        assert getattr(rebuilt, field) == getattr(inline, field), field


@pytest.mark.asyncio
async def test_spawn_retries_a_socket_that_is_not_listening_yet():
    # A dead daemon of the same gem can leave its socket file behind, and the
    # new daemon publishes presence before it binds: the first connect is refused.
    pool = daemon_pool.DaemonPool()
    handle = SimpleNamespace(
        connect=AsyncMock(side_effect=[ConnectionRefusedError(), None]),
        identity="web-retry",
        close=AsyncMock(),
    )

    with (
        patch.object(daemon_pool.subprocess, "Popen", return_value=SimpleNamespace()),
        patch.object(daemon_pool, "DaemonHandle", return_value=handle),
        patch.object(pool, "_await_socket", AsyncMock(return_value="/tmp/k.sock")),
    ):
        result = await pool.spawn("sess_retry", workspace="/tmp")

    assert result is handle
    assert handle.connect.await_count == 2
    handle.close.assert_not_awaited()


def _koordinator(folder, pid, socket_path):
    return {"identity": "koordinator", "project": str(folder), "pid": pid, "socket_path": str(socket_path)}


@pytest.mark.asyncio
async def test_spawn_attaches_to_the_daemon_in_its_own_folder(tmp_path):
    # Every folder runs its own koordinator. Until the new one publishes
    # presence, the only live koordinator is another folder's: attaching to it
    # wires the chat to the wrong agent.
    here, other = tmp_path / "here", tmp_path / "other"
    here.mkdir()
    other.mkdir()
    (tmp_path / "here.sock").touch()
    (tmp_path / "other.sock").touch()
    agents = [_koordinator(other, 111, tmp_path / "other.sock")]
    pool = daemon_pool.DaemonPool()
    pool._bridge.get_agents = lambda use_cache=False: list(agents)
    handle = SimpleNamespace(
        identity="koordinator", connect=AsyncMock(), launcher=SimpleNamespace(poll=lambda: None), pid=0
    )

    async def publish():
        await asyncio.sleep(0.3)
        agents.append(_koordinator(here, 222, tmp_path / "here.sock"))

    with (
        patch.object(daemon_pool.subprocess, "Popen", return_value=SimpleNamespace()),
        patch.object(daemon_pool, "DaemonHandle", return_value=handle),
    ):
        publisher = asyncio.create_task(publish())
        await pool.spawn("sess_here", identity="koordinator", workspace=str(here))
        await publisher

    handle.connect.assert_awaited_once_with(str(tmp_path / "here.sock"))
    assert handle.pid == 222


@pytest.mark.asyncio
async def test_failed_spawn_never_signals_another_folders_agent(tmp_path):
    # close() looks up the pid of a daemon that failed before its presence was
    # read; a same-named agent in another folder is not that daemon.
    handle = daemon_pool.DaemonHandle("sess_failed", "koordinator", SimpleNamespace())
    handle.project = str(tmp_path / "here")
    elsewhere = [_koordinator(tmp_path / "other", 4242, tmp_path / "other.sock")]

    with (
        patch.object(daemon_pool.HubBridge, "get_agents", return_value=elsewhere),
        patch.object(daemon_pool.os, "kill") as kill,
    ):
        await handle.close(timeout=0.1)

    assert handle.pid == 0
    kill.assert_not_called()
