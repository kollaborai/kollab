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
        assert result["discovered"] == [{"session_id": "remote", "discovered": True}]
    finally:
        registry.pop("local", None)
