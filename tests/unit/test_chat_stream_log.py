"""Daemon conversation stream log behavior for issue #1451."""

from kollabor.state.chat_stream import ChatStreamLog


def _row(row_id: str) -> dict:
    return {
        "id": row_id,
        "seq": 1,
        "kind": "input",
        "role": "user",
        "turn_id": None,
        "status": "queued",
        "parts": [{"type": "text", "text": row_id}],
        "created_at": "2026-11-25T12:00:00Z",
    }


def _snapshot(rows=None):
    return {
        "rows": list(rows or []),
        "older": None,
        "turns": [],
        "inputs": [],
    }


def test_new_subscriber_gets_a_transcript_snapshot_with_next_cursor():
    log = ChatStreamLog(
        "conv-1",
        snapshot_provider=lambda: _snapshot([_row("saved-row")]),
    )

    event = log.read()[0]

    assert event["type"] == "snapshot"
    assert event["conversation_uid"] == "conv-1"
    assert event["data"]["rows"][0]["id"] == "saved-row"
    assert event["data"]["next_seq"] == event["seq"] + 1


def test_events_have_one_strict_sequence_and_replay_after_cursor():
    log = ChatStreamLog("conv-1", snapshot_provider=_snapshot, replay_window=3)
    events = [
        log.publish("row.delete", {"id": f"row-{index}"})
        for index in range(1, 4)
    ]

    replay = log.read(after=1, conversation_uid="conv-1")

    assert [event["seq"] for event in events] == [1, 2, 3]
    assert [event["seq"] for event in replay] == [2, 3]


def test_a_gap_outside_the_replay_window_gets_a_fresh_snapshot():
    log = ChatStreamLog("conv-1", snapshot_provider=_snapshot, replay_window=2)
    for index in range(4):
        log.publish("row.delete", {"id": f"row-{index}"})

    result = log.read(after=0, conversation_uid="conv-1")

    assert len(result) == 1
    assert result[0]["type"] == "snapshot"
    assert result[0]["data"]["next_seq"] == 5


def test_a_new_conversation_uid_gets_a_fresh_snapshot():
    log = ChatStreamLog("conv-2", snapshot_provider=_snapshot)
    log.publish("row.delete", {"id": "old-row"})

    result = log.read(after=1, conversation_uid="conv-1")

    assert len(result) == 1
    assert result[0]["type"] == "snapshot"
    assert result[0]["conversation_uid"] == "conv-2"


def test_the_current_turn_stays_replayable_past_the_regular_window():
    log = ChatStreamLog("conv-1", snapshot_provider=_snapshot, replay_window=2)
    log.publish(
        "turn.state",
        {"turn_id": "turn-1", "state": "running", "input_ids": ["input-1"]},
    )
    for index in range(5):
        log.publish("row.delete", {"id": f"row-{index}"})

    replay = log.read(after=0, conversation_uid="conv-1")

    assert [event["seq"] for event in replay] == [1, 2, 3, 4, 5, 6]
    assert all(event["type"] != "snapshot" for event in replay)

    log.publish(
        "turn.state",
        {"turn_id": "turn-1", "state": "ended", "input_ids": ["input-1"]},
    )
    assert log.read(after=0, conversation_uid="conv-1")[0]["type"] == "snapshot"
