"""A cancelled turn's late tool result reaches the transcript but not the gems.

A tool that ignores the stop signal reports ~2s after the engine already sent
``turn_cancelled``. The daemon handle marks that result (``cancelled: true``)
so no gem reads it as a live turn. Only tools running at the cancel are marked:
a later turn is never, whatever starts it (a hub message sends no turn_start).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from kollabor_engine.daemon_pool import DaemonHandle
from kollabor_engine.session import EngineSession


def _handle() -> DaemonHandle:
    return DaemonHandle(session_id="s", identity="i", launcher=None)


async def _marks(handle: DaemonHandle, events: list) -> list:
    """Fan the events out (a string is a cancel); return each one's cancelled mark."""
    queue = handle.subscribe()
    for event in events:
        if event == "cancel":
            handle.mark_cancelled()
        else:
            handle._fan_out(dict(event))
    await asyncio.sleep(0)
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait().get("cancelled"))
    handle.unsubscribe(queue)
    return collected


@pytest.mark.asyncio
async def test_the_cancelled_tools_late_result_arrives_marked():
    marks = await _marks(
        _handle(),
        [{"type": "tool_start", "tool_id": "a"}, "cancel", {"type": "tool_result", "tool_id": "a"}],
    )
    assert marks == [None, True]


@pytest.mark.asyncio
async def test_a_turn_started_by_a_hub_message_after_the_cancel_is_never_marked():
    marks = await _marks(
        _handle(),
        [
            {"type": "tool_start", "tool_id": "a"},
            "cancel",
            {"type": "hub_message"},
            {"type": "thinking"},
            {"type": "tool_start", "tool_id": "b"},
            {"type": "token"},
            {"type": "tool_result", "tool_id": "b"},
            # The stubborn tool reports in the middle of the new turn.
            {"type": "tool_result", "tool_id": "a"},
        ],
    )
    assert marks == [None, None, None, None, None, None, True]


@pytest.mark.asyncio
async def test_a_new_call_reusing_the_id_belongs_to_the_new_turn():
    marks = await _marks(
        _handle(),
        [
            {"type": "tool_start", "tool_id": "a"},
            "cancel",
            {"type": "tool_start", "tool_id": "a"},
            {"type": "tool_result", "tool_id": "a"},
        ],
    )
    assert marks == [None, None, None]


@pytest.mark.asyncio
async def test_cancel_turn_marks_the_daemon_and_emits_turn_cancelled():
    session = EngineSession(
        session_id="s", profile=SimpleNamespace(name="p", model=None, provider="openai")
    )
    state = SimpleNamespace(cancel_current_request=AsyncMock(return_value={"cancelled": True}))
    session.daemon = SimpleNamespace(state=state, fan_out=Mock(), mark_cancelled=Mock())
    session.cancel_turn()
    session.daemon.mark_cancelled.assert_called_once_with()
    types = [call.args[0]["type"] for call in session.daemon.fan_out.call_args_list]
    assert types == ["turn_cancelled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_owned_cancel_marks_only_a_successful_cancel(cancelled):
    session = EngineSession(
        session_id="s", profile=SimpleNamespace(name="p", model=None, provider="acp")
    )

    class Rpc:
        async def call(self, method, params):
            return {"cancelled": cancelled}

    session.daemon = _handle()
    session.daemon.rpc = Rpc()
    session.daemon._fan_out({"type": "tool_start", "tool_id": "a"})
    result = await session.cancel_owned_turn("req-1")
    assert result["cancelled"] is cancelled
    assert await _marks(session.daemon, [{"type": "tool_result", "tool_id": "a"}]) == [
        True if cancelled else None
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        # The talk-over stopped its voice turn: the tool it left running reports marked.
        ({"cancelled": True}, [("tool_start", None), ("turn_cancelled", None), ("tool_result", True)]),
        # Refused (a typed turn runs) or dropped while queued: the typed turn's tool is not marked.
        ({"cancelled": False}, [("tool_start", None), ("tool_result", None)]),
        ({"cancelled": True, "queued": True}, [("tool_start", None), ("tool_result", None)]),
    ],
)
async def test_a_voice_cancel_marks_only_a_stop_that_happened(result, expected):
    session = EngineSession(
        session_id="s", profile=SimpleNamespace(name="p", model=None, provider="openai")
    )
    handle = _handle()
    handle.state = SimpleNamespace(cancel_current_request=AsyncMock(return_value=result))
    session.daemon = handle
    queue = handle.subscribe()
    handle._fan_out({"type": "tool_start", "tool_id": "a"})
    session.cancel_turn("voice-1")
    await session._active_turn_task
    handle._fan_out({"type": "tool_result", "tool_id": "a"})  # it ignored the stop
    handle.state.cancel_current_request.assert_awaited_once_with(voice_turn_id="voice-1")
    events = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [(event["type"], event.get("cancelled")) for event in events] == expected
