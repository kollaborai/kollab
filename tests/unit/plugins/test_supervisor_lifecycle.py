"""The app owns the supervisor task, but safe mode never starts it."""

from types import SimpleNamespace


def test_application_schedules_supervisor_unless_safe_mode():
    from kollabor.application import TerminalLLMChat

    tasks = []

    def collect(coroutine, name):
        tasks.append(name)
        coroutine.close()

    app = SimpleNamespace(args=SimpleNamespace(no_plugins=False), create_background_task=collect)
    TerminalLLMChat._start_plugin_supervisor(app)
    assert tasks == ["plugin_service_supervisor"]
    app.args.no_plugins = True
    TerminalLLMChat._start_plugin_supervisor(app)
    assert tasks == ["plugin_service_supervisor"]
