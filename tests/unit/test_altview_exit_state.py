"""Regression tests for AltView exit-state cleanup."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kollabor.altview.command_integration import AltViewCommandIntegrator
from kollabor_events.models import EventType
from kollabor_tui.altview.base import AltView, AltViewMetadata, AltViewState
from kollabor_tui.altview.session import AltViewSession
from kollabor_tui.altview.stack_manager import AltViewStackManager
from kollabor_tui.status.altview_widget import render_altview_status
from plugins.altview.widget_picker_altview import WidgetPickerAltView


class _Loop:
    def __init__(self, hibernating: bool = False) -> None:
        self.hibernating = hibernating

    def hibernate(self) -> None:
        self.hibernating = True

    def thaw(self) -> None:
        self.hibernating = False


class _Scheduler:
    def __init__(self, paused: bool = False) -> None:
        self.paused = paused

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


class _EventBus:
    def __init__(self) -> None:
        self.events = []
        self.loop = _Loop()
        self.scheduler = _Scheduler()
        self.services = {
            "main_render_loop": self.loop,
            "refresh_scheduler": self.scheduler,
        }

    def get_service(self, name):
        return self.services.get(name)

    async def emit_with_hooks(self, event_type, event_data, source):
        self.events.append((event_type, event_data, source))
        return {"success": True}


class _FailingEnterSession:
    def __init__(self, altview, event_bus, session_name):
        self.session_name = session_name
        self.display_queue = SimpleNamespace(frame_count=0)

    async def enter(self):
        raise RuntimeError("enter failed")

    async def run_loop(self):
        raise AssertionError("run_loop should not start after enter failure")

    async def exit(self):
        raise AssertionError("exit should not run when enter never succeeded")


class _FailingExitSession:
    session_name = "bad-exit"

    def __init__(self) -> None:
        self.display_queue = SimpleNamespace(frame_count=0)

    async def exit(self):
        raise RuntimeError("exit failed")


@pytest.mark.asyncio
async def test_push_enter_failure_restores_main_ui(monkeypatch):
    bus = _EventBus()
    manager = AltViewStackManager(bus, terminal_renderer=object())

    monkeypatch.setattr(
        "kollabor_tui.altview.stack_manager.AltViewSession",
        _FailingEnterSession,
    )

    with pytest.raises(RuntimeError, match="enter failed"):
        await manager.push(object(), "bad-enter")

    assert manager.stack_depth == 0
    assert bus.loop.hibernating is False
    assert bus.scheduler.paused is False
    assert any(event[0] == EventType.MODAL_HIDE for event in bus.events)


@pytest.mark.asyncio
async def test_pop_exit_failure_still_restores_main_ui():
    bus = _EventBus()
    bus.loop.hibernating = True
    bus.scheduler.paused = True
    manager = AltViewStackManager(bus, terminal_renderer=object())
    manager._stack.append(_FailingExitSession())

    await manager._pop_current()

    assert manager.stack_depth == 0
    assert bus.loop.hibernating is False
    assert bus.scheduler.paused is False
    assert any(event[0] == EventType.MODAL_HIDE for event in bus.events)


class _AltViewForExit:
    def __init__(self) -> None:
        self.state = None
        self.on_suspend_called = False

    async def on_suspend(self):
        self.on_suspend_called = True
        raise RuntimeError("suspend failed")

    def _set_state(self, state):
        self.state = state


class _HookBus:
    def __init__(self) -> None:
        self.unregistered = False

    async def unregister_hook(self, plugin_name, hook_name):
        self.unregistered = (plugin_name, hook_name)


class _RenderLoopSpy:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        _RenderLoopSpy.instances.append(self)

    async def run(self):
        return True


class _RenderableAltView(AltView):
    def __init__(self) -> None:
        super().__init__(
            AltViewMetadata(
                plugin_type="renderable-test",
                supports_background=False,
            )
        )

    async def on_enter(self, renderer):
        self._renderer = renderer

    async def render_frame(self, delta_time: float) -> bool:
        return True

    async def handle_input(self, key_press) -> bool:
        return False


@pytest.mark.asyncio
async def test_session_exit_cleans_up_when_suspend_fails():
    altview = _AltViewForExit()
    bus = _HookBus()
    session = AltViewSession(altview, bus, "cleanup-test")
    session.renderer = SimpleNamespace(restore_terminal=Mock(return_value=True))
    session.display_queue = SimpleNamespace(stop_capture=Mock())
    session._input_hook_registered = True
    render_loop = SimpleNamespace(stop=Mock())
    session._render_loop = render_loop
    session._running = True

    await session.exit()

    render_loop.stop.assert_called_once()
    session.renderer.restore_terminal.assert_called_once()
    session.display_queue.stop_capture.assert_called_once()
    assert bus.unregistered == (
        "altview_session_cleanup-test",
        "fullscreen_input",
    )
    assert session._input_hook_registered is False
    assert altview.state == AltViewState.SUSPENDED


@pytest.mark.asyncio
async def test_altview_sessions_disable_timer_redraws_by_default(monkeypatch):
    _RenderLoopSpy.instances = []
    monkeypatch.setattr(
        "kollabor_tui.altview.session.EventDrivenRenderLoop",
        _RenderLoopSpy,
    )
    altview = SimpleNamespace(target_fps=15.0)
    session = AltViewSession(altview, _HookBus(), "static-altview")

    await session.run_loop()

    assert _RenderLoopSpy.instances[0].kwargs["render_on_timer"] is False


@pytest.mark.asyncio
async def test_altview_sessions_allow_timer_redraws_only_when_opted_in(monkeypatch):
    _RenderLoopSpy.instances = []
    monkeypatch.setattr(
        "kollabor_tui.altview.session.EventDrivenRenderLoop",
        _RenderLoopSpy,
    )
    altview = SimpleNamespace(target_fps=60.0, render_on_timer=True)
    session = AltViewSession(altview, _HookBus(), "animated-altview")

    await session.run_loop()

    assert _RenderLoopSpy.instances[0].kwargs["render_on_timer"] is True


def test_altview_request_render_delegates_to_render_loop():
    altview = _RenderableAltView()
    render_loop = SimpleNamespace(request_render=Mock())

    altview._set_render_loop(render_loop)
    altview.request_render()

    render_loop.request_render.assert_called_once()


def _status_ctx(stack_manager):
    return SimpleNamespace(
        event_bus=SimpleNamespace(get_service=lambda name: stack_manager)
    )


def _session(name, state, supports_background=False):
    return SimpleNamespace(
        session_name=name,
        altview=SimpleNamespace(
            state=state,
            metadata=SimpleNamespace(supports_background=supports_background),
            background_tasks=[],
        ),
    )


def test_altview_widget_hides_suspended_non_background_sessions():
    stack_manager = SimpleNamespace(
        get_status_sessions=lambda: [
            _session("widget-picker-123", AltViewState.SUSPENDED, False)
        ]
    )

    assert render_altview_status(120, _status_ctx(stack_manager)) == ""


def test_altview_widget_shows_active_sessions():
    stack_manager = SimpleNamespace(
        get_status_sessions=lambda: [
            _session("hub-console", AltViewState.RUNNING, False)
        ]
    )

    rendered = render_altview_status(120, _status_ctx(stack_manager))

    assert "altview:" in rendered
    assert "hub-console" in rendered


def test_altview_widget_shows_background_idle_sessions():
    stack_manager = SimpleNamespace(
        get_status_sessions=lambda: [
            _session("research", AltViewState.IDLE, True)
        ]
    )

    rendered = render_altview_status(120, _status_ctx(stack_manager))

    assert "research" in rendered
    assert "(idle)" in rendered


def test_widget_picker_altview_is_internal_and_not_registered():
    registry = Mock()
    registry.get_command.return_value = None
    integrator = AltViewCommandIntegrator(
        command_registry=registry,
        event_bus=Mock(),
        terminal_renderer=Mock(),
    )

    assert WidgetPickerAltView().metadata.category == "internal"
    assert integrator._register_plugin_commands(WidgetPickerAltView) is True
    registry.register_command.assert_not_called()


@pytest.mark.asyncio
async def test_failed_first_enter_removes_stale_registry_session(monkeypatch):
    bus = _EventBus()
    manager = AltViewStackManager(bus, terminal_renderer=object())

    monkeypatch.setattr(
        "kollabor_tui.altview.stack_manager.AltViewSession",
        _FailingEnterSession,
    )

    with pytest.raises(RuntimeError, match="enter failed"):
        await manager.push(object(), "bad-enter")

    assert manager.get_session("bad-enter") is None


@pytest.mark.asyncio
async def test_restore_main_ui_resets_renderer_render_state():
    bus = _EventBus()
    renderer = SimpleNamespace(
        writing_messages=True,
        input_line_written=True,
        last_line_count=4,
        invalidate_render_cache=Mock(),
    )
    manager = AltViewStackManager(bus, terminal_renderer=renderer)

    await manager._restore_main_ui("cleanup-test")

    assert renderer.writing_messages is False
    assert renderer.input_line_written is False
    assert renderer.last_line_count == 0
    renderer.invalidate_render_cache.assert_called_once()
