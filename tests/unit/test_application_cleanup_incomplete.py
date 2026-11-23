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


def _cli_args(*, pipe=False, query=None):
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
        query=(query or "probe") if pipe else None,
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


def _build_start_app(
    args,
    cleanup_results,
    cleanup_calls,
    *,
    parsed_command=None,
    command_result=None,
):
    app = object.__new__(application.TerminalLLMChat)
    app.args = args
    app.pipe_mode = False
    app._attach_to = None
    app._startup_complete = True
    app._startup_ready = asyncio.Event()
    app.event_bus = _EventBus()
    async def execute_command(*args, **kwargs):
        return command_result

    app.renderer = SimpleNamespace(pipe_mode=False)
    app.input_handler = SimpleNamespace(
        running=False,
        start=_noop,
        slash_parser=SimpleNamespace(parse_command=lambda text: parsed_command),
        command_executor=SimpleNamespace(execute_command=execute_command),
    )
    app.llm_service = SimpleNamespace(
        register_hooks=_noop,
        api_service=SimpleNamespace(is_provider_available=lambda: True),
    )
    app._start_plugin_supervisor = MethodType(lambda self: None, app)
    app._display_banner = MethodType(_noop, app)
    app._deferred_startup = MethodType(_noop, app)
    app._load_config_hooks = MethodType(_noop, app)
    app._initialize_llm_core = MethodType(_noop, app)
    app._display_provider_warning = MethodType(_noop, app)
    app._initialize_plugins = MethodType(_noop, app)
    app._initialize_acp_orchestrator = MethodType(_noop, app)
    app.create_background_task = MethodType(
        lambda self, coro, name: asyncio.create_task(coro, name=name), app
    )
    results = list(cleanup_results)

    async def cleanup(self):
        result = results.pop(0)
        cleanup_calls.append(result)
        self.running = False
        self.input_handler.running = False
        self._cleanup_complete = result
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cleanup_results", "expected_calls"),
    [([False, True], [False, True]), ([False, False, False], [False, False, False])],
)
async def test_pipe_unknown_command_preserves_exit_status(
    cleanup_results, expected_calls, monkeypatch
):
    args = _cli_args(pipe=True, query="/unknown")
    cleanup_calls = []
    app = _build_start_app(args, cleanup_results, cleanup_calls)
    _configure_cli(monkeypatch, args, app)

    with pytest.raises(SystemExit) as exc:
        await cli.async_main()

    assert exc.value.code == 2
    assert cleanup_calls == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cleanup_results", "expected_calls"),
    [([False, True], [False, True]), ([False, False, False], [False, False, False])],
)
async def test_pipe_successful_command_exits_zero_only_after_cleanup(
    cleanup_results, expected_calls, monkeypatch
):
    args = _cli_args(pipe=True, query="/success")
    cleanup_calls = []
    command_result = SimpleNamespace(success=True, message=None, ui_config=None)
    app = _build_start_app(
        args,
        cleanup_results,
        cleanup_calls,
        parsed_command=object(),
        command_result=command_result,
    )
    _configure_cli(monkeypatch, args, app)

    if expected_calls[-1]:
        assert await cli.async_main() is None
    else:
        with pytest.raises(application.ApplicationCleanupIncompleteError):
            await cli.async_main()

    assert cleanup_calls == expected_calls


@pytest.mark.asyncio
async def test_interactive_command_preserves_exit_status_if_cleanup_is_uncertain():
    args = SimpleNamespace(_help_pending=False, _unknown_args=[], stay=False)
    cleanup_calls = []
    app = _build_start_app(args, [False], cleanup_calls)
    app._boot_projects_sweep = lambda: None
    app._start_parent_watchdog = lambda: None
    app._check_for_updates = _noop

    async def process_cli_command():
        return {"name": "test", "command_def": None}

    async def execute_cli_command(command):
        return 2

    app._process_cli_command = process_cli_command
    app._execute_cli_command = execute_cli_command

    with pytest.raises(SystemExit) as exc:
        await app._deferred_startup()

    assert exc.value.code == 2
    assert cleanup_calls == [False]
