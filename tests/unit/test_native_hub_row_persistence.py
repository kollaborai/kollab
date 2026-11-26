"""Native Hub rows survive daemon restart without entering model history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events.chat_stream import sequence_problems


def _hub_row(
    row_id,
    *,
    kind,
    created_at,
    turn_id=None,
    status="done",
    history_index=None,
):
    note = {
        "type": "hub-note",
        "from": "peer",
        "to": "koordinator",
        "text": f"structured message {row_id}",
        "observed": kind == "note",
    }
    row = {
        "id": row_id,
        "seq": 0,
        "kind": kind,
        "role": "system" if kind == "note" else "user",
        "turn_id": turn_id,
        "status": status,
        "parts": [note],
        "created_at": created_at,
    }
    if history_index is not None:
        row["_history_index"] = history_index
    return row


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


def test_branch_session_filters_native_hub_rows_after_the_branch_point(tmp_path):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.save_conversations = False
    manager.conversation_metadata["conversation_uid"] = "branch-source"
    manager.add_message("user", "first message")
    manager.add_message("assistant", "later message")
    manager.record_hub_chat_stream_row(
        _hub_row(
            "hub-before-branch-point",
            kind="note",
            created_at="2026-11-25T12:00:00Z",
            history_index=0,
        )
    )
    manager.record_hub_chat_stream_row(
        _hub_row(
            "hub-after-branch-point",
            kind="note",
            created_at="2026-11-25T12:00:02Z",
            history_index=1,
        )
    )
    assert manager.save_session(logger.session_id)

    result = manager.branch_session(logger.session_id, 0)

    assert result["success"] is True
    assert [row["id"] for row in manager.chat_stream_rows] == [
        "hub-before-branch-point"
    ]


def test_compaction_replacement_drops_old_persisted_hub_rows():
    before = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    compaction_time = before + timedelta(seconds=2)
    after = before + timedelta(seconds=4)
    manager = SimpleNamespace(
        messages=[],
        chat_stream_rows=[
            _hub_row(
                "old-persisted-hub-note",
                kind="note",
                created_at=before.isoformat().replace("+00:00", "Z"),
                history_index=0,
            ),
            _hub_row(
                "new-persisted-hub-note",
                kind="note",
                created_at=after.isoformat().replace("+00:00", "Z"),
                history_index=1,
            ),
        ],
    )
    llm = SimpleNamespace(
        conversation_manager=manager,
        conversation_history=[
            SimpleNamespace(
                role="user",
                content="compaction summary",
                timestamp=compaction_time,
                metadata={"context_compaction": True},
            ),
            SimpleNamespace(
                role="assistant",
                content="retained reply",
                timestamp=after + timedelta(seconds=1),
                metadata={"reply_id": "retained-reply"},
            ),
        ],
    )
    state = SimpleNamespace(
        _llm_service=llm,
        get_conversation_uid=lambda: "compaction-live",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )
    stream = ChatStreamService(state)

    replacement = stream.replace_transcript()

    replacement_ids = [row["id"] for row in replacement["data"]["rows"]]
    assert "old-persisted-hub-note" not in replacement_ids
    assert "new-persisted-hub-note" in replacement_ids
    assert [row["id"] for row in manager.chat_stream_rows] == [
        "new-persisted-hub-note"
    ]


def test_compacted_hub_rows_stay_pruned_after_restart(tmp_path):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.conversation_metadata["conversation_uid"] = "compacted-hub-session"
    old_row = _hub_row(
        "old-hub-input",
        kind="input",
        created_at="2026-11-25T12:00:00Z",
        turn_id="old-turn",
        status="completed",
        history_index=0,
    )
    manager.chat_stream_rows = [old_row]
    assert manager.save_session(logger.session_id)

    session_file = logger.session_file
    with session_file.open("a") as session:
        session.write(
            '{"type":"context_compaction","uuid":"compact-1",'
            '"timestamp":"2026-11-25T12:00:02Z","content":"summary"}\n'
        )
        session.write(
            '{"type":"system","subtype":"hub_incoming",'
            '"uuid":"hub-row-record-2","timestamp":"2026-11-25T12:00:04Z",'
            '"chat_stream_row":'
            + json.dumps(
                _hub_row(
                    "new-hub-note",
                    kind="note",
                    created_at="2026-11-25T12:00:04Z",
                    history_index=1,
                )
            )
            + "}\n"
        )

    resumed_manager = ConversationManager(config)
    assert resumed_manager.load_session(logger.session_id)
    assert [row["id"] for row in resumed_manager.chat_stream_rows] == [
        "new-hub-note"
    ]

    state = SimpleNamespace(
        _llm_service=SimpleNamespace(
            conversation_manager=resumed_manager,
            conversation_history=[],
        ),
        get_conversation_uid=lambda: resumed_manager.conversation_metadata[
            "conversation_uid"
        ],
    )
    snapshot = ChatStreamService(state).subscribe(
        {"after": None, "conversation_uid": None}
    )["events"][0]
    assert all("_history_index" not in row for row in snapshot["data"]["rows"])
    assert [row["id"] for row in snapshot["data"]["rows"] if row["kind"] != "marker"] == [
        "new-hub-note"
    ]
