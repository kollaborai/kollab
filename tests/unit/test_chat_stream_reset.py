"""A successful /clear reset must reach every already connected chat client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.state.local import LocalStateService
from kollabor_events import EventBus
from kollabor_events.data_models import ConversationMessage
from kollabor_tui.display_tap import DisplayTap


class _ConversationManager:
    def __init__(self) -> None:
        self.conversation_metadata = {"conversation_uid": "conversation-before"}
        self.current_session_id = "before"
        self.messages = []

    @property
    def conversation_uid(self) -> str:
        return self.conversation_metadata["conversation_uid"]

    def clear_conversation(self) -> None:
        self.conversation_metadata = {"conversation_uid": "conversation-after"}
        self.current_session_id = "after"
        self.messages = []


class _AcpProfile:
    name = "test-acp"
    provider = "acp"

    def get_provider(self) -> str:
        return self.provider


class _ProfileManager:
    def get_active_profile(self) -> _AcpProfile:
        return _AcpProfile()


class _AcpOrchestrator:
    active = False
    _selected_profile = None

    async def start(self, _profile, *, resume_conversation=None) -> None:
        assert resume_conversation is None
        self.active = True


@pytest.mark.asyncio
@pytest.mark.parametrize("uses_acp", [False, True], ids=["native", "acp"])
async def test_successful_reset_notifies_every_connected_client(uses_acp: bool) -> None:
    manager = _ConversationManager()
    llm = SimpleNamespace(
        conversation_manager=manager,
        conversation_history=[
            ConversationMessage(role="user", content="old private transcript")
        ],
    )

    async def initialize() -> None:
        llm.conversation_history.clear()

    llm._initialize_conversation = initialize
    event_bus = EventBus()
    tap = DisplayTap()
    event_bus.register_service("display_tap", tap)
    profile_manager = _ProfileManager() if uses_acp else None
    if uses_acp:
        event_bus.register_service("acp_orchestrator", _AcpOrchestrator())

    state = LocalStateService(llm, profile_manager, event_bus=event_bus)
    tap.conversation_source = state.get_conversation_uid
    state.get_conversation = AsyncMock(return_value=SimpleNamespace())
    stream = state.chat_stream

    # Each open browser window has its own event queue and cursor for the old
    # conversation. Both must see the conversation identity change live.
    client_queues = [tap.subscribe("client-one"), tap.subscribe("client-two")]
    old_snapshot = stream.subscribe({"after": None})["events"][0]
    assert old_snapshot["conversation_uid"] == "conversation-before"
    assert any(
        part.get("text") == "old private transcript"
        for row in old_snapshot["data"]["rows"]
        for part in row["parts"]
    )

    await state.restart_session()

    assert state.get_conversation_uid() == "conversation-after"
    for queue in client_queues:
        frame = queue.get_nowait()
        assert frame["type"] == "chat_stream"
        event = frame["event"]
        assert event["conversation_uid"] == "conversation-after"

        # The engine reacts to a new UID by requesting that conversation's
        # snapshot. It must not keep rendering rows from the old transcript.
        refreshed = stream.subscribe(
            {
                "after": None,
                "conversation_uid": event["conversation_uid"],
                "generation": event["generation"],
            }
        )["events"][0]
        assert refreshed["type"] == "snapshot"
        assert refreshed["conversation_uid"] == "conversation-after"
        assert all(
            part.get("text") != "old private transcript"
            for row in refreshed["data"]["rows"]
            for part in row["parts"]
        )
