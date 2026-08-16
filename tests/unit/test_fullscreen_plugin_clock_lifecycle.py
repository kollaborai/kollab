"""Regression tests for scoped full-screen and alt-view plugin clocks."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from plugins.altview import matrix_altview
from plugins.fullscreen import example_plugin, matrix_plugin, space_shooter_plugin


class StopAfterClock(Exception):
    """Stop rendering once a tested clock read has completed."""


def reject_event_loop_allocation(monkeypatch):
    """Fail if a timing read allocates an event loop it cannot own."""

    def fail_new_event_loop():
        pytest.fail("plugin timing allocated an event loop it does not own")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_new_event_loop)


@pytest.mark.parametrize(
    ("method_name", "stop_target"),
    (
        ("_render_stats_page", "renderer"),
        ("_render_animation_page", "circle"),
    ),
)
def test_synchronous_example_pages_use_monotonic_clock(
    monkeypatch,
    method_name,
    stop_target,
):
    """Direct synchronous page renders do not require an event loop."""
    plugin = example_plugin.EnhancedExamplePlugin()
    plugin.renderer = MagicMock()
    plugin.renderer.get_terminal_size.return_value = (100, 40)
    plugin.running = True
    plugin.start_time = 10.0
    monotonic = Mock(return_value=13.5)
    monkeypatch.setattr(
        example_plugin,
        "time",
        SimpleNamespace(monotonic=monotonic),
    )
    monkeypatch.setattr(
        example_plugin.DrawingPrimitives,
        "draw_text_centered",
        Mock(),
    )
    if stop_target == "renderer":
        plugin.renderer.write_at.side_effect = StopAfterClock
    else:
        monkeypatch.setattr(
            example_plugin.DrawingPrimitives,
            "draw_circle_points",
            Mock(side_effect=StopAfterClock),
        )
    reject_event_loop_allocation(monkeypatch)

    with pytest.raises(StopAfterClock):
        getattr(plugin, method_name)(100, 40)

    monotonic.assert_called_once_with()


@pytest.mark.asyncio
async def test_example_initialize_uses_monotonic_clock(monkeypatch):
    """Example animation initialization is independent of loop clock access."""
    plugin = example_plugin.EnhancedExamplePlugin()
    renderer = MagicMock()
    monotonic = Mock(return_value=10.0)
    monkeypatch.setattr(
        example_plugin,
        "time",
        SimpleNamespace(monotonic=monotonic),
    )
    monkeypatch.setattr(plugin, "_init_life_grid", Mock())
    fade_in = Mock(return_value=object())
    bounce_in = Mock(return_value=object())
    monkeypatch.setattr(plugin.animation_framework, "fade_in", fade_in)
    monkeypatch.setattr(plugin.animation_framework, "bounce_in", bounce_in)
    reject_event_loop_allocation(monkeypatch)

    assert await plugin.initialize(renderer) is True

    monotonic.assert_called_once_with()
    fade_in.assert_called_once_with(1.5, 10.0)
    bounce_in.assert_called_once_with(1.0, 10.3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plugin_module", "plugin_class", "renderer_attribute"),
    (
        (matrix_plugin, matrix_plugin.MatrixRainPlugin, "matrix_renderer"),
        (
            space_shooter_plugin,
            space_shooter_plugin.SpaceShooterPlugin,
            "space_renderer",
        ),
    ),
)
async def test_fullscreen_animation_clocks_are_monotonic(
    monkeypatch,
    plugin_module,
    plugin_class,
    renderer_attribute,
):
    """Full-screen animation start and frame clocks do not allocate loops."""
    plugin = plugin_class()
    renderer = MagicMock()
    animation_renderer = MagicMock()
    plugin.renderer = renderer
    setattr(plugin, renderer_attribute, animation_renderer)
    monotonic = Mock(side_effect=(10.0, 10.25))
    monkeypatch.setattr(
        plugin_module,
        "time",
        SimpleNamespace(monotonic=monotonic),
    )
    reject_event_loop_allocation(monkeypatch)

    await plugin.on_start()
    assert await plugin.render_frame(0.25) is True

    assert plugin.start_time == 10.0
    animation_renderer.update.assert_called_once_with(0.25)
    assert monotonic.call_count == 2


@pytest.mark.asyncio
async def test_matrix_altview_clock_is_monotonic(monkeypatch):
    """Alt-view enter and frame clocks do not allocate event loops."""
    renderer = MagicMock()
    renderer.get_terminal_size.return_value = (100, 40)
    matrix_renderer = MagicMock()
    monkeypatch.setattr(
        matrix_altview,
        "MatrixRenderer",
        Mock(return_value=matrix_renderer),
    )
    monotonic = Mock(side_effect=(10.0, 10.25))
    monkeypatch.setattr(
        matrix_altview,
        "time",
        SimpleNamespace(monotonic=monotonic),
    )
    reject_event_loop_allocation(monkeypatch)
    view = matrix_altview.MatrixAltView()

    await view.on_enter(renderer)
    assert await view.render_frame(0.25) is True

    assert view._start_time == 10.0
    matrix_renderer.update.assert_called_once_with(0.25)
    assert monotonic.call_count == 2
