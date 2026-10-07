"""Tests for discovery of detached sessions through hub presence."""
from types import SimpleNamespace

import pytest
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import sessions as sessions_route

from kollabor_ai.session_naming import session_display_name


def test_discover_sessions_filters_non_attachable_and_missing_socket(tmp_path, monkeypatch):
    socket = tmp_path / "agent.sock"
    socket.touch()
    bridge = HubBridge()
    monkeypatch.setattr(
        bridge,
        "get_agents",
        lambda use_cache=False: [
            {"identity": "lapis", "launch_strategy": "subprocess", "socket_path": str(socket), "pid": 12},
            {"identity": "interactive", "launch_strategy": "interactive", "socket_path": str(socket)},
            {"identity": "gone", "launch_strategy": "subprocess", "socket_path": str(tmp_path / "gone.sock")},
        ],
    )

    found = bridge.discover_sessions(use_cache=False)

    assert [item["session_id"] for item in found] == ["lapis"]
    assert found[0]["discovered"] is True
    assert found[0]["attachable"] is False
    assert found[0]["actions_supported"] == []
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

    assert found[0]["session_id"] == identity
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


@pytest.mark.asyncio
async def test_get_session_returns_discovered(monkeypatch):
    monkeypatch.setattr(
        sessions_route.HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [{"session_id": "remote", "attachable": False, "actions_supported": []}],
    )
    result = await sessions_route.get_session("remote")
    assert result["attachable"] is False
    assert result["actions_supported"] == []
