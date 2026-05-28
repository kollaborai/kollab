"""Tests for the one-screen MCP AltView manager."""

import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "kollabor-agent" / "src"))

from kollabor.altview.command_integration import AltViewCommandIntegrator
from kollabor.commands.mcp_command import register_mcp_commands
from kollabor.commands.registry import SlashCommandRegistry
from kollabor.state.snapshots import McpServerInfo, McpSnapshot
from kollabor_tui.key_parser import KeyPress
from plugins.altview.mcp_wizard_altview import McpWizardAltView, build_mcp_entries


def _example_config():
    return {
        "servers": {
            "github": {
                "command": "npx github",
                "description": "GitHub tools",
                "enabled": False,
                "env": {"GITHUB_TOKEN": "your-github-token-here"},
            },
            "fetch": {
                "command": "npx fetch",
                "description": "Fetch URLs",
                "enabled": False,
            },
            "brave": {
                "command": "npx brave",
                "description": "Search",
                "enabled": False,
                "env": {"BRAVE_API_KEY": "your-brave-api-key-here"},
            },
        }
    }


def _current_config():
    return {
        "servers": {
            "github": {
                "command": "npx github",
                "description": "GitHub tools",
                "enabled": True,
                "env": {"GITHUB_TOKEN": "real-token"},
            },
            "local-only": {
                "command": "python server.py",
                "description": "Local server",
                "enabled": False,
            },
        }
    }


def _snapshot():
    return McpSnapshot(
        total_servers=2,
        total_tools=2,
        connected_servers=1,
        servers=[
            McpServerInfo(
                name="github",
                enabled=True,
                connected=True,
                tool_count=2,
                tools=["repo_search", "issue_read"],
            ),
            McpServerInfo(
                name="local-only",
                enabled=False,
                connected=False,
                tool_count=0,
                tools=[],
            ),
        ],
    )


def _key(name, char=None):
    return KeyPress(name=name, code=char or name, char=char)


class _MemoryMcpManager:
    def __init__(self, current_config=None, example_config=None):
        self.current_config = deepcopy(current_config or {"servers": {}})
        self.example_config = deepcopy(example_config or {"servers": {}})
        self.config_path = Path("/tmp/mcp_settings.json")
        self.saved = []
        self.enabled = []
        self.disabled = []

    def load_config(self):
        return deepcopy(self.current_config)

    def load_example_config(self):
        return deepcopy(self.example_config)

    def save_config(self, config):
        self.current_config = deepcopy(config)
        self.saved.append(deepcopy(config))

    def enable_server(self, name):
        self.enabled.append(name)
        self.current_config["servers"][name]["enabled"] = True
        return {"success": True}

    def disable_server(self, name):
        self.disabled.append(name)
        self.current_config["servers"][name]["enabled"] = False
        return {"success": True}


class _MemoryConfigService:
    def __init__(self, enabled=True):
        self.values = {"plugins.mcp.enabled": enabled}
        self.saved = []
        self.reload_notifications = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def save_key(self, key, value, save_target=None):
        self.values[key] = value
        self.saved.append((key, value, save_target))
        return True

    def _notify_reload_callbacks(self):
        self.reload_notifications += 1


def test_build_mcp_entries_merges_configured_available_and_runtime_state():
    entries = build_mcp_entries(_example_config(), _current_config(), _snapshot())
    by_name = {entry.name: entry for entry in entries}

    assert list(by_name) == ["brave", "fetch", "github", "local-only"]
    assert by_name["github"].source == "configured"
    assert by_name["github"].state == "active"
    assert by_name["github"].tools == ["repo_search", "issue_read"]
    assert by_name["github"].missing_env == []
    assert by_name["fetch"].source == "available"
    assert by_name["fetch"].state == "available"
    assert by_name["brave"].badges == ["available", "needs env"]
    assert by_name["local-only"].state == "disabled"


@pytest.mark.asyncio
async def test_mcp_altview_actions_update_config_and_runtime_services():
    manager = _MemoryMcpManager(_current_config(), _example_config())
    state_service = SimpleNamespace(
        get_mcp_state=AsyncMock(return_value=_snapshot()),
        reload_mcp_servers=AsyncMock(
            return_value={"configured": 2, "discovered": 2, "reconnected": 1}
        ),
        test_mcp_server=AsyncMock(
            return_value={"found": True, "connected": True, "tool_count": 2}
        ),
    )
    view = McpWizardAltView()
    view.set_context(mcp_manager=manager, state_service=state_service)
    view.set_config(manager.load_example_config(), manager.load_config())

    await view.on_enter(renderer=Mock())

    view._selected_index = view._entry_index("fetch")
    await view.handle_input(_key("a", "a"))
    assert "fetch" in manager.current_config["servers"]
    assert view._entry_by_name("fetch").source == "configured"

    view._selected_index = view._entry_index("github")
    await view.handle_input(_key("Space", " "))
    assert manager.disabled == ["github"]
    assert manager.current_config["servers"]["github"]["enabled"] is False

    await view.handle_input(_key("Space", " "))
    assert manager.enabled == ["github"]
    assert manager.current_config["servers"]["github"]["enabled"] is True

    await view.handle_input(_key("t", "t"))
    state_service.test_mcp_server.assert_awaited_with("github")
    assert "test ok" in view._status_message

    await view.handle_input(_key("r", "r"))
    state_service.reload_mcp_servers.assert_awaited_once()
    assert "reload ok" in view._status_message

    await view.handle_input(_key("d", "d"))
    assert "github" not in manager.current_config["servers"]
    assert view._entry_by_name("github").source == "available"


