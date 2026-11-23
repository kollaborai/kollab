"""Top-level callers must not report success after incomplete cleanup."""

import asyncio
from types import MethodType, SimpleNamespace

import pytest

import kollabor.application as application
import kollabor.cli as cli


class _EventBus:
    def register_service(self, name, service):
        return None


class _RenderLoop:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run(self):
        return None


async def _noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_start_surfaces_unconfirmed_cleanup(monkeypatch):
    monkeypatch.setattr(application, "EventDrivenRenderLoop", _RenderLoop)
    app = object.__new__(application.TerminalLLMChat)
    app.args = SimpleNamespace(_help_pending=False, _unknown_args=[])
    app.pipe_mode = False
    app._attach_to = None
    app._startup_complete = True
    app._startup_ready = asyncio.Event()
    app.event_bus = _EventBus()
    app.renderer = SimpleNamespace()
    app.input_handler = SimpleNamespace(running=False, start=_noop)
    app.llm_service = SimpleNamespace(register_hooks=_noop)
    app._start_plugin_supervisor = MethodType(lambda self: None, app)
    app._display_banner = MethodType(_noop, app)
    app._deferred_startup = MethodType(_noop, app)
    app.create_background_task = MethodType(
        lambda self, coro, name: asyncio.create_task(coro, name=name), app
    )
    cleanup_calls = []

    async def cleanup_false(self):
        cleanup_calls.append(False)
        return False

    app.cleanup = MethodType(cleanup_false, app)

    with pytest.raises(RuntimeError, match="cleanup"):
        await app.start()

    assert cleanup_calls == [False]
    assert app._startup_complete is False


@pytest.mark.asyncio
async def test_cli_surfaces_failed_emergency_cleanup(monkeypatch):
    args = SimpleNamespace(
        mcp_hub=False,
        acp_prompt=None,
        acp_command=None,
        reset_config=False,
        cleanup_projects=False,
        update=False,
        font_dir=False,
        login=False,
        web_ui=False,
        restart=False,
        hub=None,
        attach=None,
        cli_command=None,
        _unknown_args=[],
        _help_pending=False,
        pipe=False,
        query=None,
        detached=False,
        system_prompt=None,
        agent=None,
        llm=None,
        model=None,
        effort=None,
        save=False,
        local=False,
        make_default_profile=False,
        skill=[],
        context=None,
        timeout=None,
    )
    discovery = SimpleNamespace(plugins_dir="plugins")
    cleanup_calls = []

    class _App:
        pipe_mode = False
        _startup_complete = False

        async def start(self, initial_message=None):
            return None

        async def cleanup(self):
            cleanup_calls.append(False)
            return False

    monkeypatch.setattr(cli, "setup_bootstrap_logging", lambda: None)
    monkeypatch.setattr(cli, "discover_plugin_args", lambda: ([], discovery))
    monkeypatch.setattr(cli, "parse_arguments", lambda _: args)
    monkeypatch.setattr(cli, "handle_early_plugin_args", lambda *args: False)
    monkeypatch.setattr(cli.sys, "argv", ["kollab"])
    monkeypatch.setattr(
        cli.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: True, read=lambda: ""),
    )
    monkeypatch.setattr("kollabor_plugins.PluginRegistry", lambda _: SimpleNamespace())
    monkeypatch.setattr(cli, "TerminalLLMChat", lambda **_: _App())

    with pytest.raises(RuntimeError, match="cleanup"):
        await cli.async_main()

    assert cleanup_calls == [False]
