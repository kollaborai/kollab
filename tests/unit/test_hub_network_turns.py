"""A remote request's replies go on its own thread, and its turn's end is signalled.

docs/specs/agent-network-simple-flow.md section 7 and Stories 2 and 3. A reply
belongs to the request that woke the turn that sends it -- not the oldest
request from that agent. The request stays open until the turn ends, so an
interim message and the answer both land on its thread, and the runtime (never
the model) then sends the requester an end-of-turn frame so `kollab --hub msg`
knows when to stop waiting. The relay hands the model one request at a time.

The first half drives one plugin against a fake relay bridge (the pattern of
test_hub_msg_remote_target.py); the second half drives two real bridges over the
encrypted in-memory wire of test_relay_agent_bridge.py's `bridges` fixture.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.queue_processor import QueueProcessor
from kollabor_events import EventType
from plugins.hub.device_names import default_device_name, format_handle
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_conversations import (
    MAX_CONTENT,
    MAX_TURN_REPLIES,
    validate_message,
)
from plugins.hub.relay_state import RelayError
from tests.unit.test_relay_network_trust import set_trust, warm_directory

ASKER = "lapis@mac-kollab"
OPS = "ops@alzan-prod-home"
T1, T2, T3 = "1" * 32, "2" * 32, "3" * 32  # request threads
W1, W2, W3 = "a" * 32, "b" * 32, "c" * 32  # request message ids
DONE = "The receiving agent finished this request."
FAILED = "The receiving agent could not complete this request."
NEW_REQUEST_NOTE = "its reply arrives by itself"


def _idle_llm() -> SimpleNamespace:
    return SimpleNamespace(
        is_processing=False,
        conversation_history=[],
        _queue_processor=SimpleNamespace(last_turn_error=None),
    )


def _responder(sent: list, *, llm=None, receipts=None):
    """infra: a plugin whose hub_msg goes through the real router to a fake relay."""
    receipts = iter(receipts or [])

    async def send(address, content, **kwargs):
        sent.append({"address": address, "content": content, **kwargs})
        return next(
            receipts, {"id": "b" * 32, "state": "queued", "duplicate": False}
        )

    relay = SimpleNamespace(
        _turn=SimpleNamespace(get=lambda: None),
        _injecting_message=None,
        defer_local=AsyncMock(return_value=False),
        resolve_handle=lambda handle: f"relay:resolved:{handle}",
        send=send,
        active=None,
        finish_response=AsyncMock(),
    )
    llm = llm if llm is not None else _idle_llm()
    bus = MagicMock()
    bus.emit_with_hooks = AsyncMock()
    bus.get_service.side_effect = lambda name: llm if name == "llm_service" else None
    plugin = HubPlugin(event_bus=bus)
    plugin._vault = None
    plugin._task_ledger = None
    plugin._nudge_engine = None
    plugin._identity = SimpleNamespace(
        identity="infra", agent_id="infra-1", state="idle", current_task=""
    )
    plugin._roster = []
    plugin._relay_agent = relay
    plugin._presence = MagicMock()
    plugin._presence.scan_all_presence.return_value = []
    plugin._display_outgoing_message = MagicMock()
    plugin._display_hub_message = MagicMock()
    plugin._bridge_forward = AsyncMock()
    plugin._maybe_route_to_coordinator = AsyncMock()
    return plugin, llm


def _request(thread_id: str, wire_id: str, content: str = "check the tunnel", *, sender=ASKER):
    """A request as the relay bridge delivers it (no reply_to: it answers nothing)."""
    return HubMessage(
        id=wire_id,
        action="message",
        from_agent="relay:peer",
        from_identity=sender,
        to="infra",
        content=content,
        thread_id=thread_id,
        metadata={"network": {"from_device": "mac-kollab", "trust": "open"}},
    )


async def _model_turn(plugin, llm, *texts, to=ASKER):
    """The model is called once, sends each text to ``to`` with hub_msg, goes idle."""
    await plugin._set_working({"messages": []})  # LLM_REQUEST_PRE
    llm.is_processing = True
    results = [
        await plugin._handle_hub_msg_tool({"id": f"t{i}", "to": to, "content": text})
        for i, text in enumerate(texts)
    ]
    llm.is_processing = False
    return results


async def _settle(plugin, llm, start: float = 100.0):
    """The queue processor reports the chain done; the relay's next tick sends the end."""
    plugin.network_chain_ended(failed=bool(llm._queue_processor.last_turn_error))
    await plugin.settle_network_turn(llm, now=start)


