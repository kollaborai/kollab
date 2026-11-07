"""Regression tests for suite-order leaks in the kill-switch tests (#642).

A test that fakes the halt clock or the halt launch state must not change what
other code in the same process sees: the asyncio loop, background threads and
the next test all read the same module attributes.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from plugins.hub import halt_runtime as hr


def test_fake_halt_clock_is_not_seen_by_other_threads(monkeypatch):
    """A background thread reading time.monotonic during a fake halt wait sees the real clock.

    The fake counter returns negative values, which a real monotonic clock never does.
    While the fake is global, the thread reads the counter and this fails under load (#642).
    """
    counter = iter(range(-1000, 1000, 5))
    thread_readings: list[float] = []

    async def fake_call(req, root, timeout=5.0):
        t = threading.Thread(target=lambda: thread_readings.append(time.monotonic()))
        t.start()
        t.join()
        return {
            "members": [{"folder": "a"}, {"folder": "b"}],
            "acked": ["b"],
            "online": ["b"],
        }

    async def nosleep(_):
        return None

    monkeypatch.setattr(hr, "sock_call", fake_call)
    monkeypatch.setattr(
        hr, "asyncio", SimpleNamespace(sleep=nosleep, TimeoutError=asyncio.TimeoutError)
    )
    monkeypatch.setattr(hr, "time", SimpleNamespace(monotonic=lambda: next(counter)))

    asyncio.run(hr._await_acks("/f", None, seconds=10.0, online_seconds=40.0))

    assert thread_readings and all(
        reading >= 0 for reading in thread_readings
    ), thread_readings


def test_entry_poison_from_one_test():
    """Runs first: a test that launches with its own argv and environment fills the entry."""
    hr.capture_entry(["kollab", "--poisoned"], {"POISONED": "1"})
    assert hr.ENTRY["argv"] == ["kollab", "--poisoned"]


def test_entry_is_empty_at_the_start_of_the_next_test():
    """Runs second: the launch entry must not carry over from the test before it."""
    assert hr.ENTRY == {}
