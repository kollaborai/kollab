"""A busy ACP agent parks peer messages instead of refusing them (issue #483).

A harness turn never passes the queue processor whose hooks mark an agent
working, so the display tap's turn_start/turn_end carry that lifecycle for
ACP agents instead. A full ACP prompt queue is backpressure, not refusal:
the message waits for the next turn boundary, when the freed slot admits
it, and only an exhausted retry budget ends the turn as busy.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.hub import plugin as hub_plugin_module
from plugins.hub.models import AgentState, HubMessage
from plugins.hub.plugin import (
    _ACP_BUSY_PARK_LIMIT,
    _ACP_BUSY_RETRY_LIMIT,
    HubPlugin,
    _NetworkTurn,
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


def _agent_request(
    content: str = "check the tunnel", from_identity: str = ASKER
) -> HubMessage:
    return HubMessage(
        id="w" * 32,
        action="message",
        from_agent="relay:peer",
        from_identity=from_identity,
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


def _held_turn(plugin, message: HubMessage, attempts: int = 1) -> None:
    """Register a real open network turn and park it, fixture-style."""
    if not hasattr(plugin, "_acp_busy_parked"):
        plugin._acp_busy_parked = []
    turn = _NetworkTurn(
        handle=message.from_identity,
        thread_id=message.thread_id,
        message_id=message.id,
        opened_at=0.0,
    )
    plugin._acp_network_turns[message.id] = turn
    plugin._acp_busy_parked.append(
        {
            "message": message,
            "turn": turn,
            "attempts": attempts,
            "ts": hub_plugin_module.time.monotonic(),
        }
    )


@pytest.mark.asyncio
async def test_turn_end_settles_idle_once_the_completion_retires(monkeypatch):
    """The boundary tap fires while the completing ACP task still counts as
    processing; a bounded re-check settles presence once it retires (#483)."""
    monkeypatch.setattr(hub_plugin_module, "_ACP_SETTLE_RECHECK_INTERVAL", 0.01)
    plugin, llm = _acp_plugin()
    plugin._identity.state = "working"
    qp = SimpleNamespace(processing_queue=asyncio.Queue(), _turn_lock=asyncio.Lock())
    llm._queue_processor = qp
    llm.is_processing = True  # the completing task is still in _completion_tasks

    plugin._follow_tool({"type": "turn_end"})
    assert plugin._identity.state == AgentState.WORKING.value  # not yet idle

    llm.is_processing = False  # its done-callback retires it a beat later
    await asyncio.sleep(0.05)  # well inside the re-check deadline

    assert plugin._identity.state == AgentState.IDLE.value
    plugin._presence.publish.assert_called()


@pytest.mark.asyncio
async def test_shutdown_ends_parked_turns_busy_instead_of_dropping_them():
    plugin, _ = _acp_plugin()
    messages = [_agent_request(f"held {i}") for i in range(2)]
    for message in messages:
        _held_turn(plugin, message)
    # Inert stubs for the rest of the shutdown path.
    plugin._relay_agent = None
    plugin._vault = None
    plugin._change_feed = None
    plugin._startup_task = None
    plugin._heartbeat_task = None
    plugin._mailbox_task = None
    plugin._dreaming_task = None
    plugin._notify_task = None
    plugin._cron_task = None
    plugin._autosave_task = None
    plugin._supervisor_task = None
    plugin._presence_sweep_task = None
    plugin._bridge_task = None
    plugin._email_task = None
    plugin._started = False
    plugin._socket_server = None
    plugin._election = None

    await plugin.shutdown(exit_process=False)

    assert plugin._acp_busy_parked == []
    assert not plugin._acp_network_turns
    assert plugin._end_network_turn.await_count == 2
    for call in plugin._end_network_turn.await_args_list:
        assert call.kwargs["failed"] is True
        assert call.kwargs["outcome"] == "busy"


@pytest.mark.asyncio
async def test_a_new_message_parks_behind_earlier_ones():
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    first = _agent_request("first in line")
    await plugin._submit_authenticated_agent_message(first, llm)
    assert len(plugin._acp_busy_parked) == 1

    second = _agent_request("arrives while the first is parked")
    result = await plugin._submit_authenticated_agent_message(second, llm)

    assert result["reason"] == "acp_queue_full"
    assert [e["message"] for e in plugin._acp_busy_parked] == [first, second]
    assert llm.submit_human_input.await_count == 1  # the second never submitted
    plugin._end_network_turn.assert_not_awaited()  # its turn stays open
    assert second.id in plugin._acp_network_turns

    # The next boundaries admit both, in arrival order.
    llm.submit_human_input = AsyncMock(return_value={"status": "queued"})
    while plugin._acp_busy_parked:
        plugin._kick_parked_busy_retries()
        await plugin._busy_retry_task
    assert [c.args[0] for c in llm.submit_human_input.await_args_list] == [
        first.content,
        second.content,
    ]


@pytest.mark.asyncio
async def test_a_busy_boundary_ages_every_parked_message():
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    messages = [_agent_request(f"held {i}") for i in range(2)]
    for message in messages:
        _held_turn(plugin, message)

    for _ in range(_ACP_BUSY_RETRY_LIMIT):  # boundaries drain the budget
        plugin._kick_parked_busy_retries()
        await plugin._busy_retry_task

    assert plugin._acp_busy_parked == []
    assert not plugin._acp_network_turns
    assert plugin._end_network_turn.await_count == 2  # both aged out, not just the head
    for call in plugin._end_network_turn.await_args_list:
        assert call.kwargs["failed"] is True
        assert call.kwargs["outcome"] == "busy"


@pytest.mark.asyncio
async def test_parked_messages_expire_busy_after_the_ttl(monkeypatch):
    monkeypatch.setattr(hub_plugin_module, "_ACP_BUSY_PARK_TTL_SECONDS", 0.05)
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    message = _agent_request()
    await plugin._submit_authenticated_agent_message(message, llm)
    assert plugin._acp_busy_parked

    await asyncio.sleep(0.2)  # past the TTL; the timer sweeps the entry

    assert plugin._acp_busy_parked == []
    plugin._end_network_turn.assert_awaited_once()
    kwargs = plugin._end_network_turn.await_args.kwargs
    assert kwargs["failed"] is True
    assert kwargs["outcome"] == "busy"


@pytest.mark.asyncio
async def test_a_departing_peers_parked_messages_end_unavailable():
    plugin, _ = _acp_plugin()
    gone = _agent_request("held for a leaving peer", from_identity=ASKER)
    staying = _agent_request("held for a staying peer", from_identity="onyx@other")
    staying.id = "s" * 32  # distinct from gone's fixture id
    _held_turn(plugin, gone)
    _held_turn(plugin, staying)

    await plugin._sweep_parked_for_departure(ASKER)

    assert [e["message"] for e in plugin._acp_busy_parked] == [staying]
    assert gone.id not in plugin._acp_network_turns
    assert staying.id in plugin._acp_network_turns
    plugin._end_network_turn.assert_awaited_once()
    kwargs = plugin._end_network_turn.await_args.kwargs
    assert kwargs["failed"] is True
    assert kwargs["outcome"] == "unavailable"


@pytest.mark.asyncio
async def test_one_boundary_kicks_once_when_both_taps_fire():
    """turn_complete and its turn_end follow-up tap the same turn, so the
    parked list ages once per real boundary, not once per tap (#483)."""
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    message = _agent_request()
    _held_turn(plugin, message)

    # Real wire shapes (#497): turn_complete carries snake `turn_id`, while
    # turn_end's frame carries camel `turnId` and no `turn_id` at all. The
    # shared key is `request_id`; feeding the same dict to both taps would
    # hide the very mismatch the boundary dedupe has to survive.
    plugin._follow_tool(
        {"type": "turn_complete", "turn_id": "7", "request_id": "r-1"}
    )
    first_pass = plugin._busy_retry_task
    assert first_pass is not None
    await first_pass  # the retry pass settles before the follow-up tap lands

    plugin._follow_tool(
        {"type": "turn_end", "turnId": "7", "request_id": "r-1"}
    )
    assert plugin._busy_retry_task is first_pass  # same boundary: no second kick
    assert llm.submit_human_input.await_count == 1  # one pass, one aging
    assert plugin._acp_busy_parked[0]["attempts"] == 2  # not 3

    plugin._follow_tool({"type": "turn_end", "turnId": "8", "request_id": "r-2"})  # next turn
    assert plugin._busy_retry_task is not first_pass  # a fresh boundary kicks
    await plugin._busy_retry_task
    assert llm.submit_human_input.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_end_re_parks_the_message_instead_of_losing_it(caplog):
    """A raise inside the terminal end must not swallow the message: the
    entry re-parks with a warning and the next boundary ends it (#483)."""
    plugin, llm = _acp_plugin()
    llm.submit_human_input = AsyncMock(return_value={
        "status": "rejected",
        "reason": "provider down",
        "message": "boom",
    })
    message = _agent_request()
    _held_turn(plugin, message)
    turn = plugin._acp_busy_parked[0]["turn"]
    plugin._end_network_turn = AsyncMock(
        side_effect=[Exception("socket died"), None]
    )

    with caplog.at_level(logging.WARNING, logger="plugins.hub.plugin"):
        plugin._kick_parked_busy_retries()
        await plugin._busy_retry_task

    assert [e["message"] for e in plugin._acp_busy_parked] == [message]
    assert turn.ended is False  # a later boundary may still end it
    assert any("re-parked" in str(record.message) for record in caplog.records)

    plugin._kick_parked_busy_retries()  # the next boundary retries the end
    await plugin._busy_retry_task

    assert plugin._acp_busy_parked == []
    assert message.id not in plugin._acp_network_turns
    assert plugin._end_network_turn.await_count == 2  # retried, then ended


@pytest.mark.asyncio
async def test_departure_during_inflight_retry_preserves_other_message():
    """A departing in-flight head must not cancel admission or strand its follower."""
    plugin, llm = _acp_plugin()
    gone = _agent_request("departing", from_identity=ASKER)
    staying = _agent_request("staying", from_identity="onyx@other")
    staying.id = "s" * 32
    _held_turn(plugin, gone)
    _held_turn(plugin, staying)
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def blocked_admission(content, **_kwargs):
        if content == "departing":
            entered.set()
            await resume.wait()
        return {"status": "queued"}

    llm.submit_human_input = AsyncMock(side_effect=blocked_admission)
    plugin._kick_parked_busy_retries()
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    await plugin._sweep_parked_for_departure(ASKER)
    await asyncio.wait_for(plugin._busy_retry_task, timeout=1.0)

    assert [call.args[0] for call in llm.submit_human_input.await_args_list] == [
        "departing",
        "staying",
    ]
    assert plugin._acp_busy_parked == []
    assert staying.id in plugin._acp_network_turns
    plugin._end_network_turn.assert_not_awaited()

    detached = list(plugin._busy_detached_tasks)
    resume.set()
    await asyncio.wait_for(asyncio.gather(*detached), timeout=1.0)

    assert gone.id in plugin._acp_network_turns
    plugin._end_network_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_preserves_manual_relay_authority():
    """A parked request must retry under its own manual relay authority."""
    from contextvars import ContextVar
    from types import MethodType

    from plugins.hub.relay_agent import RelayAgentBridge
    from plugins.hub.relay_state import RelayError

    plugin, llm = _acp_plugin()
    relay = SimpleNamespace(
        _turn=ContextVar("parked_relay_turn", default=None),
        _correlated_event_context=ContextVar("parked_relay_event", default=None),
        _authorize_active=MagicMock(return_value=None),
        active=SimpleNamespace(
            record={"id": "manual-task-123"}, finished=False
        ),
    )
    plugin._relay_agent = relay
    message = _agent_request("manual task", from_identity="relay:manual-peer")
    llm.submit_human_input = AsyncMock(
        side_effect=[_busy_reject(), {"status": "queued"}]
    )
    token = relay._turn.set("manual-task-123")
    try:
        await plugin._submit_authenticated_agent_message(message, llm)
    finally:
        relay._turn.reset(token)

    await plugin._retry_parked_busy_messages()
    if plugin._parked_ttl_handle is not None:
        plugin._parked_ttl_handle.cancel()

    first_origin = llm.submit_human_input.await_args_list[0].kwargs["origin_context"]
    retry_origin = llm.submit_human_input.await_args_list[1].kwargs["origin_context"]
    assert retry_origin["relay_turn_id"] == first_origin["relay_turn_id"]

    relay._authorize_active.assert_called_once_with(turn_id="manual-task-123")
    relay._authorize_active.reset_mock()
    relay._authorize_active.side_effect = RelayError("approval revoked")

    relay.guard_tool = MethodType(RelayAgentBridge.guard_tool, relay)
    active = SimpleNamespace(origin=retry_origin)
    acp = SimpleNamespace(runner=SimpleNamespace(_active_context=active))
    plugin.event_bus.get_service.side_effect = (
        lambda name: acp if name == "acp_orchestrator" else None
    )
    plugin._acp_hub_token = "review-token"
    plugin._acp_hub_generation = "1"
    plugin._acp_hub_tools = SimpleNamespace(call=AsyncMock(return_value="executed"))

    denied = await plugin._handle_acp_hub_call(
        token="review-token", generation="1", name="hub_msg", arguments={}
    )

    assert "authority is no longer valid" in denied["error"].lower()
    relay._authorize_active.assert_called_once()
    plugin._acp_hub_tools.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_ttl_rearm_uses_oldest_remaining_expiry(monkeypatch):
    """Staggered messages expire within one TTL from their own arrival."""
    plugin, _llm = _acp_plugin()
    first = _agent_request("first")
    second = _agent_request("second")
    second.id = "s" * 32
    _held_turn(plugin, first)
    _held_turn(plugin, second)
    monkeypatch.setattr(
        hub_plugin_module,
        "time",
        SimpleNamespace(monotonic=lambda: 1000.0),
    )
    ttl = hub_plugin_module._ACP_BUSY_PARK_TTL_SECONDS
    plugin._acp_busy_parked[0]["ts"] = 1000.0 - ttl - 1.0
    plugin._acp_busy_parked[1]["ts"] = 1000.0 - ttl / 2.0

    plugin._expire_parked_busy()

    delay = plugin._parked_ttl_handle.when() - asyncio.get_running_loop().time()
    plugin._parked_ttl_handle.cancel()
    await asyncio.sleep(0)
    assert delay <= ttl / 2.0 + 1.0


@pytest.mark.asyncio
async def test_expiry_during_inflight_retry_preserves_other_message(monkeypatch):
    """An expiring in-flight head must not cancel admission or strand its follower."""
    plugin, llm = _acp_plugin()
    expired = _agent_request("expired")
    staying = _agent_request("staying", from_identity="onyx@other")
    staying.id = "s" * 32
    _held_turn(plugin, expired)
    _held_turn(plugin, staying)
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def blocked_admission(content, **_kwargs):
        if content == "expired":
            entered.set()
            await resume.wait()
        return {"status": "queued"}

    llm.submit_human_input = AsyncMock(side_effect=blocked_admission)
    now = 1000.0
    ttl = hub_plugin_module._ACP_BUSY_PARK_TTL_SECONDS
    plugin._acp_busy_parked[0]["ts"] = now - ttl - 1.0
    plugin._acp_busy_parked[1]["ts"] = now
    monkeypatch.setattr(
        hub_plugin_module,
        "time",
        SimpleNamespace(monotonic=lambda: now),
    )

    plugin._kick_parked_busy_retries()
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    plugin._expire_parked_busy()
    if plugin._parked_ttl_handle is not None:
        plugin._parked_ttl_handle.cancel()
    await asyncio.wait_for(plugin._busy_retry_task, timeout=1.0)

    assert [call.args[0] for call in llm.submit_human_input.await_args_list] == [
        "expired",
        "staying",
    ]
    assert plugin._acp_busy_parked == []
    assert staying.id in plugin._acp_network_turns
    plugin._end_network_turn.assert_not_awaited()

    detached = list(plugin._busy_detached_tasks)
    resume.set()
    await asyncio.wait_for(asyncio.gather(*detached), timeout=1.0)

    assert expired.id in plugin._acp_network_turns
    plugin._end_network_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_departure_after_steering_send_waits_for_real_admission_result():
    """A delivered steering RPC cannot be rolled back by cancelling its waiter."""
    from acp.connection import Connection

    from kollabor.llm.acp_bridge import ACPRunner

    plugin, llm = _acp_plugin()
    message = _agent_request("remote steering payload", from_identity=ASKER)
    _held_turn(plugin, message)
    sent: list[dict] = []
    sent_event = asyncio.Event()

    class Transport:
        async def send(self, payload):
            sent.append(payload)
            sent_event.set()

    rpc = Connection(None, Transport(), listening=False)

    class HarnessConnection:
        async def ext_method(self, method, params):
            return await rpc.send_request("_" + method, params)

    runner = ACPRunner.__new__(ACPRunner)
    runner.initialize_metadata = {"steering": {"supported": True}}
    runner.capabilities = {}
    runner._active_turn_id = 1
    runner._active_context = SimpleNamespace(
        thread_id=message.thread_id,
        origin={"sender": message.from_agent},
    )
    runner.session_id = "review-session"
    runner._connection = HarnessConnection()

    async def real_steer_admission(
        content, *, origin_context, thread_id, **_kwargs
    ):
        if await runner.steer(
            content, origin=origin_context, thread_id=thread_id
        ):
            return {"status": "injected"}
        return {"status": "rejected", "reason": "acp_queue_full"}

    llm.submit_human_input = AsyncMock(side_effect=real_steer_admission)
    plugin._kick_parked_busy_retries()
    await asyncio.wait_for(sent_event.wait(), timeout=1.0)

    await plugin._sweep_parked_for_departure(ASKER)
    await asyncio.wait_for(plugin._busy_retry_task, timeout=1.0)

    assert sent[0]["method"] == "_session/steering"
    assert len(rpc._pending) == 1
    plugin._end_network_turn.assert_not_awaited()

    detached = list(plugin._busy_detached_tasks)
    rpc._handle_response({
        "jsonrpc": "2.0",
        "id": sent[0]["id"],
        "result": {},
    })
    await asyncio.wait_for(asyncio.gather(*detached), timeout=1.0)

    assert message.id not in plugin._acp_network_turns
    plugin._end_network_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_manual_task_is_not_admitted_from_parked_queue():
    """Retry revalidates the saved relay authority before touching ACP."""
    from contextvars import ContextVar

    from kollabor.llm.llm_coordinator import LLMService
    from plugins.hub.relay_agent import RelayAgentBridge

    plugin, llm = _acp_plugin()
    task_id = "manual-task-123"
    active = SimpleNamespace(record={"id": task_id}, finished=False)
    relay = SimpleNamespace(
        _turn=ContextVar("review_relay_turn", default=None),
        active=active,
        store=SimpleNamespace(transition=MagicMock()),
        llm=SimpleNamespace(is_processing=False),
    )
    plugin._relay_agent = relay
    message = _agent_request(
        "revoked manual prompt", from_identity="relay:manual-peer"
    )
    llm.submit_human_input = AsyncMock(return_value=_busy_reject())
    token = relay._turn.set(task_id)
    try:
        await plugin._submit_authenticated_agent_message(message, llm)
    finally:
        relay._turn.reset(token)
    assert len(plugin._acp_busy_parked) == 1

    await RelayAgentBridge._stop_active(
        relay, "cancelled", "authority revoked"
    )
    assert active.finished

    accepted = []

    class FakeACP:
        async def steer(self, *_args, **_kwargs):
            return False

        def admit(self, text, **kwargs):
            accepted.append((text, kwargs))
            return SimpleNamespace(request_id="admitted", turn_id=1)

    acp = FakeACP()
    coordinator = LLMService.__new__(LLMService)

    async def submit_real_admission(
        content, *, origin_context, thread_id, **_kwargs
    ):
        return await coordinator._admit_acp(
            acp,
            {
                "message": content,
                "origin_context": origin_context,
                "thread_id": thread_id,
            },
            "agent",
        )

    llm.submit_human_input = AsyncMock(side_effect=submit_real_admission)
    await plugin._retry_parked_busy_messages()
    if plugin._parked_ttl_handle is not None:
        plugin._parked_ttl_handle.cancel()

    assert accepted == []
    llm.submit_human_input.assert_not_awaited()
    assert plugin._acp_busy_parked == []
    assert message.id not in plugin._acp_network_turns
    assert plugin._end_network_turn.await_args.kwargs["outcome"] == "unavailable"


@pytest.mark.asyncio
async def test_departed_inflight_rejection_applies_one_deferred_outcome():
    """A swept admission that really rejects ends once with the sweep result."""
    plugin, llm = _acp_plugin()
    message = _agent_request("departing rejection", from_identity=ASKER)
    _held_turn(plugin, message)
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def rejected_admission(*_args, **_kwargs):
        entered.set()
        await resume.wait()
        return {"status": "rejected", "reason": "acp_queue_full"}

    llm.submit_human_input = AsyncMock(side_effect=rejected_admission)
    plugin._kick_parked_busy_retries()
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    await plugin._sweep_parked_for_departure(ASKER)
    await asyncio.wait_for(plugin._busy_retry_task, timeout=1.0)
    plugin._end_network_turn.assert_not_awaited()

    detached = list(plugin._busy_detached_tasks)
    resume.set()
    await asyncio.wait_for(asyncio.gather(*detached), timeout=1.0)

    assert message.id not in plugin._acp_network_turns
    plugin._end_network_turn.assert_awaited_once()
    assert plugin._end_network_turn.await_args.kwargs["outcome"] == "unavailable"


@pytest.mark.asyncio
async def test_shutdown_waits_for_inflight_admission_instead_of_cancelling_it():
    """Shutdown cannot report rollback while an ACP admission may be on the wire."""
    plugin, llm = _acp_plugin()
    message = _agent_request("shutdown admission")
    _held_turn(plugin, message)
    entered = asyncio.Event()
    resume = asyncio.Event()
    cancelled = asyncio.Event()

    async def rejected_admission(*_args, **_kwargs):
        entered.set()
        try:
            await resume.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"status": "rejected", "reason": "acp_queue_full"}

    llm.submit_human_input = AsyncMock(side_effect=rejected_admission)
    plugin._kick_parked_busy_retries()
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    plugin._relay_agent = None
    plugin._vault = None
    plugin._change_feed = None
    plugin._startup_task = None
    plugin._heartbeat_task = None
    plugin._mailbox_task = None
    plugin._dreaming_task = None
    plugin._notify_task = None
    plugin._cron_task = None
    plugin._autosave_task = None
    plugin._supervisor_task = None
    plugin._presence_sweep_task = None
    plugin._bridge_task = None
    plugin._email_task = None
    plugin._started = False
    plugin._socket_server = None
    plugin._election = None

    shutdown_task = asyncio.create_task(plugin.shutdown(exit_process=False))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not shutdown_task.done()
    assert not cancelled.is_set()
    plugin._end_network_turn.assert_not_awaited()

    resume.set()
    await asyncio.wait_for(shutdown_task, timeout=1.0)

    assert not cancelled.is_set()
    assert message.id not in plugin._acp_network_turns
    assert plugin._end_network_turn.await_args.kwargs["outcome"] == "busy"