def _ends(sent: list) -> list:
    return [s for s in sent if s.get("turn_end")]


def _replies(sent: list) -> list:
    return [s for s in sent if not s.get("turn_end")]


# --------------------------------------------------------------------- #
# Replies go on the thread of the request the turn is handling
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_interim_and_the_answer_both_go_on_the_requests_thread():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))

    interim, answer = await _model_turn(
        plugin, llm, "on it", "wg0 peer: latest handshake 38 s ago. Healthy."
    )

    assert interim.success and answer.success
    assert [(s["thread_id"], s["reply_to"]) for s in _replies(sent)] == [
        (T1, W1),
        (T1, W1),
    ]
    # Answers on a received request read plain; only a new request gets the
    # "its reply arrives by itself" note.
    assert interim.output == answer.output == f"sent to {ASKER}"


@pytest.mark.asyncio
async def test_two_requests_answered_in_reverse_order_never_cross():
    sent: list = []
    plugin, llm = _responder(sent)

    # The second request is the one the agent handles first.
    await plugin._on_message_received(_request(T2, W2, "second"))
    await _model_turn(plugin, llm, "answer to second")
    await _settle(plugin, llm)
    await plugin._on_message_received(_request(T1, W1, "first"))
    await _model_turn(plugin, llm, "on it", "answer to first")
    await _settle(plugin, llm, start=200.0)

    assert [(s["content"], s["thread_id"], s["reply_to"]) for s in _replies(sent)] == [
        ("answer to second", T2, W2),
        ("on it", T1, W1),
        ("answer to first", T1, W1),
    ]
    assert [(e["thread_id"], e["turn_end"]) for e in _ends(sent)] == [
        (T2, {"replies": 1, "failed": False}),
        (T1, {"replies": 2, "failed": False}),
    ]


@pytest.mark.asyncio
async def test_a_message_to_someone_else_is_not_on_the_requests_thread():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))

    [answer] = await _model_turn(plugin, llm, "all clear")
    other = await plugin._handle_hub_msg_tool(
        {"id": "t9", "to": OPS, "content": "rotate the logs"}
    )

    assert answer.success
    to_ops = [s for s in sent if s["address"].endswith(OPS)]
    assert len(to_ops) == 1 and to_ops[0]["reply_to"] == ""
    assert to_ops[0]["thread_id"] != T1
    assert other.output.startswith(f"sent to {OPS}; {NEW_REQUEST_NOTE}")
    # It was not a reply to the asker, so the asker is not told to expect it.
    await _settle(plugin, llm)
    assert _ends(sent)[0]["turn_end"]["replies"] == 1


@pytest.mark.asyncio
async def test_the_same_words_on_two_requests_are_both_sent():
    """The dedup that stops a model resending itself must not eat request 2's
    answer just because request 1 got the same words."""
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    [first] = await _model_turn(plugin, llm, "done")
    await _settle(plugin, llm)
    await plugin._on_message_received(_request(T2, W2))
    [second] = await _model_turn(plugin, llm, "done")

    assert first.output == second.output == f"sent to {ASKER}"
    assert [(s["thread_id"], s["content"]) for s in _replies(sent)] == [
        (T1, "done"),
        (T2, "done"),
    ]


@pytest.mark.asyncio
async def test_the_same_words_twice_on_one_thread_are_sent_once_and_counted_once():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))

    first, again = await _model_turn(plugin, llm, "done", "done")
    await _settle(plugin, llm)

    assert first.output == f"sent to {ASKER}"
    assert again.output.startswith("not sent again")
    assert len(_replies(sent)) == 1
    assert _ends(sent)[0]["turn_end"]["replies"] == 1  # the shell waits for one


@pytest.mark.asyncio
async def test_a_reply_the_network_refused_is_not_counted():
    refused = {
        "id": "f" * 32,
        "state": "rejected",
        "reason": "not_authorized",
        "duplicate": False,
    }
    sent: list = []
    plugin, llm = _responder(sent, receipts=[refused])
    await plugin._on_message_received(_request(T1, W1))

    [failed] = await _model_turn(plugin, llm, "on it")
    await _settle(plugin, llm)

    assert not failed.success
    assert _ends(sent)[0]["turn_end"] == {"replies": 0, "failed": False}


