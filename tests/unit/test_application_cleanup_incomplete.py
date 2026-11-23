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


class _FailingRenderLoop:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run(self):
        raise RuntimeError("original startup failure")


class _CancelledRenderLoop:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run(self):
        raise asyncio.CancelledError("injected cancellation")


async def _noop(*args, **kwargs):
    return None


def _cli_args(*, pipe=False):
    return SimpleNamespace(
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
        pipe=pipe,
        query="probe" if pipe else None,
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
        timeout="30s" if pipe else None,
    )


def _configure_cli(monkeypatch, args, app):
    discovery = SimpleNamespace(plugins_dir="plugins")
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
    args = _cli_args()
    cleanup_calls = []
    app = _build_start_app(args, [False, cleanup_result], cleanup_calls)
    _configure_cli(monkeypatch, args, app)

    if cleanup_result:
        await cli.async_main()
    else:
        with pytest.raises(
            application.ApplicationCleanupIncompleteError, match="cleanup"
        ) as exc:
            await cli.async_main()
        assert isinstance(exc.value.__cause__, application.ApplicationCleanupIncompleteError)

    assert cleanup_calls == [False, cleanup_result]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_result", [False, True])
async def test_cli_preserves_original_start_failure_after_cleanup_retry(
    monkeypatch, retry_result
):
    monkeypatch.setattr(application, "EventDrivenRenderLoop", _FailingRenderLoop)
    args = _cli_args()
    cleanup_calls = []
    app = _build_start_app(args, [False, retry_result], cleanup_calls)
    _configure_cli(monkeypatch, args, app)

    if retry_result:
        with pytest.raises(RuntimeError, match="original startup failure"):
            await cli.async_main()
    else:
        with pytest.raises(application.ApplicationCleanupIncompleteError) as exc:
            await cli.async_main()
        assert isinstance(exc.value.__cause__, RuntimeError)
        assert "original startup failure" in str(exc.value.__cause__)

    assert cleanup_calls == [False, retry_result]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_result", [False, True])
async def test_cli_preserves_original_pipe_failure_after_cleanup_retry(
    monkeypatch, retry_result
):
    monkeypatch.setattr(application, "EventDrivenRenderLoop", _FailingRenderLoop)
    args = _cli_args(pipe=True)
    cleanup_calls = []
    app = _build_start_app(args, [False, retry_result], cleanup_calls)

    async def fail_config_hooks(self):
        raise RuntimeError("original pipe startup failure")

    app._load_config_hooks = MethodType(fail_config_hooks, app)
    _configure_cli(monkeypatch, args, app)

    if retry_result:
        with pytest.raises(RuntimeError, match="original pipe startup failure"):
            await cli.async_main()
    else:
        with pytest.raises(application.ApplicationCleanupIncompleteError) as exc:
            await cli.async_main()
        assert isinstance(exc.value.__cause__, RuntimeError)
        assert "original pipe startup failure" in str(exc.value.__cause__)

    assert cleanup_calls == [False, retry_result]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_result", [False, True])
async def test_cli_preserves_cancellation_after_cleanup_retry(
    monkeypatch, retry_result
):
    monkeypatch.setattr(application, "EventDrivenRenderLoop", _CancelledRenderLoop)
    args = _cli_args()
    cleanup_calls = []
    app = _build_start_app(args, [False, retry_result], cleanup_calls)
    _configure_cli(monkeypatch, args, app)

    if retry_result:
        with pytest.raises(asyncio.CancelledError, match="injected cancellation"):
            await cli.async_main()
    else:
        with pytest.raises(application.ApplicationCleanupIncompleteError) as exc:
            await cli.async_main()
        assert isinstance(exc.value.__cause__, asyncio.CancelledError)
        assert "injected cancellation" in str(exc.value.__cause__)

    assert cleanup_calls == [False, retry_result]
