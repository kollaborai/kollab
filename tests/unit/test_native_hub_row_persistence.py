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
    assert len(resumed_manager.chat_stream_rows) == 1
    restored = resumed_manager.chat_stream_rows[0]
    assert {
        key: value for key, value in restored.items() if not key.startswith("_history_")
    } == {**row, "status": "failed"}
    assert restored["_history_row_revision"] == 1

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


def test_branch_excludes_hub_row_recorded_after_selected_message(tmp_path):
    """The default row position is the boundary after the current message."""
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.save_conversations = False
    manager.conversation_metadata["conversation_uid"] = "branch-boundary-source"
    manager.add_message("user", "selected message")
    # A native row recorded now is after message 0. Its boundary is 1, so it
    # must not be part of a branch whose inclusive endpoint is message 0.
    manager.record_hub_chat_stream_row(
        _hub_row(
            "hub-after-selected-message",
            kind="note",
            created_at="2026-11-25T12:00:00Z",
        )
    )
    manager.add_message("assistant", "later message")
    assert manager.save_session(logger.session_id)

    result = manager.branch_session(logger.session_id, 0)

    assert result["success"] is True
    assert manager.chat_stream_rows == []


def test_native_hub_rows_recorded_after_compaction_use_live_history_coordinates(
    tmp_path,
):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    manager = ConversationManager(config)
    # The manager's model transcript can lag the coordinator's list while a
    # compaction has just swapped in the shorter history.
    manager.messages = [
        {"role": "user", "content": f"old message {index}", "metadata": {}}
        for index in range(8)
    ]
    live_history = [
        {
            "uuid": "compact-summary-uuid",
            "role": "user",
            "content": "compacted summary",
            "metadata": {
                "context_compaction": True,
                "compaction_id": "compact-live-current",
            },
        },
        {
            "uuid": "current-tail-uuid",
            "role": "assistant",
            "content": "current tail",
            "metadata": {},
        },
    ]

    row = manager.record_hub_chat_stream_row(
        _hub_row(
            "after-live-compaction",
            kind="note",
            created_at="2026-11-25T12:00:00Z",
        ),
        history=live_history,
    )

    assert row["_history_index"] == 2
    assert row["_history_compaction_id"] == "compact-live-current"

    manager.messages = live_history
    logger = KollaborConversationLogger(conversations)
    manager.conversation_logger = logger
    assert manager.save_session(logger.session_id)

    resumed = ConversationManager(config)
    assert resumed.load_session(logger.session_id)
    assert resumed.chat_stream_history_compaction_id == "compact-live-current"
    assert resumed.chat_stream_history_length == 2
    next_row = resumed.record_hub_chat_stream_row(
        _hub_row(
            "after-restart-compaction",
            kind="note",
            created_at="2026-11-25T12:00:01Z",
        )
    )
    assert next_row["_history_index"] == 2
    assert next_row["_history_compaction_id"] == "compact-live-current"


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