# --------------------------------------------------------------------- #
# The runtime ends the request: one end frame, after the turn, never shown
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_turn_end_frame_carries_the_reply_count_and_closes_the_request():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _model_turn(plugin, llm, "on it", "the answer")
    assert plugin.network_turn_open()
    assert _ends(sent) == []  # not before the turn is over

    await _settle(plugin, llm)

    [end] = _ends(sent)
    assert (end["thread_id"], end["reply_to"]) == (T1, W1)
    assert end["turn_end"] == {"replies": 2, "failed": False}
    assert end["content"] == DONE and end["address"] == f"relay:resolved:{ASKER}"
    assert not plugin.network_turn_open()
    # Nothing on this screen: the frame is the runtime's, not a message.
    assert plugin._display_outgoing_message.call_count == 2
    # Nothing is owed now: the next message to the asker is a new request.
    fresh = await plugin._handle_hub_msg_tool(
        {"id": "t9", "to": ASKER, "content": "one more thing"}
    )
    assert fresh.output.startswith(f"sent to {ASKER}; {NEW_REQUEST_NOTE}")
    assert _replies(sent)[-1]["reply_to"] == ""
    assert _replies(sent)[-1]["thread_id"] != T1


@pytest.mark.asyncio
async def test_a_gap_with_the_model_idle_between_the_tool_and_the_answer_is_not_the_end():
    # Live (dev4): the terminal tool ran at 10:21:09, the hub_msg answer went at
    # 10:21:14, and in between the model read idle for over a second. The old idle
    # debounce sent the end frame then, and the shell printed "finished without a
    # reply" while the answer was still coming.
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))

    # Delivered but the model has not been called yet: not over, however long.
    for now in (100.0, 130.0, 160.0):
        await plugin.settle_network_turn(llm, now=now)
    assert _ends(sent) == [] and plugin.network_turn_open()

    # The model is called and runs a terminal tool; then a gap with nothing running.
    await plugin._set_working({"messages": []})
    llm.is_processing = True
    llm.is_processing = False
    for now in (200.0, 200.5, 201.5, 204.0):
        await plugin.settle_network_turn(llm, now=now)
    assert _ends(sent) == [] and plugin.network_turn_open()

    # The next model call answers with hub_msg; the chain is still not finished.
    await plugin._set_working({"messages": []})
    llm.is_processing = True
    await plugin._handle_hub_msg_tool({"id": "t0", "to": ASKER, "content": "46G free on /"})
    await plugin.settle_network_turn(llm, now=206.0)
    assert _ends(sent) == []

    # The queue processor finishes the chain: the end frame follows the answer.
    llm.is_processing = False
    plugin.network_chain_ended()
    await plugin.settle_network_turn(llm, now=206.2)

    assert [s["content"] for s in _replies(sent)] == ["46G free on /"]
    [end] = _ends(sent)
    assert end["turn_end"] == {"replies": 1, "failed": False}
    assert sent[-1] is end and not plugin.network_turn_open()


@pytest.mark.asyncio
async def test_a_plain_text_answer_after_a_gap_is_forwarded_then_the_turn_ends():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await plugin._set_working({"messages": []})  # the terminal tool call
    for now in (200.0, 201.5, 204.0):  # the gap
        await plugin.settle_network_turn(llm, now=now)
    assert _ends(sent) == [] and _replies(sent) == []

    await _plain_answer(plugin, llm, "46G free on /")
    await plugin.settle_network_turn(llm, now=206.0)
    assert _ends(sent) == [] and _replies(sent) == []  # chain not reported done yet

    plugin.network_chain_ended()
    await plugin.settle_network_turn(llm, now=206.2)

    assert [s["content"] for s in _replies(sent)] == ["46G free on /"]
    [end] = _ends(sent)
    assert end["turn_end"] == {"replies": 1, "failed": False}
    assert sent[-1] is end


@pytest.mark.asyncio
async def test_a_turn_that_errors_after_a_gap_still_ends_failed():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await plugin._set_working({"messages": []})
    for now in (200.0, 201.5, 204.0):
        await plugin.settle_network_turn(llm, now=now)
    assert _ends(sent) == []

    plugin.network_chain_ended(failed=True)
    await plugin.settle_network_turn(llm, now=205.0)

    [end] = _ends(sent)
    assert end["turn_end"] == {"replies": 0, "failed": True}
    assert end["content"] == FAILED and _replies(sent) == []


