"""Resume and branch session changes reach existing chat stream clients."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kollabor.state.local import LocalStateService
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events import EventBus
from kollabor_events.data_models import ConversationMessage
from kollabor_tui.display_tap import DisplayTap


def _manager_config(conversations_dir):
    return SimpleNamespace(
        _conversations_dir=conversations_dir,
        get=lambda _key, default=None: default,
    )


def _state_with_saved_session(tmp_path):
    logger = KollaborConversationLogger(tmp_path)
    logger.reset_session("current-before")
    manager = ConversationManager(_manager_config(tmp_path), logger)
    saved_at = datetime(2026, 11, 26, tzinfo=timezone.utc).isoformat()
    saved_message = {
        "uuid": "saved-message",
        "role": "user",
        "content": "saved session transcript",
        "timestamp": saved_at,
        "parent_uuid": None,
        "metadata": {"request_id": "saved-session-input"},
        "session_id": "saved-session",
    }
    manager.current_session_id = "saved-session"
    manager.conversation_metadata = {
        "conversation_uid": "conversation-saved",
        "started_at": saved_at,
        "message_count": 1,
        "turn_count": 1,
        "topics": [],
        "model_used": None,
    }
    manager.messages = [saved_message]
    manager.message_index = {saved_message["uuid"]: saved_message}
    manager.context_window = [saved_message]
    assert manager.save_session("saved-session")

    old_message = {
        **saved_message,
        "uuid": "old-message",
        "content": "old session transcript",
        "metadata": {"request_id": "old-session-input"},
        "session_id": "current-before",
    }
    manager.current_session_id = "current-before"
    manager.conversation_metadata = {
        "conversation_uid": "conversation-before",
        "started_at": saved_at,
        "message_count": 1,
        "turn_count": 1,
        "topics": [],
        "model_used": None,
    }
    manager.messages = [old_message]
    manager.message_index = {old_message["uuid"]: old_message}
    manager.context_window = [old_message]
    logger.reset_session("current-before")
    llm = SimpleNamespace(
        conversation_manager=manager,
        conversation_logger=logger,
        conversation_history=[
            ConversationMessage(
                role="user",
                content="old session transcript",
                metadata={"request_id": "old-session-input"},
            )
        ],
        session_stats={"messages": 1},
    )
    bus = EventBus()
    tap = DisplayTap()
    bus.register_service("display_tap", tap)
    state = LocalStateService(llm, None, event_bus=bus)
    tap.conversation_source = state.get_conversation_uid
    return state, tap


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "branch"])
async def test_successful_session_switch_notifies_two_existing_clients(
    tmp_path, operation
):
    state, tap = _state_with_saved_session(tmp_path)
    queues = [tap.subscribe("client-one"), tap.subscribe("client-two")]
    old_uid = state.get_conversation_uid()

    if operation == "resume":
        result = await state.resume_conversation("saved-session")
    else:
        result = await state.branch_conversation("saved-session", 0)

    new_uid = state.get_conversation_uid()
    assert result["message_count"] == 1
    assert new_uid != old_uid
    for client_queue in queues:
        frame = client_queue.get_nowait()
        assert frame["type"] == "chat_stream"
        event = frame["event"]
        assert event["type"] == "replace"
        assert event["conversation_uid"] == new_uid
        texts = [
            part.get("text")
            for row in event["data"]["rows"]
            for part in row["parts"]
            if part.get("type") == "text"
        ]
        assert "saved session transcript" in texts
        assert "old session transcript" not in texts


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "branch"])
async def test_failed_session_switch_does_not_notify_clients(tmp_path, operation):
    state, tap = _state_with_saved_session(tmp_path)
    queues = [tap.subscribe("client-one"), tap.subscribe("client-two")]
    old_uid = state.get_conversation_uid()

    with pytest.raises(ValueError):
        if operation == "resume":
            await state.resume_conversation("missing-session")
        else:
            await state.branch_conversation("saved-session", 99)

    assert state.get_conversation_uid() == old_uid
    assert all(client_queue.empty() for client_queue in queues)
