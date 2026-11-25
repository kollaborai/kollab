"""Durable controls stay admitted while receive classification is pending."""

from types import SimpleNamespace

import pytest

from plugins.hub import messenger
from plugins.hub.messenger import AgentMessenger, InboxCapacityError
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


def _control(message_id: str) -> HubMessage:
    return HubMessage(
        id=message_id,
        action="message",
        from_identity="coordinator",
        to="receiver",
        content=message_id,
        metadata={"task_assignment": True},
    )


@pytest.mark.asyncio
async def test_refused_control_keeps_capacity_until_receive_classifies_it(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    await AgentMessenger.send_to_file("receiver", _control("accepted"))

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(agent_id="receiver-id", identity="receiver")
    concurrent_admission = []

    async def refuse_after_concurrent_admission(_message: HubMessage) -> bool:
        try:
            await AgentMessenger.send_to_file("receiver", _control("concurrent"))
        except InboxCapacityError:
            concurrent_admission.append(False)
        else:
            concurrent_admission.append(True)
        return False

    plugin._on_message_received = refuse_after_concurrent_admission
    replay = AgentMessenger.read_mailboxes(["receiver-id", "receiver"], consume=False)
    assert [message.id for message in replay] == ["accepted"]
    try:
        await plugin._deliver_inbox_batch(replay)
    except InboxCapacityError:
        pass

    remaining = AgentMessenger.read_mailbox("receiver", restart_boundary=0)
    assert [message.id for message in remaining] == ["accepted"]
    assert concurrent_admission == [False]


@pytest.mark.asyncio
async def test_accepted_control_is_removed_only_after_receive_classification(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    await AgentMessenger.send_to_file("receiver", _control("accepted"))

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(agent_id="receiver-id", identity="receiver")
    files_during_receive = []

    async def accept(_message: HubMessage) -> None:
        files_during_receive.append(len(list((tmp_path / "receiver").glob("*.json"))))

    plugin._on_message_received = accept
    replay = AgentMessenger.read_mailboxes(["receiver-id", "receiver"], consume=False)
    await plugin._deliver_inbox_batch(replay)

    assert files_during_receive == [1]
    assert list((tmp_path / "receiver").glob("*.json")) == []