@pytest.mark.asyncio
async def test_two_queued_requests_each_end_on_their_own_chain_end():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _model_turn(plugin, llm, "one")
    await _settle(plugin, llm)
    assert not plugin.network_turn_open()

    # The second request is handed over; a chain end before the model runs for it
    # (some other chain's) is not its turn.
    await plugin._on_message_received(_request(T2, W2))
    plugin.network_chain_ended()
    await plugin.settle_network_turn(llm, now=300.0)
    assert plugin.network_turn_open() and len(_ends(sent)) == 1

    await _model_turn(plugin, llm, "two")
    await _settle(plugin, llm, start=310.0)

    assert [(s["thread_id"], s["content"]) for s in _replies(sent)] == [(T1, "one"), (T2, "two")]
    assert [(s["thread_id"], s["turn_end"]) for s in _ends(sent)] == [
        (T1, {"replies": 1, "failed": False}),
        (T2, {"replies": 1, "failed": False}),
    ]


@pytest.mark.asyncio
async def test_a_turn_that_errored_ends_with_a_failed_frame_that_names_no_error_text():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _model_turn(plugin, llm, "on it")
    llm._queue_processor.last_turn_error = "401 from api.example.com key=sk-secret"

    await _settle(plugin, llm)

    [end] = _ends(sent)
    assert end["turn_end"] == {"replies": 1, "failed": True}
    assert end["content"] == FAILED
    assert "sk-secret" not in repr(end) and "example.com" not in repr(end)


@pytest.mark.asyncio
async def test_an_acknowledgement_starts_no_turn_and_its_asker_is_released_at_once():
    sent: list = []
    plugin, llm = _responder(sent)
    llm.is_processing = True  # busy with something else: it must not wait for that

    await plugin._on_message_received(_request(T1, W1, "thanks, got it"))
    assert plugin.network_turn_open()
    assert [
        c
        for c in plugin.event_bus.emit_with_hooks.await_args_list
        if c.args[0] is EventType.TRIGGER_LLM_CONTINUE
    ] == []  # the model was not woken

    await plugin.settle_network_turn(llm, now=100.0)

    [end] = _ends(sent)
    assert (end["thread_id"], end["turn_end"]) == (T1, {"replies": 0, "failed": False})
    assert not plugin.network_turn_open()


@pytest.mark.asyncio
async def test_a_request_with_no_model_to_run_releases_its_asker_too():
    sent: list = []
    plugin, llm = _responder(sent)
    plugin.event_bus.get_service.side_effect = lambda name: None

    await plugin._on_message_received(_request(T1, W1))
    await plugin.settle_network_turn(llm, now=100.0)

    assert [e["turn_end"] for e in _ends(sent)] == [{"replies": 0, "failed": False}]


@pytest.mark.asyncio
async def test_a_request_that_finds_the_model_busy_waits_for_that_chain_to_finish():
    """The chain that was running when it arrived is not its turn; the next is."""
    sent: list = []
    plugin, llm = _responder(sent)
    llm.is_processing = True
    await plugin._on_message_received(_request(T1, W1))

    # The running chain calls the model again and ends.
    await plugin._set_working({"messages": []})
    for now in (100.0, 101.0):
        await plugin.settle_network_turn(llm, now=now)
    llm.is_processing = False
    plugin.network_chain_ended()  # the running chain's end is not the request's turn
    for now in (110.0, 111.0, 120.0):
        await plugin.settle_network_turn(llm, now=now)
    plugin.network_chain_ended()  # nor is one before the model ran for it
    await plugin.settle_network_turn(llm, now=121.0)
    assert _ends(sent) == [] and plugin.network_turn_open()

    # Its own turn (the retry chain) runs and ends.
    await _model_turn(plugin, llm, "the answer")
    await _settle(plugin, llm, start=200.0)

    [end] = _ends(sent)
    assert end["turn_end"] == {"replies": 1, "failed": False}
    assert _replies(sent)[0]["thread_id"] == T1


