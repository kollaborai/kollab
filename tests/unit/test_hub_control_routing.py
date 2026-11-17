"""Sender-facing Hub results when a durable control cannot be queued."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub import messenger
from plugins.hub.messenger import AgentMessenger, InboxCapacityError
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (
            InboxCapacityError(
                "durable inbox capacity reached; retry after the receiver drains controls"
            ),
            "durable inbox capacity reached; retry after the receiver drains controls",
        ),
        (OSError("disk full"), "durable mailbox write failed (OSError)"),
    ],
)
async def test_offline_control_failure_is_a_route_rejection(
    error: Exception, expected_reason: str
) -> None:
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="sapphire",
        agent_id="sapphire-id",
        is_coordinator=True,
    )
    plugin._presence = MagicMock()
    plugin._presence.discover_agents_async = AsyncMock(return_value=[])
    message = HubMessage(
        action="message",
        from_identity="sapphire",
        to="koordinator",
        content="assign task",
        metadata={"task_assignment": True},
    )
    with patch(
        "plugins.hub.plugin.AgentMessenger.send_to_file",
        new=AsyncMock(side_effect=error),
    ):
        rejections = await plugin._route_message(message)

    assert rejections == [("koordinator", expected_reason)]
    assert "_queued_for" not in message.metadata


@pytest.mark.asyncio
async def test_mailbox_writer_reports_success_only_after_durable_admission(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    message = HubMessage(
        action="message",
        from_identity="sapphire",
        to="receiver",
        content="assign task",
        metadata={"task_assignment": True},
    )

    admitted = await AgentMessenger.send_to_file("receiver", message)

    assert admitted is True
    assert len(list((tmp_path / "receiver").glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_offline_control_retry_ignores_route_delivery_metadata(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="sapphire",
        agent_id="sapphire-id",
        is_coordinator=True,
    )
    plugin._presence = MagicMock()
    plugin._presence.discover_agents_async = AsyncMock(return_value=[])
    message = HubMessage(
        id="offline-retry",
        action="message",
        from_identity="sapphire",
        to="koordinator",
        content="assign task",
        metadata={"task_assignment": True},
    )

    assert await plugin._route_message(message) == []
    assert message.metadata["_queued_for"] == ["koordinator"]
    assert await plugin._route_message(message) == []
    assert len(list((tmp_path / "koordinator").glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_offline_control_id_conflict_is_a_route_rejection(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="sapphire",
        agent_id="sapphire-id",
        is_coordinator=True,
    )
    plugin._presence = MagicMock()
    plugin._presence.discover_agents_async = AsyncMock(return_value=[])
    original = HubMessage(
        id="conflicting-retry",
        action="message",
        from_identity="sapphire",
        to="koordinator",
        content="first assignment",
        metadata={"task_assignment": True},
    )
    conflict = HubMessage(
        id=original.id,
        action=original.action,
        from_identity=original.from_identity,
        to=original.to,
        content="different assignment",
        metadata={"task_assignment": True},
    )
    await AgentMessenger.send_to_file("koordinator", original)

    assert await plugin._route_message(conflict) == [
        (
            "koordinator",
            "durable message ID already exists with different content",
        )
    ]
