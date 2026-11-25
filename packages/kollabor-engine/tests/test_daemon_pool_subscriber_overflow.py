"""Per-subscriber overflow metadata is bounded and does not alter event frames."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kollabor_engine import daemon_pool
from kollabor_engine.session_context import PHONE_STREAM_REVOKED


@pytest.mark.asyncio
async def test_overflow_is_local_to_slow_subscriber_and_healthy_fifo_is_unchanged():
    handle = daemon_pool.DaemonHandle("overflow-test", "agent", launcher=None)
    slow = handle.subscribe()
    slow._maxsize = 1
    fast = handle.subscribe()
    first = {"type": "token", "text": "one", "seq": 10}
    dropped = {"type": "turn_complete", "request_id": "r1", "seq": 11}

    handle._fan_out(first)
    handle._fan_out(dropped)

    assert fast.get_nowait() == {**first, "session_id": "overflow-test"}
    assert fast.get_nowait() == {**dropped, "session_id": "overflow-test"}
    assert slow.get_nowait() == {**first, "session_id": "overflow-test"}
    assert slow.dropped_count == 1
    assert slow.first_dropped_seq == 11
    assert slow.last_dropped_seq == 11
    assert slow.dropped_terminal is True
    assert slow.qsize() == 0


@pytest.mark.asyncio
async def test_repeated_overflow_uses_fixed_metadata_and_keeps_queue_frames():
    handle = daemon_pool.DaemonHandle("overflow-test", "agent", launcher=None)
    queue = handle.subscribe()
    queue._maxsize = 1
    events = [
        {"type": "token", "text": "kept", "seq": 20},
        {"type": "token", "text": "lost-a", "seq": 21},
        {"type": "response", "text": "lost-b", "seq": 22},
    ]
    for event in events:
        handle._fan_out(event)

    assert queue.qsize() == 1
    assert queue.get_nowait() == {**events[0], "session_id": "overflow-test"}
    assert queue.dropped_count == 2
    assert queue.first_dropped_seq == 21
    assert queue.last_dropped_seq == 22
    assert queue.dropped_terminal is False
    assert queue.empty()
    assert set(vars(queue).keys()) <= {
        "_maxsize", "_getters", "_putters", "_unfinished_tasks", "_finished",
        "_queue", "_is_shutdown", "dropped_count", "first_dropped_seq",
        "last_dropped_seq", "dropped_terminal", "dropped_permission", "revoked",
    }


@pytest.mark.asyncio
async def test_revocation_purges_frames_and_takes_priority_over_overflow_metadata():
    handle = daemon_pool.DaemonHandle("overflow-test", "agent", launcher=None)
    context = SimpleNamespace(owner_id="local", phone_id="phone-a", revoked=False)
    queue = handle.subscribe(context=context)
    queue._maxsize = 1
    handle._fan_out({"type": "token", "text": "kept", "seq": 30})
    handle._fan_out({"type": "turn_complete", "request_id": "r1", "seq": 31})
    assert queue.dropped_count == 1

    context.revoked = True
    assert handle.close_streams_for_context(context) == 1

    assert queue.revoked is True
    assert queue.qsize() == 1
    assert queue.get_nowait() is PHONE_STREAM_REVOKED
    assert queue.dropped_count == 1
    assert queue not in handle._subscribers