def test_legacy_compaction_stays_pruned_after_restart(tmp_path):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.conversation_metadata["conversation_uid"] = "legacy-compacted-hub-session"
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

    with logger.session_file.open("a") as session:
        session.write(
            '{"type":"context_compaction","uuid":"compact-legacy",'
            '"timestamp":"2026-11-25T12:00:02Z","content":"summary"}\n'
        )
        session.write(
            '{"type":"system","subtype":"hub_incoming",'
            '"uuid":"hub-row-record-legacy","timestamp":"2026-11-25T12:00:04Z",'
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


def test_late_hub_row_replay_preserves_legacy_compaction_timestamp_fallback(tmp_path):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.conversation_metadata["conversation_uid"] = "mixed-compaction-hub-session"
    assert manager.save_session(logger.session_id)

    with logger.session_file.open("a") as session:
        session.write(
            '{"type":"context_compaction","uuid":"compact-modern",'
            '"timestamp":"2026-11-25T12:00:02Z","content":"modern summary",'
            '"compaction_round":1,"compaction_snapshot_count":0,'
            '"compaction_retained_positions":[],"compaction_output_count":1,'
            '"compaction_id":"modern-compaction"}\n'
        )
        session.write(
            '{"type":"context_compaction","uuid":"compact-legacy",'
            '"timestamp":"2026-11-25T12:00:04Z","content":"legacy summary",'
            '"compaction_round":2}\n'
        )
        session.write(
            '{"type":"system","subtype":"hub_incoming",'
            '"uuid":"hub-row-record-after-legacy",'
            '"timestamp":"2026-11-25T12:00:05Z","chat_stream_row":'
            + json.dumps(
                _hub_row(
                    "stale-hub-note",
                    kind="note",
                    created_at="2026-11-25T12:00:03Z",
                )
            )
            + "}\n"
        )

    resumed_manager = ConversationManager(config)
    assert resumed_manager.load_session(logger.session_id)
    assert resumed_manager.chat_stream_rows == []


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
        created_at="2026-11-25T12:00:01Z",
        turn_id="old-turn",
        status="completed",
        history_index=4,
    )
    retained_row = _hub_row(
        "retained-prefix-note",
        kind="note",
        created_at="2026-11-25T12:00:01Z",
        history_index=19,
    )
    manager.chat_stream_rows = [old_row, retained_row]
    assert manager.save_session(logger.session_id)

    session_file = logger.session_file
    with session_file.open("a") as session:
        session.write(
            '{"type":"context_compaction","uuid":"compact-1",'
            '"timestamp":"2026-11-25T12:00:02Z","content":"summary",'
            '"compaction_round":1,"compaction_snapshot_count":20,'
            '"compaction_retained_positions":[0,14,15,16,17,18,19]}\n'
        )
        session.write(
            '{"type":"system","subtype":"hub_incoming",'
            '"uuid":"hub-row-record-2","timestamp":"2026-11-25T12:00:01Z",'
            '"chat_stream_row":'
            + json.dumps(
                _hub_row(
                    "concurrent-hub-note",
                    kind="note",
                    created_at="2026-11-25T12:00:01Z",
                    history_index=21,
                )
            )
            + "}\n"
        )

    resumed_manager = ConversationManager(config)
    assert resumed_manager.load_session(logger.session_id)
    assert [row["id"] for row in resumed_manager.chat_stream_rows] == [
        "retained-prefix-note",
        "concurrent-hub-note",
    ]
    summary = next(
        message
        for message in resumed_manager.messages
        if message.get("metadata", {}).get("context_compaction")
    )
    assert summary["metadata"]["compaction_snapshot_count"] == 20
    assert summary["metadata"]["compaction_retained_positions"] == [
        0,
        14,
        15,
        16,
        17,
        18,
        19,
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
        "retained-prefix-note",
        "concurrent-hub-note",
    ]


def test_repeated_live_compaction_remaps_hub_row_into_current_history_coordinates():
    row = _hub_row(
        "retained-from-first-compaction",
        kind="note",
        created_at="2026-11-25T12:00:01Z",
        history_index=19,
    )
    row["_history_row_revision"] = 1
    manager = SimpleNamespace(messages=[], chat_stream_rows=[row])

    def set_history_coordinate(compaction_id, history_length):
        manager.chat_stream_history_compaction_id = compaction_id
        manager.chat_stream_history_length = history_length

    manager.set_chat_stream_history_coordinate = set_history_coordinate
    first_summary = SimpleNamespace(
        role="user",
        content="first summary",
        timestamp="2026-11-25T12:00:02Z",
        metadata={
            "context_compaction": True,
            "compaction_round": 1,
            "compaction_id": "compact-one",
            "compaction_source_id": None,
            "compaction_snapshot_count": 20,
            "compaction_snapshot_row_revision": 1,
            "compaction_retained_positions": [0, 19],
            "compaction_retained_position_map": {"0": 0, "19": 2},
            "compaction_output_count": 3,
        },
    )
    llm = SimpleNamespace(
        conversation_manager=manager,
        conversation_history=[
            SimpleNamespace(role="system", content="system", timestamp=None, metadata={}),
            first_summary,
            SimpleNamespace(role="assistant", content="retained tail", timestamp=None, metadata={}),
        ],
    )
    state = SimpleNamespace(
        _llm_service=llm,
        get_conversation_uid=lambda: "repeated-live-compaction",
        chat_stream_snapshot=lambda: {"rows": [], "turns": [], "inputs": []},
    )
    stream = ChatStreamService(state)

    stream.replace_transcript()

    assert manager.chat_stream_rows[0]["_history_index"] == 2
    assert manager.chat_stream_rows[0]["_history_compaction_id"] == "compact-one"
    assert manager.chat_stream_history_compaction_id == "compact-one"
    assert manager.chat_stream_history_length == 3

    second_summary = SimpleNamespace(
        role="user",
        content="second summary",
        timestamp="2026-11-25T12:00:04Z",
        metadata={
            "context_compaction": True,
            # Simulate a daemon restart: the plugin's in-memory round resets.
            "compaction_round": 1,
            "compaction_id": "compact-two",
            "compaction_source_id": "compact-one",
            "compaction_snapshot_count": 3,
            "compaction_snapshot_row_revision": 1,
            "compaction_retained_positions": [0],
            "compaction_retained_position_map": {"0": 0},
            "compaction_output_count": 2,
        },
    )
    llm.conversation_history = [
        SimpleNamespace(role="system", content="system", timestamp=None, metadata={}),
        second_summary,
    ]

    replacement = stream.replace_transcript()

    assert "retained-from-first-compaction" not in {
        row["id"] for row in replacement["data"]["rows"]
    }
    assert manager.chat_stream_rows == []
    assert manager.chat_stream_history_compaction_id == "compact-two"
    assert manager.chat_stream_history_length == 2