@pytest.mark.asyncio
async def test_a_request_no_turn_ever_handled_is_dropped_at_the_wait_ceiling():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))

    await plugin.settle_network_turn(llm, now=time.monotonic() + 60)
    assert plugin.network_turn_open()  # the shell may still be waiting
    await plugin.settle_network_turn(llm, now=time.monotonic() + 601)

    # The shell gave up long ago; the relay must not stay closed to requests.
    assert not plugin.network_turn_open()
    assert _ends(sent) == []


# --------------------------------------------------------------------- #
# The wire: turn_end is a strict optional field
# --------------------------------------------------------------------- #


def _wire_payload(**overrides) -> dict:
    payload = {
        "id": "d" * 32,
        "thread_id": T1,
        "reply_to": W1,
        "from": "relay:" + "e" * 64 + ":" + "f" * 32 + ":infra-1",
        "to": "relay:" + "9" * 64 + ":" + "8" * 32 + ":lapis-1",
        "from_identity": "infra",
        "from_coordinator": False,
        "to_identity": "lapis",
        "to_coordinator": False,
        "content": DONE,
        "kind": "message",
        "expires_at": int(time.time()) + 300,
        "from_device": "alzan-prod-home",
        "turn_end": {"replies": 2, "failed": False},
    }
    payload.update(overrides)
    return payload


def _validate(payload: dict) -> dict:
    return validate_message(payload, peer_key="e" * 64, workspace_id="8" * 32)


def test_a_well_formed_turn_end_is_accepted_and_a_plain_message_needs_none():
    assert _validate(_wire_payload())["turn_end"] == {"replies": 2, "failed": False}
    plain = _wire_payload()
    del plain["turn_end"]
    assert "turn_end" not in _validate(plain)


@pytest.mark.parametrize(
    "bad",
    [
        "yes",
        None,
        [],
        {},
        {"replies": 2},
        {"replies": 2, "failed": False, "extra": 1},
        {"replies": -1, "failed": False},
        {"replies": True, "failed": False},  # a bool is not a count
        {"replies": "2", "failed": False},
        {"replies": MAX_TURN_REPLIES + 1, "failed": False},
        {"replies": 2, "failed": 0},
    ],
)
def test_a_malformed_turn_end_is_refused(bad):
    with pytest.raises(RelayError, match="invalid turn end"):
        _validate(_wire_payload(turn_end=bad))


def test_only_a_plain_message_can_be_a_turn_end():
    with pytest.raises(RelayError, match="invalid turn end"):
        _validate(_wire_payload(kind="result"))


# --------------------------------------------------------------------- #
# Two real bridges over the encrypted in-memory wire
# --------------------------------------------------------------------- #


async def _network(bridges):
    members, _ = bridges
    (left, hub_left, left_model, _), (right, hub_right, right_model, _) = members
    set_trust(left, "open")
    set_trust(right, "open")
    await warm_directory(left)
    await warm_directory(right)
    return SimpleNamespace(
        left=left,
        hub_left=hub_left,
        left_model=left_model,
        right=right,
        hub_right=hub_right,
        right_model=right_model,
        # each side's handle for the other (both agents are called sapphire)
        to_right=format_handle("sapphire", default_device_name(right.workspace)),
        to_left=format_handle("sapphire", default_device_name(left.workspace)),
    )


async def _shell_request(net, text: str) -> asyncio.Task:
    """`kollab --hub msg` on the left, as the daemon runs it; returns its frames."""
    frames: list = []

    async def run():
        async for frame in net.hub_left._handle_network_send_request(
            net.to_right, text, 30
        ):
            frames.append(frame)
        return frames

    task = asyncio.create_task(run())
    task.frames = frames  # visible while the wait is still open
    await asyncio.sleep(0.05)  # sent
    return task


async def _far_turn(net, *texts):
    """The right agent's model runs its turn: sends each text to the left, ends."""
    await net.hub_right._set_working({"messages": []})
    net.right_model.is_processing = True
    for i, text in enumerate(texts):
        result = await net.hub_right._handle_hub_msg_tool(
            {"id": f"t{i}", "to": net.to_left, "content": text}
        )
        assert result.success, result.output
    net.right_model.is_processing = False
    net.hub_right.network_chain_ended()  # the queue processor finished the chain
    await net.hub_right.settle_network_turn(net.right_model)  # the tick: end frame


