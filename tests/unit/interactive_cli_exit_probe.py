"""Subprocess probe for interactive CLI command exit/cleanup ownership."""

import asyncio
import sys
from types import MethodType, SimpleNamespace

import kollabor.application as application
import kollabor.cli as cli
import kollabor_plugins


class _EventBus:
    def register_service(self, name, service):
        return None


async def _noop(*args, **kwargs):
    return None


async def _run(scenario: str) -> None:
    release_foreground = asyncio.Event()

    class RenderLoop:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self):
            await release_foreground.wait()

    class InputHandler:
        def __init__(self):
            self.running = False

        async def start(self):
            await release_foreground.wait()

    cleanup_sequences = {
        "transient-success": [False, True, True],
        "transient-failure": [False, True, True],
        "persistent-success": [False, False, False],
    }
    cleanup_results = cleanup_sequences[scenario]
    cleanup_calls = []
    args = SimpleNamespace(
        _help_pending=False,
        _unknown_args=[],
        _start_time=None,
        no_plugins=True,
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
        stay=False,
    )
    app = object.__new__(application.TerminalLLMChat)
    app.args = args
    app.pipe_mode = False
    app._attach_to = None
    app._startup_complete = True
    app._startup_ready = asyncio.Event()
    app._background_tasks = []
    app.event_bus = _EventBus()
    app.renderer = SimpleNamespace()
    app.input_handler = InputHandler()
    app.llm_service = SimpleNamespace(register_hooks=_noop)
    app._start_plugin_supervisor = MethodType(lambda self: None, app)
    app._display_banner = MethodType(_noop, app)
    app._boot_projects_sweep = lambda: None
    app._start_parent_watchdog = lambda: None
    app._check_for_updates = _noop
    app._load_config_hooks = MethodType(_noop, app)
    app._initialize_llm_core = MethodType(_noop, app)
    app._display_provider_warning = MethodType(_noop, app)
    app._initialize_plugins = MethodType(_noop, app)
    app._initialize_acp_orchestrator = MethodType(_noop, app)

    async def process_command(self):
        return {"name": "test-command", "command_def": None}

    async def execute_command(self, command):
        return 7 if scenario == "transient-failure" else 0

    app._process_cli_command = MethodType(process_command, app)
    app._execute_cli_command = MethodType(execute_command, app)

    async def cleanup(self):
        result = cleanup_results.pop(0)
        cleanup_calls.append(result)
        self.running = False
        self.input_handler.running = False
        self._cleanup_complete = result
        if not result:
            self._startup_complete = False
        return result

    app.cleanup = MethodType(cleanup, app)
    original_create_task = application.TerminalLLMChat.create_background_task

    def create_background_task(self, coro, name="unnamed"):
        if name == "deferred_startup":
            startup_coro = coro

            async def release_after_startup():
                try:
                    await startup_coro
                finally:
                    # Keep the foreground loops pending until the child has
                    # either completed or raised its exit request.
                    release_foreground.set()

            coro = release_after_startup()
        return original_create_task(self, coro, name)

    app.create_background_task = MethodType(create_background_task, app)

    discovery = SimpleNamespace(plugins_dir="plugins")
    originals = (
        application.EventDrivenRenderLoop,
        kollabor_plugins.PluginRegistry,
        cli.setup_bootstrap_logging,
        cli.discover_plugin_args,
        cli.parse_arguments,
        cli.handle_early_plugin_args,
        cli.TerminalLLMChat,
        cli.sys.argv,
        cli.sys.stdin,
    )
    try:
        application.EventDrivenRenderLoop = RenderLoop
        kollabor_plugins.PluginRegistry = lambda *args, **kwargs: SimpleNamespace()
        cli.setup_bootstrap_logging = lambda: None
        cli.discover_plugin_args = lambda: ([], discovery)
        cli.parse_arguments = lambda _: args
        cli.handle_early_plugin_args = lambda *args: False
        cli.TerminalLLMChat = lambda **kwargs: app
        cli.sys.argv = ["kollab"]
        cli.sys.stdin = SimpleNamespace(isatty=lambda: True, read=lambda: "")
        await cli.async_main()
    finally:
        (
            application.EventDrivenRenderLoop,
            kollabor_plugins.PluginRegistry,
            cli.setup_bootstrap_logging,
            cli.discover_plugin_args,
            cli.parse_arguments,
            cli.handle_early_plugin_args,
            cli.TerminalLLMChat,
            cli.sys.argv,
            cli.sys.stdin,
        ) = originals


if __name__ == "__main__":
    asyncio.run(_run(sys.argv[1]))
