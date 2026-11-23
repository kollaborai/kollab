"""Application registration used by the Hub stop handoff."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import kollabor.application as application_module
from kollabor_config import config_utils
from plugins.hub.plugin import HubPlugin


@pytest.mark.asyncio
async def test_hub_stop_hands_off_to_the_initialized_application(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    home = tmp_path / "home"
    global_config = tmp_path / "global"
    project.mkdir()
    home.mkdir()
    global_config.mkdir()
    (global_config / "config.json").write_text(
        json.dumps({"kollabor": {"llm": {"default_profile": "claude-code"}}})
    )
    monkeypatch.chdir(project)
    monkeypatch.delenv("KOLLAB_HUB_PROJECT_SCOPED", raising=False)

    registry = SimpleNamespace(
        get_merged_config=lambda: {},
        list_plugins=lambda: [],
        instantiate_plugins=lambda *args, **kwargs: {},
    )
    with (
        patch.object(config_utils, "get_config_directory", return_value=global_config),
        patch.object(Path, "home", return_value=home),
    ):
        app = application_module.TerminalLLMChat(
            args=SimpleNamespace(simple=True), plugin_registry=registry
        )

    assert app.event_bus.get_service("app") is app
    assert app.running is False
    app.running = True

    plugin = object.__new__(HubPlugin)
    plugin.event_bus = app.event_bus
    plugin._identity = None
    plugin._schedule_stop_watchdog = lambda **kwargs: None

    def unexpected_standalone_fallback(coro):
        coro.close()
        raise AssertionError("Hub stop bypassed application cleanup")

    monkeypatch.setattr(asyncio, "ensure_future", unexpected_standalone_fallback)
    plugin._begin_hub_stop(timeout=8.0, reason="test stop")

    assert app.running is False