def test_repeated_compaction_rows_are_filtered_sequentially_after_restart(tmp_path):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.conversation_metadata["conversation_uid"] = "repeated-restart-compaction"
    old_row = _hub_row(
        "stale-after-second-compaction",
        kind="note",
        created_at="2026-11-25T12:00:01Z",
        history_index=19,
    )
    old_row["_history_row_revision"] = 1
    assert manager.record_hub_chat_stream_row(old_row)
    assert manager.save_session(logger.session_id)

    common = (
        '"messages_summarized":10,"messages_kept":3,"summary_length":120,'
        '"pre_message_count":20,"post_message_count":3,'
        '"compaction_snapshot_row_revision":1,'
        '"compaction_retained_positions":[0,19],'
        '"compaction_retained_position_map":{"0":0,"19":2},'
        '"compaction_output_count":3'
    )
    with logger.session_file.open("a") as session:
        session.write(
            '{"type":"context_compaction","uuid":"compact-one",'
            '"timestamp":"2026-11-25T12:00:02Z","compaction_round":1,'
            '"compaction_id":"compact-one","compaction_source_id":null,'
            '"compaction_snapshot_count":20,' + common + ',"content":"summary one"}\n'
        )
        session.write(
            '{"type":"context_compaction","uuid":"compact-two",'
            '"timestamp":"2026-11-25T12:00:04Z","compaction_round":1,'
            '"compaction_id":"compact-two","compaction_source_id":"compact-one",'
            '"compaction_snapshot_count":3,'
            '"compaction_snapshot_row_revision":1,'
            '"compaction_retained_positions":[0],'
            '"compaction_retained_position_map":{"0":0},'
            '"compaction_output_count":2,"content":"summary two"}\n'
        )

    resumed_manager = ConversationManager(config)
    assert resumed_manager.load_session(logger.session_id)

    assert resumed_manager.chat_stream_rows == []
    summary = next(
        message
        for message in resumed_manager.messages
        if message.get("metadata", {}).get("context_compaction")
    )
    assert summary["metadata"]["compaction_id"] == "compact-two"
    assert summary["metadata"]["compaction_source_id"] == "compact-one"
    assert summary["metadata"]["compaction_retained_position_map"] == {"0": 0}


