"""A partially initialized app does not start an unmanaged supervisor."""
from kollabor.application import TerminalLLMChat


def test_supervisor_without_args(monkeypatch):
    app = object.__new__(TerminalLLMChat)
    tasks = []

    def collect(coroutine, name):
        tasks.append(name)
        coroutine.close()

    monkeypatch.setattr(app, "create_background_task", collect)
    app._start_plugin_supervisor()
    assert tasks == []
