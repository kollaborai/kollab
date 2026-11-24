"""A durable claim, rather than callback exit timing, owns mailbox delivery (#935)."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_config import config_utils
from plugins.hub import messenger
from plugins.hub import plugin as hub_plugin
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    return tmp_path


def _receiver(callback):
    receiver = HubPlugin.__new__(HubPlugin)
    receiver._identity = SimpleNamespace(agent_id="session", identity="receiver")
    receiver._on_message_received = callback
    return receiver


def _queued():
    return AgentMessenger.read_mailboxes(["session", "receiver"], consume=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_effect", [False, True])
async def test_cancelled_receiver_cannot_be_reentered_from_stale_batch(
    inbox, after_effect
):
    message = HubMessage(action="message", to="receiver", content="retained")
    calls = []
    effects = []

    async def receive(msg):
        calls.append(msg.id)
        if after_effect:
            effects.append(msg.id)
        raise asyncio.CancelledError

    receiver = _receiver(receive)
    await AgentMessenger.send_to_file("receiver", message)
    stale = _queued()
    with pytest.raises(asyncio.CancelledError):
        await receiver._deliver_inbox_batch(stale)

    # Simulate a second owner/restart holding a previously parsed batch. It
    # must consult durable state, not the metadata in that in-memory snapshot.
    restarted = _receiver(receive)
    try:
        await restarted._deliver_inbox_batch(stale)
    except asyncio.CancelledError:
        pass

    assert calls == [message.id]
    assert effects == ([message.id] if after_effect else [])
    held = _queued()
    assert [msg.id for msg in held] == [message.id]
    assert held[0].metadata["_mailbox_delivery_uncertain"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("after_effect", [False, True])
async def test_cancelled_coalesced_replay_retains_evidence(inbox, after_effect):
    message = HubMessage(action="message", to="receiver", content="retained")
    effects = []

    async def inject(block, subtype):
        if after_effect:
            effects.append(block)
        raise asyncio.CancelledError

    receiver = _receiver(None)
    receiver.event_bus = SimpleNamespace(
        get_service=lambda _name: SimpleNamespace(inject_system_message=inject)
    )
    await AgentMessenger.send_to_file("receiver", message)
    summary = HubMessage(action="inbox_summary", content="offline")
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await receiver._deliver_inbox_batch([summary, *_queued()])

    assert getattr(cancelled.value, "acknowledged_message_ids", set()) == set()
    held = _queued()
    assert [msg.id for msg in held] == [message.id]
    assert held[0].metadata["_mailbox_delivery_uncertain"] is True
    assert len(effects) == int(after_effect)


@pytest.mark.asyncio
async def test_one_owner_during_real_task_cancellation(inbox):
    message = HubMessage(action="message", to="receiver", content="one effect")
    effects = []
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def receive(msg):
        effects.append(msg.id)
        entered.set()
        await finish.wait()

    await AgentMessenger.send_to_file("receiver", message)
    stale = _queued()
    task = asyncio.create_task(_receiver(receive)._deliver_inbox_batch(stale))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()

        async def competing_receiver(msg):
            effects.append(msg.id)

        await _receiver(competing_receiver)._deliver_inbox_batch(stale)
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert effects == [message.id]
    assert _queued() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("coalesced", [False, True])
async def test_missing_source_is_not_permission_to_deliver(inbox, coalesced):
    effects = []

    async def receive(msg):
        effects.append(msg.id)

    async def inject(block, subtype):
        effects.append(block)

    receiver = _receiver(receive)
    receiver.event_bus = SimpleNamespace(
        get_service=lambda _name: SimpleNamespace(inject_system_message=inject)
    )
    message = HubMessage(action="message", to="receiver", content="absent")
    batch = [message]
    if coalesced:
        batch.insert(0, HubMessage(action="inbox_summary", content="offline"))
    assert await receiver._deliver_inbox_batch(batch) == set()
    assert effects == []


@pytest.mark.asyncio
async def test_partial_coalesced_source_cannot_authorize_whole_block(inbox):
    effects = []

    async def inject(block, subtype):
        effects.append(block)

    receiver = _receiver(None)
    receiver.event_bus = SimpleNamespace(
        get_service=lambda _name: SimpleNamespace(inject_system_message=inject)
    )
    present = HubMessage(action="message", to="receiver", content="present")
    absent = HubMessage(action="message", to="receiver", content="absent")
    await AgentMessenger.send_to_file("receiver", present)
    summary = HubMessage(action="inbox_summary", content="offline")
    assert await receiver._deliver_inbox_batch([summary, present, absent]) == set()
    assert effects == []
    assert [msg.id for msg in _queued()] == [present.id]


@pytest.mark.asyncio
async def test_failed_ack_write_preserves_claimed_payload(inbox, monkeypatch):
    message = HubMessage(action="message", to="receiver", content="retain evidence")
    effects = []

    async def receive(msg):
        effects.append(msg.id)

    await AgentMessenger.send_to_file("receiver", message)
    record = next((inbox / "receiver").glob("*.json"))
    original_unlink = Path.unlink
    original_dump = messenger.json.dump

    def fail_unlink(path, *args, **kwargs):
        if path == record:
            raise PermissionError("ack unlink denied")
        return original_unlink(path, *args, **kwargs)

    def fail_receipt(data, stream, *args, **kwargs):
        if data.get("_mailbox_acknowledged") is True:
            stream.write('{"partial":')
            raise OSError("receipt write interrupted")
        return original_dump(data, stream, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    monkeypatch.setattr(messenger.json, "dump", fail_receipt)
    receiver = _receiver(receive)
    assert await receiver._deliver_inbox_batch(_queued()) == {message.id}
    data = json.loads(record.read_text())
    assert data["content"] == message.content
    assert data["_mailbox_delivery_uncertain"] is True
    assert await _receiver(receive)._deliver_inbox_batch(_queued()) == set()
    assert effects == [message.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("after_effect", [False, True])
async def test_ack_replace_failure_cannot_reopen_cancelled_delivery(
    inbox, monkeypatch, after_effect
):
    message = HubMessage(action="message", to="receiver", content="retained")
    calls = []

    async def receive(msg):
        calls.append(msg.id)
        if after_effect:
            # Any later persistence failure must leave the pre-effect claim.
            monkeypatch.setattr(
                messenger.os, "replace",
                lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
            )
        raise asyncio.CancelledError

    await AgentMessenger.send_to_file("receiver", message)
    stale = _queued()
    with pytest.raises(asyncio.CancelledError):
        await _receiver(receive)._deliver_inbox_batch(stale)
    try:
        await _receiver(receive)._deliver_inbox_batch(stale)
    except asyncio.CancelledError:
        pass
    assert calls == [message.id]
    assert _queued()[0].metadata["_mailbox_delivery_uncertain"] is True


@pytest.mark.parametrize("arrival", ["durable", "malformed", "ordinary"])
def test_orphan_cleanup_has_no_recursive_delete_capability(
    tmp_path, monkeypatch, arrival
):
    inbox = tmp_path / "projects" / "p" / "hub" / "messages" / "receiver"
    inbox.mkdir(parents=True)
    (inbox / "old.json").write_text(json.dumps({"timestamp": 1.0}))
    arrived = inbox / "new.json"
    validate = messenger._validated_mailbox_timestamp

    def arrive_during_scan(data):
        payload = {"timestamp": "invalid"}
        if arrival == "durable":
            payload = {"timestamp": 1.0, "metadata": {"task_assignment": True}}
        elif arrival == "ordinary":
            payload = {"timestamp": messenger.time.time()}
        arrived.write_text(json.dumps(payload))
        return validate(data)

    monkeypatch.setattr(messenger, "_validated_mailbox_timestamp", arrive_during_scan)
    monkeypatch.setattr(config_utils, "get_config_directory", lambda: tmp_path)
    passes = []

    async def one_pass(_seconds):
        passes.append(True)
        if len(passes) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        hub_plugin, "asyncio",
        SimpleNamespace(sleep=one_pass, CancelledError=asyncio.CancelledError),
    )
    asyncio.run(HubPlugin._presence_sweep_loop(SimpleNamespace(_task_ledger=None)))
    # A populated orphan is retained. If a scan occurs, its concurrent arrival
    # must also survive; cleanup may only remove a directory proven empty by rmdir.
    assert inbox.is_dir()
    assert (inbox / "old.json").exists()
    if arrived.exists():
        assert json.loads(arrived.read_text())
