"""Focused tests for ConfigService lifecycle behavior."""

import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor_config.service import ConfigService


def _uninitialized_service() -> ConfigService:
    service = object.__new__(ConfigService)
    service._pending_reload_tasks = set()
    service._stop_file_watching = Mock()
    return service


@pytest.mark.asyncio
async def test_file_change_reload_task_failure_is_observed(caplog):
    service = _uninitialized_service()
    service._handle_file_change = AsyncMock(side_effect=RuntimeError("reload boom"))

    with caplog.at_level(logging.ERROR, logger="kollabor_config.service"):
        service._schedule_file_change_reload()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert not service._pending_reload_tasks
    assert "Configuration hot-reload task failed" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_file_change_reload_tasks():
    service = _uninitialized_service()
    task = asyncio.create_task(asyncio.sleep(60))
    service._pending_reload_tasks.add(task)

    service.shutdown()

    assert task.cancelling()
    assert not service._pending_reload_tasks
    await asyncio.gather(task, return_exceptions=True)
