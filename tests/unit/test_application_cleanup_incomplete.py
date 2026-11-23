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


def _build_start_app(args, cleanup_results, cleanup_calls):
    app = object.__new__(application.TerminalLLMChat)
    app.args = args
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
    results = list(cleanup_results)

    async def cleanup(self):
        result = results.pop(0)
        cleanup_calls.append(result)
        self.running = False
        self.input_handler.running = False
        return result

    app.cleanup = MethodType(cleanup, app)
    return app


@pytest.mark.asyncio
async def test_start_surfaces_unconfirmed_cleanup(monkeypatch):
    monkeypatch.setattr(application, "EventDrivenRenderLoop", _RenderLoop)
    cleanup_calls = []
    app = _build_start_app(
        SimpleNamespace(_help_pending=False, _unknown_args=[]), [False], cleanup_calls
    )

    with pytest.raises(RuntimeError, match="cleanup"):
        await app.start()

    assert cleanup_calls == [False]
    assert app._startup_complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_result", [False, True])
async def test_cli_retries_incomplete_start_cleanup(monkeypatch, cleanup_result):
    monkeypatch.setattr(application, "EventDrivenRenderLoop", _RenderLoop)
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
    app = _build_start_app(args, [False, cleanup_result], cleanup_calls)

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
    monkeypatch.setattr(cli, "TerminalLLMChat", lambda **_: app)

    if cleanup_result:
        await cli.async_main()
    else:
        with pytest.raises(RuntimeError, match="cleanup"):
            await cli.async_main()

    assert cleanup_calls == [False, cleanup_result]
