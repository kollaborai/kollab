"""Native Hub rows survive daemon restart without entering model history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events.chat_stream import sequence_problems


def _hub_row(row_id, *, kind, created_at, turn_id=None, status="done"):
    note = {
        "type": "hub-note",
        "from": "peer",
        "to": "koordinator",
        "text": f"structured message {row_id}",
        "observed": kind == "note",
    }
    return {
        "id": row_id,
        "seq": 0,
        "kind": kind,
        "role": "system" if kind == "note" else "user",
        "turn_id": turn_id,
        "status": status,
        "parts": [note],
        "created_at": created_at,
    }


def test_native_hub_rows_round_trip_and_page_without_becoming_model_messages(
    tmp_path, monkeypatch
):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.conversation_metadata["conversation_uid"] = "native-hub-conversation"
    assert manager.save_session(logger.session_id)

    beginning = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    rows = [
        _hub_row(
            "note-before-window",
            kind="note",
            created_at=(beginning).isoformat().replace("+00:00", "Z"),
        ),
        _hub_row(
            "input-before-window",
            kind="input",
            turn_id="native-turn-1",
            status="completed",
            created_at=(beginning + timedelta(seconds=1)).isoformat().replace(
                "+00:00", "Z"
            ),
        ),
        _hub_row(
            "note-in-window",
            kind="note",
            created_at=(beginning + timedelta(seconds=2)).isoformat().replace(
                "+00:00", "Z"
            ),
        ),
        _hub_row(
            "input-in-window",
            kind="input",
            turn_id="native-turn-2",
            status="completed",
            created_at=(beginning + timedelta(seconds=3)).isoformat().replace(
                "+00:00", "Z"
            ),
        ),
    ]

    import asyncio

    async def persist_rows():
        for row in rows:
            await logger.log_system_message(
                "hub row display record",
                parent_uuid=None,
                subtype="hub_incoming",
                chat_stream_row=row,
            )

    asyncio.run(persist_rows())

    resumed_manager = ConversationManager(config)
    assert resumed_manager.load_session(logger.session_id)
    assert resumed_manager.messages == []
    assert resumed_manager.context_window == []
    assert [row["id"] for row in resumed_manager.chat_stream_rows] == [
        row["id"] for row in rows
    ]

    llm = SimpleNamespace(
        conversation_manager=resumed_manager,
        conversation_history=[],
    )
    state = SimpleNamespace(
        _llm_service=llm,
        get_conversation_uid=lambda: resumed_manager.conversation_metadata[
            "conversation_uid"
        ],
    )
    monkeypatch.setattr("kollabor.state.chat_stream.REPLAY_WINDOW", 2)
    stream = ChatStreamService(state)
    snapshot = stream.subscribe(
        {"after": None, "conversation_uid": None}
    )["events"][0]

    assert sequence_problems([snapshot]) == []
    assert [row["id"] for row in snapshot["data"]["rows"]] == [
        "note-in-window",
        "input-in-window",
    ]
    assert snapshot["data"]["inputs"] == [
        {
            "id": "input-in-window",
            "state": "ended",
            "turn_id": "native-turn-2",
            "outcome": "completed",
        }
    ]
    assert snapshot["data"]["turns"] == [
        {
            "turn_id": "native-turn-2",
            "state": "ended",
            "input_ids": ["input-in-window"],
        }
    ]

    page = stream.row_pager().read(before=None, limit=1)
    assert [row["id"] for row in page["rows"]] == ["input-before-window"]
    assert page["older"] is not None
    older_page = stream.row_pager().read(before=page["older"], limit=1)
    assert [row["id"] for row in older_page["rows"]] == ["note-before-window"]


def test_interrupted_native_hub_input_ends_failed_after_restart(tmp_path):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.conversation_metadata["conversation_uid"] = "native-hub-conversation"
    assert manager.save_session(logger.session_id)

    row = _hub_row(
        "interrupted-input",
        kind="input",
        turn_id="native-turn-interrupted",
        status="started",
        created_at="2026-11-25T12:00:00Z",
    )

    import asyncio

    asyncio.run(
        logger.log_system_message(
            "hub row display record",
            parent_uuid=None,
            subtype="hub_incoming",
            chat_stream_row=row,
        )
    )

    resumed_manager = ConversationManager(config)
    assert resumed_manager.load_session(logger.session_id)
    assert resumed_manager.chat_stream_rows == [
        {**row, "status": "failed"}
    ]

    llm = SimpleNamespace(
        conversation_manager=resumed_manager,
        conversation_history=[],
    )
    state = SimpleNamespace(
        _llm_service=llm,
        get_conversation_uid=lambda: resumed_manager.conversation_metadata[
            "conversation_uid"
        ],
    )
    snapshot = ChatStreamService(state).subscribe(
        {"after": None, "conversation_uid": None}
    )["events"][0]
    assert snapshot["data"]["inputs"] == [
        {
            "id": "interrupted-input",
            "state": "ended",
            "turn_id": "native-turn-interrupted",
            "outcome": "failed",
        }
    ]
