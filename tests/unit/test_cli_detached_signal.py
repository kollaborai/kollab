"""Detached CLI SIGTERM must use the normal application cleanup path."""

import signal

import kollabor.cli as cli


def test_detached_sigterm_routes_to_keyboard_interrupt_and_restores_handler(monkeypatch):
    installed = []

    def fake_signal(signum, handler):
        installed.append((signum, handler))
        return "previous-handler"

    def fake_run(coro):
        coro.close()
        assert installed
        installed[-1][1](signal.SIGTERM, None)

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(cli.asyncio, "run", fake_run)
    cli._run_async_main_with_sigterm_cleanup(True)

    assert installed[0][0] == signal.SIGTERM
    assert installed[1] == (signal.SIGTERM, "previous-handler")


def test_attached_or_native_run_does_not_install_sigterm_override(monkeypatch):
    calls = []

    def fake_signal(*args):
        calls.append(args)
        return None

    def fake_run(coro):
        coro.close()

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(cli.asyncio, "run", fake_run)
    cli._run_async_main_with_sigterm_cleanup(False)

    assert calls == []
