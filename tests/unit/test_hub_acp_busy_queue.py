"""A busy ACP agent parks peer messages instead of refusing them (issue #483).

A harness turn never passes the queue processor whose hooks mark an agent
working, so the display tap's turn_start/turn_end carry that lifecycle for
ACP agents instead. A full ACP prompt queue is backpressure, not refusal:
the message waits for the next turn boundary, when the freed slot admits
it, and only an exhausted retry budget ends the turn as busy.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.hub.models import AgentState, HubMessage
from plugins.hub.plugin import (
    _ACP_BUSY_PARK_LIMIT,
    _ACP_BUSY_RETRY_LIMIT,
    HubPlugin,
)

ASKER = "lapis@laptop-kollab"


def _acp_plugin(*, llm=None):
    """A plugin instance whose ACP admission path runs against stubs."""
    plugin = HubPlugin.__new__(HubPlugin)
    llm = llm if llm is not None else SimpleNamespace(
        is_processing=False,
        submit_human_input=AsyncMock(return_value={"status": "queued"}),
    )
    bus = MagicMock()
    bus.get_service.side_effect = lambda name: (
        llm if name == "llm_service" else None
    )
    plugin.event_bus = bus
    plugin._identity = SimpleNamespace(
        identity="infra", agent_id="infra-1", state="idle",
        current_task="", current_tool={},
    )
    plugin._presence = MagicMock()
    plugin._running_tools = {}
    plugin._relay_agent = SimpleNamespace(_turn=SimpleNamespace(get=lambda: None))
    plugin._end_network_turn = AsyncMock()
    plugin._acp_network_turns = {}
    return plugin, llm


def _agent_request(content: str = "check the tunnel") -> HubMessage:
    return HubMessage(
        id="w" * 32,
        action="message",
        from_agent="relay:peer",
        from_identity=ASKER,
        to="infra",
        content=content,
        thread_id="1" * 32,
        metadata={"network": {"from_device": "laptop-kollab", "trust": "open"}},
    )


def _busy_reject() -> dict:
    return {
        "status": "rejected",
        "reason": "acp_queue_full",
        "message": "ACP prompt queue is full",
    }


@pytest.mark.asyncio
async def test_full_acp_queue_parks_the_message_instead_of_refusing_it():
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    message = _agent_request()

    result = await plugin._submit_authenticated_agent_message(message, llm)

    assert result["status"] == "rejected"
    plugin._end_network_turn.assert_not_awaited()  # no bounce: backpressure
    assert message.id in plugin._acp_network_turns  # the turn stays open
    assert len(plugin._acp_busy_parked) == 1
    entry = plugin._acp_busy_parked[0]
    assert entry["message"] is message
    assert entry["attempts"] == 1
    assert entry["turn"] is plugin._acp_network_turns[message.id]


@pytest.mark.asyncio
async def test_park_limit_still_refuses_when_the_lot_is_full():
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    message = _agent_request()
    plugin._acp_busy_parked = [
        {"message": _agent_request(f"held {i}"), "turn": None,
         "attempts": 1, "ts": 0.0}
        for i in range(_ACP_BUSY_PARK_LIMIT)
    ]

    await plugin._submit_authenticated_agent_message(message, llm)

    assert len(plugin._acp_busy_parked) == _ACP_BUSY_PARK_LIMIT  # not parked
    plugin._end_network_turn.assert_awaited_once()
    kwargs = plugin._end_network_turn.await_args.kwargs
    assert kwargs["outcome"] == "busy"
    assert message.id not in plugin._acp_network_turns


@pytest.mark.asyncio
async def test_turn_boundary_admits_one_parked_message():
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(
        side_effect=[_busy_reject(), {"status": "queued"}]
    )
    message = _agent_request()
    await plugin._submit_authenticated_agent_message(message, llm)
    assert plugin._acp_busy_parked

    plugin._kick_parked_busy_retries()
    assert plugin._busy_retry_task is not None
    await plugin._busy_retry_task

    assert plugin._acp_busy_parked == []  # admitted
    assert message.id in plugin._acp_network_turns  # turn owns the reply now
    assert llm.submit_human_input.await_count == 2
    plugin._end_network_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_waits_at_each_boundary_until_the_budget_runs_out():
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    message = _agent_request()
    # Park through the real path, then exhaust the budget one boundary at a time.
    await plugin._submit_authenticated_agent_message(message, llm)
    entry = plugin._acp_busy_parked[0]
    for _ in range(_ACP_BUSY_RETRY_LIMIT - 1):
        plugin._kick_parked_busy_retries()
        await plugin._busy_retry_task
        assert plugin._acp_busy_parked == [entry]  # still waiting
    plugin._kick_parked_busy_retries()  # attempts == limit now
    await plugin._busy_retry_task
    assert plugin._acp_busy_parked == []
    assert message.id not in plugin._acp_network_turns
    plugin._end_network_turn.assert_awaited_once()
    kwargs = plugin._end_network_turn.await_args.kwargs
    assert kwargs["failed"] is True
    assert kwargs["outcome"] == "busy"
    assert kwargs["outcome_text"] == "The receiving agent is busy."


@pytest.mark.asyncio
async def test_retry_overlapping_passes_are_coalesced():
    plugin, llm = _acp_plugin()
    message = _agent_request()
    plugin._acp_busy_parked = [{
        "message": message, "turn": None, "attempts": 1, "ts": 0.0,
    }]
    gate = asyncio.Event()
    llm.submit_human_input = AsyncMock(
        side_effect=lambda *a, **k: gate.wait() or {"status": "queued"}
    )

    plugin._kick_parked_busy_retries()
    first = plugin._busy_retry_task
    plugin._kick_parked_busy_retries()  # boundary fires mid-pass
    assert plugin._busy_retry_task is first  # no second pass while one runs

    gate.set()
    await first
    assert plugin._acp_busy_parked == []
    assert llm.submit_human_input.await_count == 1


def test_tap_turn_start_marks_the_agent_working():
    plugin, _ = _acp_plugin()
    plugin._identity.state = "idle"
    plugin._follow_tool({"type": "turn_start"})
    assert plugin._identity.state == AgentState.WORKING.value
    plugin._presence.publish.assert_called()

    plugin._identity.state = "idle"  # the permission gate works the same
    plugin._follow_tool({"type": "acp_waiting", "waiting": True})
    assert plugin._identity.state == AgentState.WORKING.value


def test_tap_turn_start_preserves_the_waiting_state():
    plugin, _ = _acp_plugin()
    plugin._identity.state = "waiting"
    plugin._follow_tool({"type": "turn_start"})
    assert plugin._identity.state == "waiting"
    plugin._presence.publish.assert_not_called()


@pytest.mark.asyncio
async def test_tap_turn_end_settles_the_agent_idle():
    plugin, llm = _acp_plugin()
    plugin._identity.state = "working"
    plugin._running_tools["t1"] = {"name": "terminal", "type": "terminal"}
    qp = SimpleNamespace(
        processing_queue=asyncio.Queue(),
        _turn_lock=asyncio.Lock(),
    )
    llm._queue_processor = qp
    llm.is_processing = False

    plugin._follow_tool({"type": "turn_end"})

    assert plugin._identity.state == AgentState.IDLE.value
    assert plugin._running_tools == {}
    assert plugin._identity.current_tool == {}
    plugin._presence.publish.assert_called()


def test_tap_turn_end_keeps_working_while_any_owner_holds_work():
    plugin, llm = _acp_plugin()
    plugin._identity.state = "working"
    llm.is_processing = True  # another admitted turn is still running
    plugin._follow_tool({"type": "turn_end"})
    assert plugin._identity.state == "working"


@pytest.mark.asyncio
async def test_tap_turn_end_admits_a_parked_message_at_the_boundary():
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value={"status": "queued"})
    message = _agent_request()
    plugin._identity.state = "working"
    plugin._acp_busy_parked = [{
        "message": message,
        "turn": SimpleNamespace(ended=False, failed=False),
        "attempts": 1, "ts": 0.0,
    }]
    plugin._acp_network_turns[message.id] = plugin._acp_busy_parked[0]["turn"]
    qp = SimpleNamespace(processing_queue=asyncio.Queue(), _turn_lock=asyncio.Lock())
    llm._queue_processor = qp

    plugin._follow_tool({"type": "turn_end"})
    assert plugin._identity.state == AgentState.IDLE.value
    await plugin._busy_retry_task

    assert plugin._acp_busy_parked == []
    assert message.id in plugin._acp_network_turns
    llm.submit_human_input.assert_awaited_once()