@pytest.mark.asyncio
async def test_review_authority_revoked_while_pre_admission_hook_waits():
    """A grant revoked after the retry check must not reach ACP admission."""
    from contextvars import ContextVar

    from kollabor.llm.llm_coordinator import LLMService
    from plugins.hub.relay_agent import RelayAgentBridge

    plugin, _ = _acp_plugin()
    message = _agent_request("revoked while hook waits", from_identity="relay:manual-peer")
    _held_turn(plugin, message)
    task_id = "manual-task-123"
    entry = plugin._acp_busy_parked[0]
    entry["origin_context"] = {
        "sender": message.from_agent,
        "relay_turn_id": task_id,
        "authenticated": True,
        "operator": False,
    }
    active = SimpleNamespace(record={"id": task_id}, finished=False)
    relay = SimpleNamespace(
        _turn=ContextVar("review_turn", default=None),
        active=active,
        _authorize_active=MagicMock(),
        store=SimpleNamespace(transition=MagicMock()),
        llm=SimpleNamespace(is_processing=False),
    )
    plugin._relay_agent = relay
    entered, resume = asyncio.Event(), asyncio.Event()
    accepted = []
    acp = SimpleNamespace(active=True, steer=AsyncMock(return_value=False))
    acp.admit = MagicMock(
        side_effect=lambda text, **kwargs: (
            accepted.append((text, kwargs))
            or SimpleNamespace(request_id="accepted", turn_id=1)
        )
    )
    service = LLMService.__new__(LLMService)
    service._profile_selects_acp = lambda: (True, False)

    class EventBus:
        def get_service(self, name):
            return {
                "acp_orchestrator": acp,
                "hub_plugin": plugin,
            }.get(name)

        async def emit_with_hooks(self, _event, data, source):
            entered.set()
            await resume.wait()  # USER_INPUT hook, before any ACP delivery
            result = await service._admit_acp(acp, data, source)
            return {
                "main": {
                    "hook_results": [{
                        "hook_key": "llm_core.process_user_input",
                        "success": True,
                        "result": result,
                    }]
                }
            }

    service.event_bus = EventBus()
    valid_admission = await service._admit_acp(
        acp,
        {
            "message": "valid while relay grant is active",
            "origin_context": entry["origin_context"],
        },
        "agent",
    )
    assert valid_admission["status"] == "queued"
    accepted.clear()

    plugin.event_bus.get_service.side_effect = lambda name: (
        service if name == "llm_service" else None
    )
    plugin._kick_parked_busy_retries()
    await asyncio.wait_for(entered.wait(), timeout=1)

    await RelayAgentBridge._stop_active(relay, "cancelled", "authority revoked")
    resume.set()
    await asyncio.wait_for(plugin._busy_retry_task, timeout=1)

    assert active.finished is True
    assert not accepted, "revoked manual prompt was admitted after the pre-admission hook"


