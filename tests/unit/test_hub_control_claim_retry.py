"""Parsed claim evidence cannot turn an identical durable retry into a conflict."""

import pytest

from plugins.hub import messenger
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage


@pytest.mark.asyncio
@pytest.mark.parametrize("release_claim", [False, True])
async def test_parsed_control_retry_ignores_claim_evidence(
    tmp_path, monkeypatch, release_claim
):
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    message = HubMessage(
        id="retry", to="receiver", content="assignment",
        metadata={"task_assignment": True},
    )
    await AgentMessenger.send_to_file("receiver", message)
    assert AgentMessenger.mark_mailbox_delivery_uncertain(["receiver"], {message.id})
    parsed = AgentMessenger.read_mailbox("receiver", consume=False)[0]
    assert parsed.metadata["_mailbox_delivery_uncertain"] is True
    if release_claim:
        AgentMessenger.clear_mailbox_delivery_uncertain(["receiver"], {message.id})
    assert await AgentMessenger.send_to_file("receiver", parsed) is True
    assert len(list((tmp_path / "receiver").glob("*.json"))) == 1
