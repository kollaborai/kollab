import asyncio
from types import SimpleNamespace

import pytest
from kollabor_engine.routes import sessions
from kollabor_engine.session import EngineSession

from kollabor.state import LocalStateService
from kollabor_agent.mcp_integration import MCPIntegration


@pytest.mark.asyncio
async def test_session_requested_mcp_server_does_not_change_saved_settings(
    tmp_path, monkeypatch
):
    from kollabor_agent import MCPManager

    manager = MCPManager(mcp_dir=tmp_path / "mcp")
    manager.save_config(
        {"servers": {"memory": {"command": "memory-server", "enabled": False}}}
    )

    integration = MCPIntegration.__new__(MCPIntegration)
    integration.mcp_servers = {"memory": {"command": "memory-server", "enabled": False}}
    integration.session_servers = {}
    integration.tool_registry = {}
    integration.server_connections = {}
    integration._active_connections = set()
    integration._cancel_requested = False
    integration.event_bus = None
    integration.session_id = "session-mcp-scope"
    integration.user_token = None
    integration._agent_manager = None
    integration.workspace = tmp_path
    integration.shutdown = _noop
    integration._load_mcp_config = lambda: integration.mcp_servers.update(
        manager.load_config()["servers"]
    )
    discovered_servers = []

    async def discover():
        discovered_servers.extend(
            name
            for name, config in integration.mcp_servers.items()
            if config.get("enabled", True)
        )
        return {name: {"status": "connected"} for name in discovered_servers}

    integration.discover_mcp_servers = discover
    integration._refresh_native_tools_after_reload = _noop
    state = LocalStateService(SimpleNamespace(mcp_integration=integration), None)
    state._mcp_manager = lambda: manager

    session = EngineSession.__new__(EngineSession)
    session.session_id = "session-mcp-scope"
    session.daemon = SimpleNamespace(state=state)
    session.mcp_server_names = ["memory"]

    await session._apply_mcp_servers()

    assert manager.load_config()["servers"]["memory"]["enabled"] is False
    assert integration.session_servers["memory"]["enabled"] is True
    assert discovered_servers == ["memory"]


@pytest.mark.asyncio
async def test_session_connect_keeps_saved_disabled_server_disabled(tmp_path, monkeypatch):
    from kollabor_agent import MCPManager

    manager = MCPManager(mcp_dir=tmp_path / "mcp")
    manager.save_config(
        {"servers": {"memory": {"command": "memory-server", "enabled": False}}}
    )
    integration = SimpleNamespace(
        mcp_servers={"memory": {"command": "memory-server", "enabled": False}},
        session_servers={},
    )
    state = LocalStateService(SimpleNamespace(mcp_integration=integration), None)
    state._mcp_manager = lambda: manager
    state.reload_mcp_servers = _noop
    session = SimpleNamespace(
        state=state, _mcp_lock=asyncio.Lock(), mcp_server_names=[]
    )
    monkeypatch.setattr(sessions, "get_session_registry", lambda: {"s": session})
    monkeypatch.setattr(sessions, "_mcp_snapshot", _snapshot)
    monkeypatch.setattr(
        sessions,
        "_find_server",
        lambda *_: SimpleNamespace(connected=False, tool_count=0, tools=[]),
    )

    await sessions.connect_server("s", "memory")

    assert manager.load_config()["servers"]["memory"]["enabled"] is False


async def _snapshot(_session):
    return object()


async def _noop():
    return {}


async def _noop(*_args, **_kwargs):
    return None
