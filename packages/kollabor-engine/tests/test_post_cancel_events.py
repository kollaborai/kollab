"""A cancelled turn's late tool events reach the transcript but not the gems.

A tool that ignores the stop signal reports ~2s after the engine already sent
``turn_cancelled``. The daemon handle marks those trailing mid-turn events
(``cancelled: true``) so no gem reads them as a live turn; a new turn's first
event clears the mark.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from kollabor_engine.daemon_pool import DaemonHandle
from kollabor_engine.session import EngineSession


async def _collect(handle: DaemonHandle, events: list[dict]) -> list[dict]:
    queue = handle.subscribe()
    for event in events:
        handle._fan_out(dict(event))
    await asyncio.sleep(0)
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait())
    handle.unsubscribe(queue)
    return collected


@pytest.mark.asyncio
async def test_late_tool_events_after_cancel_arrive_marked_until_a_new_turn():
    handle = DaemonHandle(session_id="s", identity="i", launcher=None)
    handle._post_cancel = True
    collected = await _collect(
        handle,
        [
            {"type": "tool_result", "tool_id": "a"},
            {"type": "thinking"},
            {"type": "tool_start", "tool_id": "b"},
            {"type": "token"},
            # A new turn begins: its events arrive unmarked.
            {"type": "user_message"},
            {"type": "tool_result", "tool_id": "b"},
        ],
    )
    assert [e.get("cancelled") for e in collected] == [True, True, True, True, None, None]


@pytest.mark.asyncio
async def test_turn_start_also_clears_the_cancel_mark():
    handle = DaemonHandle(session_id="s", identity="i", launcher=None)
    handle._post_cancel = True
    collected = await _collect(
        handle,
        [{"type": "turn_start"}, {"type": "tool_result", "tool_id": "a"}],
    )
    assert [e.get("cancelled") for e in collected] == [None, None]


@pytest.mark.asyncio
async def test_cancel_turn_marks_the_daemon_and_emits_turn_cancelled():
    session = EngineSession(
        session_id="s", profile=SimpleNamespace(name="p", model=None, provider="openai")
    )
    state = SimpleNamespace(cancel_current_request=AsyncMock(return_value={"cancelled": True}))
    session.daemon = SimpleNamespace(state=state, fan_out=Mock())
    session.cancel_turn()
    assert session.daemon._post_cancel is True
    types = [call.args[0]["type"] for call in session.daemon.fan_out.call_args_list]
    assert types == ["turn_cancelled"]


@pytest.mark.asyncio
async def test_owned_cancel_marks_only_a_successful_cancel():
    session = EngineSession(
        session_id="s", profile=SimpleNamespace(name="p", model=None, provider="acp")
    )

    class Rpc:
        async def call(self, method, params):
            return {"cancelled": False}

    session.daemon = DaemonHandle(session_id="s", identity="i", launcher=None)
    session.daemon.rpc = Rpc()
    result = await session.cancel_owned_turn("req-1")
    assert result["cancelled"] is False
    assert session.daemon._post_cancel is False
