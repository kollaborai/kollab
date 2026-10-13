"""Bounded ACP application startup integration checks.

These tests deliberately isolate config/home/socket state under ``tmp_path``;
real adapter qualification and the full deferred plugin graph remain separate.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import kollabor.application as application_module
from kollabor_config import config_utils
from plugins.hub.plugin import HubPlugin


@pytest.mark.asyncio
async def test_hub_plugin_real_init_and_cleanup_in_temp_scope(tmp_path, monkeypatch):
    project = tmp_path / "project"
    home = tmp_path / "home"
    global_config = tmp_path / "global"
    project.mkdir()
    home.mkdir()
    global_config.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("KOLLAB_HUB_SOLO", "1")
    monkeypatch.setenv("KOLLAB_HUB_SOCKET_DIR", str(tmp_path / "sockets"))
    (global_config / "config.json").write_text(
        json.dumps({"plugins": {"hub": {"enabled": True, "project_scoped": True}}})
    )

    config = SimpleNamespace(
        get=lambda key, default=None: {
            "plugins.hub.enabled": True,
            "plugins.hub.project_scoped": True,
        }.get(key, default),
        register_reload_callback=lambda callback: None,
    )
    event_bus = SimpleNamespace(
        register_service=lambda name, value: None,
        get_service=lambda name: None,
    )
    plugin = HubPlugin(event_bus=event_bus, config=config)

    with patch.object(config_utils, "get_config_directory", return_value=global_config), patch.object(
        Path, "home", return_value=home
    ):
        await plugin._do_initialize(
            args=SimpleNamespace(pipe=False),
            event_bus=event_bus,
            config=config,
            profile_manager=SimpleNamespace(active_profile_name="claude-code"),
        )
        assert plugin._identity is not None
        assert plugin._identity.solo is True
        assert plugin._presence is not None
        await plugin._start_hub()
        assert plugin._socket_server is not None
        await plugin.shutdown(exit_process=False)

    assert not list((tmp_path / "sockets").glob("*.sock"))


def test_terminal_constructor_uses_isolated_profile_fixture(tmp_path, monkeypatch):
    project = tmp_path / "project"
    home = tmp_path / "home"
    global_config = tmp_path / "global"
    project.mkdir()
    home.mkdir()
    global_config.mkdir()
    (global_config / "config.json").write_text(
        json.dumps({"kollabor": {"llm": {"default_profile": "claude-code"}}})
    )
    registry = SimpleNamespace(
        get_merged_config=lambda: {},
        list_plugins=lambda: [],
        instantiate_plugins=lambda *args, **kwargs: {},
    )
    monkeypatch.chdir(project)
    with patch.object(config_utils, "get_config_directory", return_value=global_config), patch.object(
        Path, "home", return_value=home
    ):
        app = application_module.TerminalLLMChat(
            args=SimpleNamespace(simple=True), plugin_registry=registry
        )
    assert app.profile_manager.get_active_profile().provider == "acp"
    assert app.profile_manager.get_active_profile().model == ""
