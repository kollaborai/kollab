"""Managed owner identity and application loop shutdown regressions."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from kollabor.application import TerminalLLMChat


def test_parent_identity_does_not_accept_a_reused_pid(monkeypatch):
    parent = SimpleNamespace(
        is_running=lambda: True,
        status=lambda: psutil.STATUS_RUNNING,
        create_time=lambda: 42.0,
    )
    monkeypatch.setattr(psutil, "Process", lambda _pid: parent)

    assert TerminalLLMChat._parent_process_alive(123, 42.0)
    assert not TerminalLLMChat._parent_process_alive(123, 41.0)


def test_zombie_parent_does_not_keep_a_daemon_alive(monkeypatch):
    parent = SimpleNamespace(
        is_running=lambda: True,
        status=lambda: psutil.STATUS_ZOMBIE,
    )
    monkeypatch.setattr(psutil, "Process", lambda _pid: parent)

    assert not TerminalLLMChat._parent_process_alive(123, None)


def test_missing_parent_is_dead_but_unreadable_parent_is_not_assumed_dead(monkeypatch):
    def missing(_pid):
        raise psutil.NoSuchProcess(123)

    monkeypatch.setattr(psutil, "Process", missing)
    assert not TerminalLLMChat._parent_process_alive(123, 42.0)

    def inaccessible(_pid):
        raise psutil.AccessDenied(123)

    monkeypatch.setattr(psutil, "Process", inaccessible)
    assert TerminalLLMChat._parent_process_alive(123, 42.0)


@pytest.mark.asyncio
async def test_owner_exit_stops_both_foreground_loops_through_lifecycle_apis():
    app = object.__new__(TerminalLLMChat)
    app.running = True
    app.render_loop = SimpleNamespace(stop=Mock())
    app.input_handler = SimpleNamespace(stop=AsyncMock())
    app._parent_process_alive = Mock(return_value=False)

    await app._watch_parent_process(123, 42.0)

    assert app.running is False
    app.render_loop.stop.assert_called_once_with()
    app.input_handler.stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_birth_marker_is_consumed_before_orchestrator_child_inheritance(monkeypatch):
    app = object.__new__(TerminalLLMChat)
    app.running = True
    app._background_tasks = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def watcher(pid, birth):
        assert pid == 123 and birth == 42.0
        entered.set()
        await release.wait()

    app._watch_parent_process = watcher
    monkeypatch.setenv("KOLLAB_PARENT_PID", "123")
    monkeypatch.setenv("KOLLAB_PARENT_CREATE_TIME", "42.0")

    app._start_parent_watchdog()
    await entered.wait()

    assert "KOLLAB_PARENT_CREATE_TIME" not in os.environ
    assert os.environ["KOLLAB_PARENT_PID"] == "123"
    release.set()
    await asyncio.gather(*app._background_tasks)


def test_unmanaged_daemon_does_not_start_a_parent_watchdog(monkeypatch):
    app = object.__new__(TerminalLLMChat)
    app.create_background_task = Mock()
    monkeypatch.delenv("KOLLAB_PARENT_PID", raising=False)
    monkeypatch.delenv("KOLLAB_PARENT_CREATE_TIME", raising=False)

    app._start_parent_watchdog()

    app.create_background_task.assert_not_called()


@pytest.mark.parametrize("birth", ["nan", "inf", "0", "-1"])
def test_invalid_birth_marker_cannot_create_an_unbounded_owner_binding(monkeypatch, birth):
    app = object.__new__(TerminalLLMChat)
    app.create_background_task = Mock()
    monkeypatch.setenv("KOLLAB_PARENT_PID", "123")
    monkeypatch.setenv("KOLLAB_PARENT_CREATE_TIME", birth)

    with pytest.raises(ValueError, match="Invalid parent process identity"):
        app._start_parent_watchdog()

    app.create_background_task.assert_not_called()
