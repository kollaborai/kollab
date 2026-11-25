"""Route rejection remains visible when durable quarantine storage is full."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.hub import messenger
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


def _control(message_id: str) -> HubMessage:
    return HubMessage(
        id=message_id,
        action="message",
        from_identity="source",
        to="receiver",
        content=message_id,
        metadata={"task_assignment": True},
    )


@pytest.mark.asyncio
async def test_full_quarantine_mailbox_returns_route_rejection(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity="receiver", agent_id="receiver-id")
    plugin._presence = MagicMock()
    plugin._presence.discover_agents_async = AsyncMock(return_value=[])
    plugin._vault = None
    plugin._trace_delivery = MagicMock()
    plugin._decide_sender_delivery = lambda _message: SimpleNamespace(
        mode="quarantine", reason="sender is not trusted", trace_level="error"
    )
    await AgentMessenger.send_to_file("receiver.quarantine", _control("existing"))

    result = await plugin._route_message(_control("incoming"))

    assert result == [
        (
            "mesh",
            "sender is not trusted; quarantine mailbox capacity reached",
        )
    ]
    stored = AgentMessenger.read_mailbox("receiver.quarantine", restart_boundary=0)
    assert [message.id for message in stored] == ["existing"]
