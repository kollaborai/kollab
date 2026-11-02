"""Detached CLI SIGTERM must use the normal application cleanup path."""

import asyncio
import signal

import pytest

import kollabor.cli as cli


def test_detached_sigterm_does_not_interrupt_run_and_restores_handler(monkeypatch):
    installed = []
    raised_interrupts = []

    def fake_signal(signum, handler):
        installed.append((signum, handler))
        return "previous-handler"

    def fake_run(coro):
        coro.close()
        assert installed
        try:
            installed[-1][1](signal.SIGTERM, None)
        except KeyboardInterrupt:
            raised_interrupts.append(True)

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(cli.asyncio, "run", fake_run)
    cli._run_async_main_with_sigterm_cleanup(True)

    assert raised_interrupts == []
    assert installed[0][0] == signal.SIGTERM
    assert installed[1] == (signal.SIGTERM, "previous-handler")


def test_detached_sigterm_during_async_child_cleanup_is_graceful(monkeypatch):
    original_signal = signal.signal
    sigterm_handlers = []

    def fake_signal(signum, handler):
        if signum == signal.SIGTERM:
            sigterm_handlers.append(handler)
            return "previous-handler"
        return original_signal(signum, handler)

    monkeypatch.setattr(signal, "signal", fake_signal)
    child_tasks = []
    cleanup_completed = []

    async def fake_async_main():
        child_started = asyncio.Event()

        async def child_cleanup():
            try:
                child_started.set()
                await asyncio.Event().wait()
            finally:
                # Model SIGTERM arriving while an owned connection is cleaning up.
                sigterm_handlers[0](signal.SIGTERM, None)
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    pass
                cleanup_completed.append(True)

        task = asyncio.create_task(child_cleanup(), name="owned-connection-cleanup")
        child_tasks.append(task)
        await child_started.wait()
        task.cancel()
        await asyncio.Event().wait()

    monkeypatch.setattr(cli, "async_main", fake_async_main)
    cli._run_async_main_with_sigterm_cleanup(True)

    child_task = child_tasks[0]
    assert child_task.cancelled()
    assert cleanup_completed == [True]



def test_detached_sigterm_before_asyncio_runner_starts_is_deferred(monkeypatch):
    original_signal = signal.signal
    original_asyncio_run = asyncio.run
    sigterm_handlers = []
    started = []
    cleaned = []

    def fake_signal(signum, handler):
        if signum == signal.SIGTERM:
            sigterm_handlers.append(handler)
            return "previous-handler"
        return original_signal(signum, handler)

    async def fake_async_main():
        try:
            started.append(True)
            await asyncio.Event().wait()
        finally:
            cleaned.append(True)

    def fake_run(coro):
        # The handler is installed before asyncio.run creates its main task.
        sigterm_handlers[0](signal.SIGTERM, None)
        return original_asyncio_run(coro)

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(cli, "async_main", fake_async_main)
    monkeypatch.setattr(cli.asyncio, "run", fake_run)
    cli._run_async_main_with_sigterm_cleanup(True)

    assert started == [True]
    assert cleaned == [True]


def test_detached_cancellation_without_sigterm_propagates(monkeypatch):
    async def cancelled_async_main():
        raise asyncio.CancelledError

    monkeypatch.setattr(cli, "async_main", cancelled_async_main)
    with pytest.raises(asyncio.CancelledError):
        cli._run_async_main_with_sigterm_cleanup(True)


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