@pytest.mark.asyncio
async def test_review_shutdown_with_nonresponsive_admission_is_bounded(
    monkeypatch, caplog
):
    """A stalled admission must not hold plugin shutdown indefinitely."""
    monkeypatch.setattr(
        hub_plugin_module, "_ACP_BUSY_SHUTDOWN_WAIT_SECONDS", 0.05
    )
    plugin, llm = _acp_plugin()
    message = _agent_request("stalled admission")
    _held_turn(plugin, message)
    entered, resume = asyncio.Event(), asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked(*_args, **_kwargs):
        entered.set()
        try:
            await resume.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"status": "rejected", "reason": "acp_queue_full"}

    llm.submit_human_input = AsyncMock(side_effect=blocked)
    plugin._kick_parked_busy_retries()
    await asyncio.wait_for(entered.wait(), timeout=1)
    for name in (
        "_relay_agent", "_vault", "_change_feed", "_startup_task",
        "_heartbeat_task", "_mailbox_task", "_dreaming_task", "_notify_task",
        "_cron_task", "_autosave_task", "_supervisor_task",
        "_presence_sweep_task", "_bridge_task", "_email_task",
        "_socket_server", "_election",
    ):
        setattr(plugin, name, None)
    plugin._started = False

    caplog.set_level(logging.WARNING, logger=hub_plugin_module.__name__)
    shutdown = asyncio.create_task(plugin.shutdown(exit_process=False))
    await asyncio.sleep(0.2)
    blocked_shutdown = not shutdown.done()
    resume.set()
    await asyncio.wait_for(shutdown, timeout=1)
    await asyncio.wait_for(plugin._busy_retry_task, timeout=1)

    assert not cancelled.is_set(), "shutdown must not cancel an admission already in flight"
    assert not blocked_shutdown, "shutdown waited indefinitely on a nonresponsive admission"
    assert "timed out waiting for" in caplog.text
