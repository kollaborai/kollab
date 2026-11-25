"""Only classified ordinary records may be discarded without acknowledgment."""

import builtins
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub import messenger
from plugins.hub.messenger import AgentMessenger, InboxCapacityError
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


def _control(message_id: str) -> HubMessage:
    return HubMessage(
        id=message_id,
        from_identity="coordinator",
        to="receiver",
        content=message_id,
        metadata={"task_assignment": True},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("read_many", [False, True])
async def test_consuming_read_cannot_release_a_control_admission_slot(
    tmp_path, monkeypatch, read_many
):
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    await AgentMessenger.send_to_file("receiver", _control("accepted"))
    if read_many:
        batch = AgentMessenger.read_mailboxes(["receiver"], consume=True)
    else:
        batch = AgentMessenger.read_mailbox("receiver", consume=True)
    assert [message.id for message in batch] == ["accepted"]
    with pytest.raises(InboxCapacityError):
        await AgentMessenger.send_to_file("receiver", _control("concurrent"))

    receiver = HubPlugin.__new__(HubPlugin)
    receiver._identity = SimpleNamespace(agent_id="session", identity="receiver")
    receiver._on_message_received = AsyncMock(return_value=False)
    assert await receiver._deliver_inbox_batch(batch) == set()
    assert len(list((tmp_path / "receiver").glob("*.json"))) == 1
    receiver._on_message_received = AsyncMock(return_value=True)
    assert await receiver._deliver_inbox_batch(batch) == {"accepted"}
    await AgentMessenger.send_to_file("receiver", _control("concurrent"))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "prune", "read-then-prune"])
@pytest.mark.parametrize(
    "damage",
    ["unreadable", "{", "[]", '"scalar"', "null", "{}", "timestamp", "metadata", "id"],
)
async def test_unclassified_record_keeps_its_bytes_and_capacity(
    tmp_path, monkeypatch, operation, damage
):
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    monkeypatch.setattr(messenger, "INBOX_MAX_SIZE", 1)
    original = _control("accepted")
    await AgentMessenger.send_to_file("receiver", original)
    record = next((tmp_path / "receiver").glob("*.json"))
    payload = original.to_dict()
    if damage == "timestamp":
        payload["timestamp"] = "invalid"
    elif damage == "metadata":
        payload["metadata"] = []
    elif damage == "id":
        payload.pop("id")
    if damage in {"timestamp", "metadata", "id"}:
        record.write_text(json.dumps(payload))
    elif damage != "unreadable":
        record.write_text(damage)
    damaged_bytes = record.read_bytes()
    real_open = builtins.open

    def deny_record(file, *args, **kwargs):
        if Path(file) == record:
            raise PermissionError("simulated storage failure")
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as failure:
        if damage == "unreadable":
            failure.setattr(builtins, "open", deny_record)
        if operation in {"read", "read-then-prune"}:
            assert AgentMessenger.read_mailbox("receiver", consume=False) == []
        if operation in {"prune", "read-then-prune"}:
            for index in range(3):
                await AgentMessenger.send_to_file(
                    "receiver", HubMessage(content=f"ordinary {index}")
                )
            assert len(list((tmp_path / "receiver").glob("*.json"))) == 2
        assert record.exists(), "unknown record was discarded without classification"
        with pytest.raises(InboxCapacityError):
            await AgentMessenger.send_to_file("receiver", _control("rejected"))
    assert record.read_bytes() == damaged_bytes

    # Restoring the isolated record makes it classifiable again. A real receive
    # and acknowledgment, rather than a read, releases the admission slot.
    record.write_text(json.dumps(original.to_dict()))
    receiver = HubPlugin.__new__(HubPlugin)
    receiver._identity = SimpleNamespace(agent_id="session", identity="receiver")
    receiver._on_message_received = AsyncMock(return_value=True)
    batch = AgentMessenger.read_mailboxes(["receiver"], consume=False)
    assert "accepted" in await receiver._deliver_inbox_batch(batch)
    await AgentMessenger.send_to_file("receiver", _control("rejected"))


@pytest.mark.asyncio
@pytest.mark.parametrize("release_claim", [False, True])
async def test_offline_retry_is_idempotent_across_mailbox_claim_transitions(
    tmp_path, monkeypatch, release_claim
):
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    sender = HubPlugin(event_bus=MagicMock())
    sender._identity = AgentRuntime(
        name="coordinator", identity="sender", agent_id="sender-session",
        is_coordinator=True,
    )
    sender._presence = MagicMock()
    sender._presence.discover_agents_async = AsyncMock(return_value=[])
    message = _control("retry")
    message.to = "koordinator"
    assert await sender._route_message(message) == []
    assert message.metadata["_queued_for"] == ["koordinator"]
    assert AgentMessenger.mark_mailbox_delivery_uncertain(["koordinator"], {message.id})
    if release_claim:
        AgentMessenger.clear_mailbox_delivery_uncertain(["koordinator"], {message.id})
    assert await sender._route_message(message) == []
    assert len(list((tmp_path / "koordinator").glob("*.json"))) == 1

    conflicting = HubMessage.from_dict(message.to_dict())
    conflicting.content = "different assignment"
    rejections = await sender._route_message(conflicting)
    assert rejections == [
        ("koordinator", "durable message ID already exists with different content")
    ]