async def _drain_left(net, ticks: int = 10):
    for _ in range(ticks):
        await net.left._tick()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_shell_gets_the_interim_the_answer_and_the_end_over_the_real_relay(
    bridges
):
    net = await _network(bridges)
    shell = await _shell_request(net, "rotate the nginx logs")

    await net.right._tick()  # the request reaches the model
    assert "rotate the nginx logs" in net.right_model.conversation_history[-1].content
    assert net.hub_right.network_turn_open()
    await _far_turn(net, "on it", "rotated 3 files, freed 412 MB.")
    assert not net.hub_right.network_turn_open()

    await _drain_left(net)
    frames = await asyncio.wait_for(shell, timeout=5)

    assert frames == [
        {"type": "network_reply", "from": net.to_right, "content": "on it"},
        {
            "type": "network_reply",
            "from": net.to_right,
            "content": "rotated 3 files, freed 412 MB.",
        },
        {"type": "network_done", "replies": 2},
    ]


@pytest.mark.asyncio
async def test_the_end_frame_is_neither_shown_nor_given_to_the_model(
    bridges
):
    net = await _network(bridges)
    shell = await _shell_request(net, "rotate the nginx logs")
    await net.right._tick()
    await _far_turn(net, "on it", "rotated 3 files, freed 412 MB.")
    await _drain_left(net)
    await asyncio.wait_for(shell, timeout=5)

    # Two replies were shown on the asker's screen; the end frame was not.
    shown = [c.args[0].content for c in net.hub_left._display_hub_message.call_args_list]
    assert shown == ["on it", "rotated 3 files, freed 412 MB."]
    # The replies are the shell's: they did not wake the asker's model either.
    assert net.left_model.conversation_history == []
    assert net.left_model.contexts == []
    # Every frame was consumed, none left in the store.
    assert net.left.store.queued(net.left.identity.agent_id) == []


@pytest.mark.asyncio
async def test_the_relay_hands_the_model_one_request_at_a_time_and_each_shell_gets_its_own(
    bridges
):
    net = await _network(bridges)
    first = await _shell_request(net, "first: report the hostname")
    second = await _shell_request(net, "second: report the disk space")

    await net.right._tick()
    assert "first: report" in net.right_model.conversation_history[-1].content
    # The second request waits its turn, however many times the relay ticks.
    for _ in range(3):
        await net.right._tick()
    assert len(net.right_model.conversation_history) == 1
    assert len(net.right.store.queued(net.right.identity.agent_id)) == 1

    await _far_turn(net, "on it", "the host is infra-box")
    await net.right._tick()  # the turn ended: the second request goes in
    assert "second: report" in net.right_model.conversation_history[-1].content
    await _far_turn(net, "412 GB free")

    await _drain_left(net, 20)
    assert [f.get("content") for f in await asyncio.wait_for(first, 5)] == [
        "on it",
        "the host is infra-box",
        None,
    ]
    assert [f.get("content") for f in await asyncio.wait_for(second, 5)] == [
        "412 GB free",
        None,
    ]


@pytest.mark.asyncio
async def test_requests_handled_in_reverse_order_still_answer_their_own_shell(
    bridges
):
    net = await _network(bridges)
    first = await _shell_request(net, "first: report the hostname")
    second = await _shell_request(net, "second: report the disk space")
    queued = net.right.store.queued(net.right.identity.agent_id)
    assert len(queued) == 2

    # The agent gets to the second request first.
    await net.right._deliver_open_message(net.right.store.task(queued[1]["id"]))
    await _far_turn(net, "412 GB free")
    await net.right._deliver_open_message(net.right.store.task(queued[0]["id"]))
    await _far_turn(net, "on it", "the host is infra-box")

    await _drain_left(net, 20)
    assert [f.get("content") for f in await asyncio.wait_for(first, 5)] == [
        "on it",
        "the host is infra-box",
        None,
    ]
    assert [f.get("content") for f in await asyncio.wait_for(second, 5)] == [
        "412 GB free",
        None,
    ]


@pytest.mark.asyncio
async def test_an_agent_that_asked_gets_the_reply_and_no_end_frame_reaches_its_model(
    bridges
):
    """Story 2: lapis asks, infra answers. lapis wakes on the answer (a normal
    hub message) and the runtime's end frame never appears to it."""
    net = await _network(bridges)
    # lapis (left) asks infra (right) with hub_msg.
    result = await net.hub_left._handle_hub_msg_tool(
        {"id": "t0", "to": net.to_right, "content": "check the tunnel"}
    )
    assert result.success
    await net.right._tick()
    await _far_turn(net, "handshake 38 s ago. Healthy.")

    await _drain_left(net)

    assert len(net.left_model.conversation_history) == 1  # the answer, once
    assert "handshake 38 s ago" in net.left_model.conversation_history[0].content
    assert DONE not in net.left_model.conversation_history[0].content
    assert net.hub_left._display_hub_message.call_count == 1
    assert net.left.store.queued(net.left.identity.agent_id) == []


