"""Bounded ACP application startup integration checks.

These tests deliberately isolate config/home/socket state under ``tmp_path``;
real adapter qualification and the full deferred plugin graph remain separate.
"""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import kollabor.application as application_module
from kollabor_config import config_utils
from plugins.hub import presence
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
    socket_root = Path("/tmp") / f"a{os.getpid()}"
    socket_root.mkdir(exist_ok=True)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", socket_root)
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
        socket_path = Path(plugin._socket_server.socket_path)
        assert socket_path.exists()
        assert socket_path.is_relative_to(socket_root)
        await plugin.shutdown(exit_process=False)
        assert not socket_path.exists()

    assert not list(socket_root.glob("*.sock"))


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


@pytest.mark.asyncio
async def test_hub_readiness_barrier_waits_for_delayed_socket():
    plugin = HubPlugin()

    async def delayed_start():
        await asyncio.sleep(0.01)
        plugin._socket_server = object()

    plugin._startup_task = asyncio.create_task(delayed_start())
    await plugin.wait_until_ready(timeout=1.0)
    assert plugin._socket_server is not None


@pytest.mark.asyncio
async def test_register_hooks_waits_for_actual_delayed_hub_start(monkeypatch):
    class EventBus:
        async def register_hook(self, hook):
            return None

    plugin = HubPlugin(event_bus=EventBus())
    plugin._identity = SimpleNamespace(solo=True)
    plugin._cli_args = SimpleNamespace(attach=None)

    async def delayed_start():
        await asyncio.sleep(0.01)
        plugin._socket_server = object()

    monkeypatch.setattr(plugin, "_start_hub", delayed_start)
    await plugin.register_hooks()
    await plugin.wait_until_ready(timeout=1.0)
    assert plugin._socket_server is not None


@pytest.mark.asyncio
async def test_deferred_startup_orders_plugins_hub_ready_then_acp(monkeypatch):
    events = []

    class DelayedHub:
        async def wait_until_ready(self, timeout=30.0):
            events.append("hub-ready")

    class StopAfterStartupGate(Exception):
        pass

    class App:
        _attach_to = None
        _attach_socket = None
        pipe_mode = True
        running = True
        plugin_instances = {"hub": DelayedHub()}
        event_bus = SimpleNamespace(get_service=lambda name: None)
        args = SimpleNamespace(stay=True)
        input_handler = None
        _startup_ready = asyncio.Event()

        def create_background_task(self, coro, name):
            coro.close()

        async def _check_for_updates(self):
            return None

        async def _load_config_hooks(self):
            return None

        async def _initialize_llm_core(self):
            return None

        async def _display_provider_warning(self):
            return None

        async def _initialize_plugins(self):
            events.append("plugins")

        async def _initialize_acp_orchestrator(self):
            events.append(f"acp-before-ready={'hub-ready' not in events}")

        async def _process_cli_command(self):
            raise StopAfterStartupGate()

    app = App()
    with pytest.raises(StopAfterStartupGate):
        await application_module.TerminalLLMChat._deferred_startup(app)
    assert events == ["plugins", "hub-ready", "acp-before-ready=False"]
