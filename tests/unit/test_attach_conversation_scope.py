"""An attach client only ever sees the conversation that is live (#525).

The display tap keeps one ring for the whole daemon. After /resume or a new
session the ring still held the old conversation's user text, goal status,
tokens, errors and completions, and the Hub sent all of it to the next window
that attached, and to windows whose queue already held it.
"""

import asyncio
import json
import os
import secrets
from pathlib import Path

import pytest

from kollabor_tui.display_tap import DisplayTap, publish_semantic
from plugins.hub.messenger import AgentSocketServer


class Conversations:
    """Stands in for the conversation manager: `uid` is what a switch changes."""

    def __init__(self, uid="conv-a"):
        self.uid = uid

    def __call__(self):
        return self.uid


@pytest.fixture
def socket_dir(monkeypatch):
    d = Path("/tmp") / f"kollab-scope-test-{secrets.token_hex(4)}"
    d.mkdir(mode=0o700)
    monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: d)
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def _tap(conversation):
    tap = DisplayTap(history_size=50)
    tap.conversation_source = conversation
    return tap


def _server(tap):
    server = AgentSocketServer(
        agent_id="scope-test",
        on_message=None,
        socket_name="scope-" + os.urandom(4).hex(),
    )
    server._display_tap = tap
    return server


def _publish_turn(tap, label):
    publish_semantic(tap, "user_message", role="user", text=f"{label} private text")
    publish_semantic(tap, "goal.state_changed", objective=f"{label} objective")
    publish_semantic(tap, "token", text=f"{label} token")
    publish_semantic(tap, "error", error=f"{label} error")
    publish_semantic(tap, "turn_complete", request_id=f"{label}-req")


async def _attach(path, client_id):
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(
        (json.dumps({"action": "attach", "mode": "readonly", "client_id": client_id}) + "\n").encode()
    )
    await writer.drain()
    return reader, writer


async def _read_until_heartbeat(reader, timeout=12):
    events = []
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout)
        msg = json.loads(line.decode())
        if msg.get("type") == "heartbeat":
            return events
        if msg.get("type") != "attach_ack":
            events.append(msg)


def _texts(events):
    return [json.dumps(e) for e in events]


@pytest.mark.asyncio
async def test_reattach_after_switch_replays_only_the_live_conversation(socket_dir):
    conversation = Conversations("conv-a")
    tap = _tap(conversation)
    _publish_turn(tap, "A")
    conversation.uid = "conv-b"
    _publish_turn(tap, "B")

    server = _server(tap)
    path = await server.start()
    reader, writer = await _attach(path, "late")
    try:
        events = await _read_until_heartbeat(reader)
    finally:
        writer.close()
        await server.stop()

    blob = " ".join(_texts(events))
    assert "A private text" not in blob and "A objective" not in blob
    assert "A token" not in blob and "A error" not in blob and "A-req" not in blob
    assert [e["type"] for e in events] == [
        "user_message", "goal.state_changed", "token", "error", "turn_complete",
    ]
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


@pytest.mark.asyncio
async def test_reconnect_inside_the_same_conversation_keeps_its_events(socket_dir):
    conversation = Conversations("conv-a")
    tap = _tap(conversation)
    _publish_turn(tap, "A")

    server = _server(tap)
    path = await server.start()
    try:
        for client_id in ("first", "second"):
            reader, writer = await _attach(path, client_id)
            events = await _read_until_heartbeat(reader)
            writer.close()
            assert [e["type"] for e in events] == [
                "user_message", "goal.state_changed", "token", "error", "turn_complete",
            ]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_events_queued_before_a_switch_are_not_delivered_to_either_window(
    socket_dir, monkeypatch
):
    """A's events sit in both live windows' queues when the switch happens."""
    conversation = Conversations("conv-a")
    tap = _tap(conversation)
    gate = asyncio.Event()
    real_to_thread = asyncio.to_thread

    async def gated(func, *args, **kwargs):
        if getattr(func, "__name__", "") == "_get_event":
            await gate.wait()
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("plugins.hub.messenger.asyncio.to_thread", gated)

    server = _server(tap)
    path = await server.start()
    windows = [await _attach(path, "one"), await _attach(path, "two")]
    try:
        for _ in range(100):
            if tap.subscriber_count == 2:
                break
            await asyncio.sleep(0.05)
        assert tap.subscriber_count == 2
        await asyncio.sleep(0.2)  # both streams are parked on the gate

        _publish_turn(tap, "A")  # queued, undelivered
        conversation.uid = "conv-b"  # /resume or a new session
        _publish_turn(tap, "B")
        gate.set()

        for reader, writer in windows:
            events = await _read_until_heartbeat(reader)
            blob = " ".join(_texts(events))
            assert "A private text" not in blob and "A objective" not in blob
            assert "A token" not in blob and "A error" not in blob and "A-req" not in blob
            assert "B private text" in blob and "B-req" in blob
    finally:
        for _, writer in windows:
            writer.close()
        await server.stop()


def test_events_without_a_conversation_are_not_assumed_to_be_the_live_one():
    conversation = Conversations("conv-a")
    tap = DisplayTap(history_size=50)
    tap.publish({"type": "user_message", "text": "from before identity existed"})
    tap.conversation_source = conversation
    tap.publish({"type": "user_message", "text": "now"})

    assert [e["text"] for e in tap.get_snapshot()] == ["now"]


def test_an_event_that_names_its_conversation_keeps_it():
    conversation = Conversations("conv-b")
    tap = _tap(conversation)
    publish_semantic(tap, "goal.state_changed", conversation_uid="conv-a", objective="A goal")
    publish_semantic(tap, "goal.state_changed", objective="B goal")

    assert [e["objective"] for e in tap.get_snapshot()] == ["B goal"]
    conversation.uid = "conv-a"
    assert [e["objective"] for e in tap.get_snapshot()] == ["A goal"]