@pytest.mark.asyncio
async def test_mcp_altview_global_toggle_persists_and_stops_runtime():
    manager = _MemoryMcpManager(_current_config(), _example_config())
    config_service = _MemoryConfigService(enabled=True)
    integration = SimpleNamespace(shutdown=AsyncMock())
    view = McpWizardAltView()
    view.set_context(
        mcp_manager=manager,
        config_service=config_service,
        mcp_integration=integration,
    )

    await view.on_enter(renderer=Mock())

    await view.handle_input(_key("g", "g"))

    assert config_service.saved == [("plugins.mcp.enabled", False, "global")]
    assert config_service.reload_notifications == 1
    integration.shutdown.assert_awaited_once()
    assert view._global_enabled is False
    assert "MCP disabled" in view._status_message


@pytest.mark.asyncio
async def test_mcp_altview_global_toggle_enables_and_reloads_runtime():
    manager = _MemoryMcpManager(_current_config(), _example_config())
    config_service = _MemoryConfigService(enabled=False)
    state_service = SimpleNamespace(
        get_mcp_state=AsyncMock(return_value=_snapshot()),
        reload_mcp_servers=AsyncMock(return_value={"reconnected": 1}),
    )
    view = McpWizardAltView()
    view.set_context(
        mcp_manager=manager,
        config_service=config_service,
        state_service=state_service,
    )

    await view.on_enter(renderer=Mock())

    await view.handle_input(_key("g", "g"))

    assert config_service.saved == [("plugins.mcp.enabled", True, "global")]
    assert config_service.reload_notifications == 1
    state_service.reload_mcp_servers.assert_awaited_once()
    assert view._global_enabled is True
    assert "MCP enabled" in view._status_message


@pytest.mark.asyncio
async def test_mcp_altview_claims_bare_mcp_and_delegates_subcommands():
    registry = SlashCommandRegistry()
    legacy = register_mcp_commands(
        command_registry=registry,
        mcp_integration=Mock(),
        renderer=Mock(),
        app=Mock(),
    )
    legacy.handle_mcp = AsyncMock(return_value="legacy-show")
    registry.get_command("mcp").handler = legacy.handle_mcp

    integrator = AltViewCommandIntegrator(
        command_registry=registry,
        event_bus=Mock(),
        terminal_renderer=Mock(),
        app=Mock(),
    )

    assert integrator._register_plugin_commands(McpWizardAltView) is True
    command_def = registry.get_command("mcp")

    assert command_def.plugin_name == "altview_integrator"
    assert any(sub.name == "show" for sub in command_def.subcommands)
    result = await command_def.handler(SimpleNamespace(args=["show"], name="mcp"))
    assert result == "legacy-show"
    legacy.handle_mcp.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_altview_setup_alias_opens_same_manager_screen():
    registry = SlashCommandRegistry()
    legacy = register_mcp_commands(
        command_registry=registry,
        mcp_integration=Mock(),
        renderer=Mock(),
        app=Mock(),
    )
    legacy.handle_mcp = AsyncMock(return_value="legacy-setup")
    registry.get_command("mcp").handler = legacy.handle_mcp

    manager = _MemoryMcpManager(_current_config(), _example_config())
    event_bus = Mock()
    event_bus.get_service.return_value = None
    app = SimpleNamespace(
        event_bus=event_bus,
        llm_service=SimpleNamespace(mcp_integration=None),
        mcp_manager=manager,
    )
    integrator = AltViewCommandIntegrator(
        command_registry=registry,
        event_bus=event_bus,
        terminal_renderer=Mock(),
        app=app,
    )
    integrator._stack_manager = SimpleNamespace(push=AsyncMock(return_value=True))

    assert integrator._register_plugin_commands(McpWizardAltView) is True
    command_def = registry.get_command("mcp")
    result = await command_def.handler(SimpleNamespace(args=["setup"], name="mcp"))

    assert result.success
    legacy.handle_mcp.assert_not_awaited()
    integrator._stack_manager.push.assert_awaited_once()
    altview, session_name = integrator._stack_manager.push.await_args.args
    assert isinstance(altview, McpWizardAltView)
    assert session_name == "mcp"