# --------------------------------------------------------------------- #
# A plain-text answer is the reply when the turn sent none
# --------------------------------------------------------------------- #


def _response(text: str, **extra) -> dict:
    """The LLM_RESPONSE_POST data of one model response."""
    return {"response_text": text, "clean_response": text, "turn_completed": True, **extra}


async def _plain_answer(plugin, llm, text: str):
    """The model is called once and answers in plain text (no hub_msg), goes idle."""
    await plugin._set_working({"messages": []})  # LLM_REQUEST_PRE
    llm.is_processing = True
    await plugin._parse_hub_messages(_response(text))  # LLM_RESPONSE_POST
    llm.is_processing = False


@pytest.mark.asyncio
async def test_a_plain_text_answer_is_sent_as_the_reply_before_the_end_frame():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1, "report free disk space"))
    await _plain_answer(plugin, llm, "412 GB free on /.")
    assert sent == []  # not before the turn is over

    await _settle(plugin, llm)

    [reply] = _replies(sent)
    [end] = _ends(sent)
    assert (reply["content"], reply["thread_id"], reply["reply_to"]) == (
        "412 GB free on /.",
        T1,
        W1,
    )
    assert reply["address"] == f"relay:resolved:{ASKER}"
    assert end["turn_end"] == {"replies": 1, "failed": False}
    assert sent.index(reply) < sent.index(end)


@pytest.mark.asyncio
async def test_an_answer_sent_with_hub_msg_is_not_sent_again_as_plain_text():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _model_turn(plugin, llm, "the answer")
    await plugin._parse_hub_messages(_response("Sent it."))

    await _settle(plugin, llm)

    assert [r["content"] for r in _replies(sent)] == ["the answer"]
    assert _ends(sent)[0]["turn_end"] == {"replies": 1, "failed": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["", "  \n ", "<think>hmm</think>", "<hub_status/> <scratchpad>n</scratchpad>"],
)
async def test_an_answer_with_no_text_left_sends_no_reply(text):
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _plain_answer(plugin, llm, text)

    await _settle(plugin, llm)

    assert _replies(sent) == []
    assert _ends(sent)[0]["turn_end"] == {"replies": 0, "failed": False}


@pytest.mark.asyncio
async def test_only_the_response_that_ends_the_chain_is_the_answer():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await plugin._set_working({"messages": []})
    llm.is_processing = True
    await plugin._parse_hub_messages(
        _response("Checking the disk.", all_tools=[{"type": "terminal"}], turn_completed=False)
    )
    await plugin._set_working({"messages": []})  # the tool result goes back
    await plugin._parse_hub_messages(_response(""))
    llm.is_processing = False

    await _settle(plugin, llm)

    assert _replies(sent) == []  # the interim is not the answer


@pytest.mark.asyncio
async def test_a_failed_turn_sends_no_text_only_the_failed_frame():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _plain_answer(plugin, llm, "half an answer")
    llm._queue_processor.last_turn_error = "401 from api.example.com"

    await _settle(plugin, llm)

    assert _replies(sent) == []
    [end] = _ends(sent)
    assert end["turn_end"] == {"replies": 0, "failed": True} and end["content"] == FAILED


@pytest.mark.asyncio
async def test_hub_xml_thinking_and_control_characters_are_stripped_from_the_answer():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _plain_answer(
        plugin,
        llm,
        "<think>they want the disk</think>412 GB free. <hub_status/>"
        '<hub_msg to="ops">side note</hub_msg><scratchpad>n</scratchpad>Done.\x07',
    )

    await _settle(plugin, llm)

    [reply] = _replies(sent)
    assert reply["content"] == "412 GB free. Done."


