"""ESC must interrupt retry backoff, not wait it out.

`cancel_current_request()` calls `task.cancel()`, but between retry attempts
the previous attempt's task is already `.done()`, so the cancel was dropped
and `asyncio.sleep(delay)` ran to completion -- up to 120s per attempt, five
attempts -- before firing another request at an API that just rate-limited us.
"""

import asyncio
import time

import pytest

from kollabor_ai.api_communication_service import APICommunicationService


def _service():
    svc = APICommunicationService.__new__(APICommunicationService)
    svc.current_request_task = None
    svc.cancel_requested = False
    svc._cancel_event = asyncio.Event()
    return svc


@pytest.mark.asyncio
async def test_backoff_aborts_when_cancelled_midsleep():
    svc = _service()

    async def cancel_soon():
        await asyncio.sleep(0.05)
        svc.cancel_current_request()

    started = time.monotonic()
    asyncio.create_task(cancel_soon())
    with pytest.raises(asyncio.CancelledError):
        await svc._sleep_or_cancel(30.0)

    assert time.monotonic() - started < 1.0, "waited out the backoff instead of aborting"


@pytest.mark.asyncio
async def test_backoff_completes_normally_when_not_cancelled():
    svc = _service()
    started = time.monotonic()
    await svc._sleep_or_cancel(0.1)
    elapsed = time.monotonic() - started
    assert 0.08 <= elapsed < 1.0
    assert not svc.cancel_requested


@pytest.mark.asyncio
async def test_already_cancelled_never_sleeps():
    """ESC pressed just before the backoff starts must not buy a full delay."""
    svc = _service()
    svc.cancel_requested = True
    started = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await svc._sleep_or_cancel(30.0)
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_cancel_fires_even_with_a_done_task():
    """The exact regression window: previous attempt finished, no live task."""
    svc = _service()

    async def already_finished():
        return "done"

    task = asyncio.create_task(already_finished())
    await task
    svc.current_request_task = task
    assert task.done()

    svc.cancel_current_request()
    assert svc.cancel_requested
    assert svc._cancel_event.is_set(), "cancel dropped because the task was done"
