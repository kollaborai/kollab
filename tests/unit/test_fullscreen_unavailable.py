"""FullScreenManager refuses to launch where no terminal can key the session.

Same contract as AltViewStackManager.push (test_altview_unavailable.py): raise
AltViewUnavailable, record the plugin in the attempt sink, never enter modal
mode. Without it a web ``/matrix`` blocked the turn forever.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import kollabor_tui.fullscreen.manager as manager_module
from kollabor_tui.altview.stack_manager import (
    AltViewUnavailable,
    unavailable_attempts,
)
from kollabor_tui.fullscreen.manager import FullScreenManager
from tests.unit.test_altview_unavailable import _run_web


class _Session:
    """Stand-in session: launching it for real would not return in a test."""

    def __init__(self, plugin, event_bus, **kwargs):
        self.plugin = plugin
        self.running = False

    async def run(self):
        return True

    def get_stats(self):
        return {}


def _manager(renderer=None):
    bus = SimpleNamespace(emit_with_hooks=AsyncMock())
    mgr = FullScreenManager(bus, renderer)
    plugin = SimpleNamespace(name="matrix", metadata=SimpleNamespace(aliases=["rain"]))
    assert mgr.register_plugin(plugin)
    return mgr, bus


def _assert_never_entered(mgr, bus):
    bus.emit_with_hooks.assert_not_awaited()
    assert mgr.current_session is None
    assert mgr.session_history == []


@pytest.mark.asyncio
async def test_sink_records_the_plugin_and_launch_raises(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kollab"])  # only the sink refuses
    mgr, bus = _manager(SimpleNamespace(pipe_mode=False))
    sink: list = []
    token = unavailable_attempts.set(sink)
    try:
        with pytest.raises(AltViewUnavailable):
            await mgr.launch_plugin("rain")  # an alias: sink gets the real name
    finally:
        unavailable_attempts.reset(token)
    assert sink == ["matrix"]
    _assert_never_entered(mgr, bus)


@pytest.mark.asyncio
async def test_pipe_mode_raises():
    mgr, bus = _manager(SimpleNamespace(pipe_mode=True))
    with pytest.raises(AltViewUnavailable):
        await mgr.launch_plugin("matrix")
    _assert_never_entered(mgr, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["--detached", "-d"])
async def test_detached_daemon_raises(monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["kollab", flag, "--as", "web"])
    mgr, bus = _manager(SimpleNamespace(pipe_mode=False))
    with pytest.raises(AltViewUnavailable):
        await mgr.launch_plugin("matrix")
    _assert_never_entered(mgr, bus)


@pytest.mark.asyncio
async def test_unknown_plugin_still_returns_false_without_raising(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kollab", "--detached"])
    mgr, bus = _manager(SimpleNamespace(pipe_mode=True))
    assert await mgr.launch_plugin("nope") is False
    _assert_never_entered(mgr, bus)


@pytest.mark.asyncio
async def test_plain_terminal_still_launches(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kollab"])
    monkeypatch.setattr(manager_module, "FullScreenSession", _Session)
    mgr, bus = _manager(SimpleNamespace(pipe_mode=False))
    assert await mgr.launch_plugin("matrix") is True
    assert len(mgr.session_history) == 1
    assert bus.emit_with_hooks.await_count == 3


def test_web_matrix_answers_in_one_line_even_when_the_integrator_swallows():
    """The real chain: integrator catches the raise, the sink still reports it."""
    mgr, _ = _manager(SimpleNamespace(pipe_mode=False))

    async def integrator_like_handler(_altview_mgr):
        # kollabor/fullscreen/command_integration.py: launch_plugin sits in
        # try/except Exception and turns any error into a failure result.
        try:
            await mgr.launch_plugin("matrix")
        except Exception as exc:
            return SimpleNamespace(
                success=False, message=f"Error: {exc}", ui_config=None
            )
        return SimpleNamespace(success=True, message="", ui_config=None)

    saved = _run_web("/matrix", "matrix", integrator_like_handler)
    assert saved[-1] == ("assistant", "/matrix needs the terminal UI")
    _assert_never_entered(mgr, mgr.event_bus)