@pytest.mark.asyncio
async def test_a_long_answer_is_cut_to_the_wire_limit_on_a_character_boundary():
    sent: list = []
    plugin, llm = _responder(sent)
    await plugin._on_message_received(_request(T1, W1))
    await _plain_answer(plugin, llm, "\u00e9" * 20_000)  # 40000 bytes

    await _settle(plugin, llm)

    [reply] = _replies(sent)
    assert len(reply["content"].encode("utf-8")) <= MAX_CONTENT
    assert reply["content"].endswith("\u00e9...")


async def _far_plain_turn(net, text: str):
    """The right agent's model runs its turn and answers in plain text, no hub_msg."""
    net.hub_right._maybe_route_to_coordinator = AsyncMock()
    await net.hub_right._set_working({"messages": []})
    net.right_model.is_processing = True
    await net.hub_right._parse_hub_messages(_response(text))
    net.right_model.is_processing = False
    net.hub_right.network_chain_ended()  # the queue processor finished the chain
    await net.hub_right.settle_network_turn(net.right_model)  # the tick: reply, end


@pytest.mark.asyncio
async def test_a_shell_gets_a_plain_text_answer_over_the_real_relay(bridges):
    net = await _network(bridges)
    shell = await _shell_request(net, "report free disk space")
    await net.right._tick()
    await _far_plain_turn(net, "412 GB free on /.")

    await _drain_left(net)
    frames = await asyncio.wait_for(shell, timeout=5)

    # network_done is what makes `kollab --hub msg` print the reply and exit 0.
    assert frames == [
        {"type": "network_reply", "from": net.to_right, "content": "412 GB free on /."},
        {"type": "network_done", "replies": 1},
    ]


@pytest.mark.asyncio
async def test_a_shell_waits_out_a_gap_before_the_answer_and_gets_it_then_the_end(bridges):
    net = await _network(bridges)
    shell = await _shell_request(net, "report free disk space")
    await net.right._tick()  # the request reaches the model
    await net.hub_right._set_working({"messages": []})  # the terminal tool call
    for now in (200.0, 200.5, 201.5, 204.0):  # the model reads idle for over a second
        await net.hub_right.settle_network_turn(net.right_model, now=now)
    await _drain_left(net)
    assert net.hub_right.network_turn_open() and not shell.done()
    assert shell.frames == []  # no "finished without a reply" while the answer is coming

    await _far_turn(net, "46G free on /")

    await _drain_left(net)
    frames = await asyncio.wait_for(shell, timeout=5)
    assert frames == [
        {"type": "network_reply", "from": net.to_right, "content": "46G free on /"},
        {"type": "network_done", "replies": 1},
    ]


# --------------------------------------------------------------------- #
# The queue processor reports a chain finished only when nothing is left
# --------------------------------------------------------------------- #


def _processor(*, queued=0, completed=True, error=None, cancelled=False):
    qp = QueueProcessor.__new__(QueueProcessor)
    qp.processing_queue = asyncio.Queue()
    for item in range(queued):
        qp.processing_queue.put_nowait(item)
    qp._turn_lock = asyncio.Lock()
    qp.turn_completed = completed
    qp.last_turn_error = error
    qp._cancel_processing = cancelled
    hub = MagicMock()
    qp.event_bus = SimpleNamespace(get_service=lambda name: hub if name == "hub_plugin" else None)
    return qp, hub


@pytest.mark.parametrize(
    "state, reported",
    [
        ({}, {"failed": False}),  # the model's last response needed no follow-up
        ({"error": "503 from the provider"}, {"failed": True}),
        ({"completed": False, "cancelled": True}, {"failed": False}),  # ESC
        ({"completed": False}, None),  # a tool result still has to go back to the model
        ({"queued": 1}, None),  # yielded to a queued message: its chain reports
    ],
)
def test_the_queue_processor_reports_a_chain_only_when_nothing_is_left(state, reported):
    qp, hub = _processor(**state)
    qp.note_chain_end()
    if reported is None:
        hub.network_chain_ended.assert_not_called()
    else:
        hub.network_chain_ended.assert_called_once_with(**reported)


@pytest.mark.asyncio
async def test_the_queue_processor_does_not_report_while_another_turn_is_mid_flight():
    qp, hub = _processor()
    await qp._turn_lock.acquire()  # a model call is in progress
    qp.note_chain_end()
    hub.network_chain_ended.assert_not_called()
    qp._turn_lock.release()
    qp.note_chain_end()
    hub.network_chain_ended.assert_called_once_with(failed=False)