def test_hub_row_after_staged_compaction_replays_through_later_compaction_and_branch(
    tmp_path,
):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.save_conversations = False
    manager.conversation_metadata["conversation_uid"] = "staged-row-replay"
    manager.add_message("system", "base system prompt")
    for index in range(20):
        role = "user" if index % 2 == 0 else "assistant"
        manager.add_message(role, f"pre-compaction message {index}")
    manager.messages[0]["timestamp"] = None
    for message in manager.messages[1:]:
        message["timestamp"] = "2026-11-25T11:00:00Z"
    manager._update_context_window()
    assert manager.save_session(logger.session_id)

    late_row = _hub_row(
        "hub-row-during-staged-compaction",
        kind="note",
        created_at="2026-11-25T12:00:11Z",
        history_index=21,
    )
    late_row["_history_row_revision"] = 1
    records = [
        {
            "type": "context_compaction",
            "uuid": "compact-one-record",
            "timestamp": "2026-11-25T12:00:10Z",
            "compaction_round": 1,
            "compaction_snapshot_count": 21,
            "compaction_retained_positions": [0, 20],
            "compaction_retained_position_map": {"0": 0, "20": 2},
            "compaction_output_count": 3,
            "compaction_id": "compact-one-staged",
            "compaction_snapshot_row_revision": 0,
            "content": "first staged summary",
        },
        {
            "type": "system",
            "subtype": "hub_incoming",
            "uuid": "hub-row-record-during-stage",
            "timestamp": "2026-11-25T12:00:11Z",
            "chat_stream_row": late_row,
        },
        {
            "type": "context_compaction",
            "uuid": "compact-two-record",
            "timestamp": "2026-11-25T12:00:12Z",
            # Simulate a new daemon whose in-memory round starts over.
            "compaction_round": 1,
            "compaction_snapshot_count": 3,
            "compaction_retained_positions": [0, 2],
            "compaction_retained_position_map": {"0": 0, "2": 1},
            "compaction_output_count": 2,
            "compaction_id": "compact-two-staged",
            "compaction_source_id": "compact-one-staged",
            "compaction_snapshot_row_revision": 1,
            "content": "second staged summary",
        },
        {
            "type": "system",
            "subtype": "hub_incoming",
            "uuid": "hub-row-record-after-stage",
            "timestamp": "2026-11-25T12:00:12.500Z",
            "chat_stream_row": {
                **late_row,
                "_history_index": 2,
                "_history_compaction_id": "compact-two-staged",
            },
        },
        {
            "type": "assistant",
            "uuid": "post-compaction-assistant",
            "timestamp": "2026-11-25T12:00:13Z",
            "message": {"role": "assistant", "content": "after compaction"},
        },
    ]
    with logger.session_file.open("a") as session:
        for record in records:
            session.write(json.dumps(record) + "\n")

    resumed = ConversationManager(config)
    assert resumed.load_session(logger.session_id)
    row = next(
        row
        for row in resumed.chat_stream_rows
        if row["id"] == "hub-row-during-staged-compaction"
    )
    assert [row["id"] for row in resumed.chat_stream_rows] == [
        "hub-row-during-staged-compaction"
    ]
    assert row["_history_index"] == 2
    assert row["_history_compaction_id"] == "compact-two-staged"

    through_row = resumed.branch_session(logger.session_id, 2)
    assert through_row["success"] is True
    assert [row["id"] for row in resumed.chat_stream_rows] == [
        "hub-row-during-staged-compaction"
    ]
    before_row = resumed.branch_session(logger.session_id, 1)
    assert before_row["success"] is True
    assert resumed.chat_stream_rows == []
    assert resumed.chat_stream_history_length == 2
    after_branch = resumed.record_hub_chat_stream_row(
        _hub_row(
            "hub-row-after-branch",
            kind="note",
            created_at="2026-11-25T12:00:14Z",
        )
    )
    assert after_branch["_history_index"] == 2
    assert after_branch["_history_compaction_id"] == "compact-two-staged"


def test_compacted_hub_rows_filter_from_saved_base_without_stream_tail(tmp_path):
    conversations = tmp_path / "conversations"
    config = SimpleNamespace(
        _conversations_dir=conversations,
        get=lambda _key, default=None: default,
    )
    logger = KollaborConversationLogger(conversations)
    manager = ConversationManager(config, logger)
    manager.conversation_metadata["conversation_uid"] = "saved-base-compacted-hub"
    manager.add_message("user", "compaction summary")
    manager.messages[0]["timestamp"] = "2026-11-25T12:00:02Z"
    manager.messages[0]["metadata"].update(
        {
            "context_compaction": True,
            "compaction_round": 1,
            "compaction_snapshot_count": 20,
            "compaction_retained_positions": [0, 14, 15, 16, 17, 18, 19],
        }
    )
    manager.chat_stream_rows = [
        _hub_row(
            "saved-base-old-row",
            kind="note",
            created_at="2026-11-25T12:00:01Z",
            history_index=4,
        ),
        _hub_row(
            "saved-base-retained-row",
            kind="note",
            created_at="2026-11-25T12:00:01Z",
            history_index=19,
        ),
    ]
    assert manager.save_session(logger.session_id)

    resumed_manager = ConversationManager(config)
    assert resumed_manager.load_session(logger.session_id)
    assert [row["id"] for row in resumed_manager.chat_stream_rows] == [
        "saved-base-retained-row"
    ]
    branch = resumed_manager.branch_session(logger.session_id, 0)
    assert branch["success"] is True
    assert [row["id"] for row in resumed_manager.chat_stream_rows] == [
        "saved-base-retained-row"
    ]
