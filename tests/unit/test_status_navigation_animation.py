"""Regression tests for status navigation effect-animation lifecycle."""

import asyncio

import pytest

from kollabor_tui.status.navigation_manager import StatusNavigationManager


def _manager() -> StatusNavigationManager:
    manager = object.__new__(StatusNavigationManager)
    manager._shimmer_task = None
    manager._shimmer_running = False
    manager._has_widgets_with_effects = lambda: True
    return manager


@pytest.mark.asyncio
async def test_animation_failure_releases_task_and_allows_restart():
    manager = _manager()

    async def fail_loop() -> None:
        raise RuntimeError("render failed")

    manager._effect_animation_loop = fail_loop
    await manager._start_effect_animation()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert manager._shimmer_task is None
    assert manager._shimmer_running is False

    started = asyncio.Event()

    async def running_loop() -> None:
        started.set()
        await asyncio.sleep(3600)

    manager._effect_animation_loop = running_loop
    await manager._start_effect_animation()
    await started.wait()
    assert manager._shimmer_task is not None

    await manager._stop_effect_animation()
    assert manager._shimmer_task is None
    assert manager._shimmer_running is False
