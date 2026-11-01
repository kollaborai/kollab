"""Tests for discovery of detached sessions through hub presence."""
from types import SimpleNamespace

import pytest
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import sessions as sessions_route

from kollabor_ai.session_naming import session_display_name


def test_discover_sessions_lists_every_live_agent_with_a_socket(tmp_path, monkeypatch):
    # Every agent publishes launch_strategy "interactive" (terminal sessions and
    # --detached daemons alike), so it filters nothing. Gem names repeat across
    # folders: the presence agent id keeps the rows apart.
    socket = tmp_path / "agent.sock"
    socket.touch()
    bridge = HubBridge()
    monkeypatch.setattr(
        bridge,
        "get_agents",
        lambda use_cache=False: [
            {"agent_id": "a1", "identity": "koordinator", "launch_strategy": "interactive",
             "socket_path": str(socket), "pid": 12, "project": "/w/one",
             "session_log": "/k/conversations/2610082249-nexus-drift.jsonl"},
            {"agent_id": "a2", "identity": "koordinator", "launch_strategy": "interactive",
             "socket_path": str(socket), "pid": 13, "project": "/w/two"},
            {"agent_id": "a3", "identity": "gone", "socket_path": str(tmp_path / "gone.sock")},
        ],
    )

    found = bridge.discover_sessions(use_cache=False)

    assert [(item["session_id"], item["workspace"]) for item in found] == [("a1", "/w/one"), ("a2", "/w/two")]
    assert found[0]["identity"] == "koordinator"
    assert found[0]["name"] == "2610082249-nexus-drift"  # the conversation its terminal shows
    assert found[0]["daemon_pid"] == 12
    assert found[0]["external"] is True
    assert found[0]["source"] == "hub_presence"


def test_an_agent_that_has_not_published_its_socket_is_not_listed(tmp_path, monkeypatch):
    # #555: an agent's first presence record (#38) has no socket_path. Guessing
    # from its name found a same-named agent's socket in another folder, so a
    # new chat attached to that agent.
    import glob

    other = tmp_path / "other-folder" / "lapis.sock"
    other.parent.mkdir()
    other.touch()
    real = glob.glob
    monkeypatch.setattr(glob, "glob", lambda pattern, **kw: (
        [str(other)] if pattern.endswith("lapis.sock") else real(pattern, **kw)))
    bridge = HubBridge()
    starting = {"agent_id": "b1", "identity": "lapis", "pid": 14, "project": "/w/new"}
    monkeypatch.setattr(bridge, "get_agents", lambda use_cache=False: [starting])

    assert bridge._socket_path_for_agent(starting) is None
    assert bridge.discover_sessions(use_cache=False) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("by_identity", [False, True])
async def test_a_message_reaches_a_socket_published_after_the_cached_record(tmp_path, monkeypatch, by_identity):
    # Benitoite on #556: the cached first record, with no socket_path, hid the
    # socket the agent published next, so a message to it found none for up to
    # five seconds.
    import json
    import os
    import time
    from unittest.mock import AsyncMock

    from kollabor_engine import hub_bridge

    presence = tmp_path / "presence"
    presence.mkdir()
    record = presence / "c1.json"
    first = {"agent_id": "c1", "identity": "lapis", "pid": os.getpid(), "last_heartbeat": time.time()}
    record.write_text(json.dumps(first))
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [presence])
    bridge = HubBridge()
    assert bridge.get_agent("c1")["identity"] == "lapis"  # caches the first record
    socket = tmp_path / "lapis.sock"
    socket.touch()
    record.write_text(json.dumps({**first, "socket_path": str(socket)}))
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(bridge, "_query_socket_path", send)

    if by_identity:
        assert await bridge.query_socket_by_identity("lapis", "ping") == {"ok": True}
    else:
        assert await bridge.query_socket("c1", "ping") == {"ok": True}
    assert send.await_args.args[0] == str(socket)


def test_discover_sessions_carries_what_the_agent_publishes(tmp_path, monkeypatch):
    # The row reads the same before the engine opens the agent as after:
    # EngineSession.to_dict answers profile and model, so presence must too.
    socket = tmp_path / "agent.sock"
    socket.touch()
    bridge = HubBridge()
    monkeypatch.setattr(
        bridge,
        "get_agents",
        lambda use_cache=False: [
            {"agent_id": "a1", "identity": "koordinator", "socket_path": str(socket),
             "profile_name": "anthropic", "tags": {"model": "claude-opus-5-5"}},
            {"agent_id": "a2", "identity": "lapis", "socket_path": str(socket)},
        ],
    )

    found = bridge.discover_sessions(use_cache=False)

    assert (found[0]["profile"], found[0]["model"]) == ("anthropic", "claude-opus-5-5")
    assert (found[1]["profile"], found[1]["model"]) == ("", "")


def test_discover_sessions_uses_friendly_name_for_opaque_identity(tmp_path, monkeypatch):
    socket = tmp_path / "agent.sock"
    socket.touch()
    identity = "1c753a7d2def481084e3a64bcb09a7d2"
    bridge = HubBridge()
    monkeypatch.setattr(
        bridge,
        "get_agents",
        lambda use_cache=False: [
            {
                "identity": identity,
                "launch_strategy": "subprocess",
                "socket_path": str(socket),
            }
        ],
    )

    found = bridge.discover_sessions(use_cache=False)

    assert found[0]["session_id"] == identity  # no agent id: the identity stands in
    assert found[0]["name"] == session_display_name(identity)
    assert found[0]["name"] != identity


@pytest.mark.asyncio
async def test_list_sessions_merges_discovered_without_duplicates(monkeypatch):
    local = SimpleNamespace(
        session_id="local", alive=True, to_dict=lambda: {"session_id": "local"}
    )
    registry = sessions_route.get_session_registry()
    registry["local"] = local
    monkeypatch.setattr(
        sessions_route.HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [
            {"session_id": "local", "discovered": True},
            {"session_id": "remote", "discovered": True},
        ],
    )
    try:
        result = await sessions_route.list_sessions()
        assert [item["session_id"] for item in result["sessions"]] == ["local", "remote"]
        assert result["active_count"] == 2
        assert result["discovered"] == [{"session_id": "remote", "discovered": True, "instance": "remote"}]
    finally:
        registry.pop("local", None)
