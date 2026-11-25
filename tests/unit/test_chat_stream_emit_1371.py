"""Lifecycle emit helper for chat stream events (#1371, spec #1451)."""

import pytest

from kollabor_events.chat_stream_emit import emit_chat_event


def test_a_queued_input_state_reaches_the_sink():
    sink = []

    emit_chat_event(
        sink.append,
        seq=2,
        conversation_uid="conv-1",
        event_type="input.state",
        data={"id": "web-a1", "state": "queued", "turn_id": None},
    )

    assert sink == [
        {
            "seq": 2,
            "conversation_uid": "conv-1",
            "type": "input.state",
            "data": {"id": "web-a1", "state": "queued", "turn_id": None},
        }
    ]


def test_a_started_input_without_turn_id_raises_and_is_not_sent():
    sink = []

    with pytest.raises(ValueError, match="a started input needs turn_id"):
        emit_chat_event(
            sink.append,
            seq=3,
            conversation_uid="conv-1",
            event_type="input.state",
            data={"id": "web-a1", "state": "started", "turn_id": None},
        )

    assert sink == []


def test_a_non_lifecycle_type_raises_and_is_not_sent():
    sink = []

    with pytest.raises(ValueError, match="only emits lifecycle events"):
        emit_chat_event(
            sink.append,
            seq=4,
            conversation_uid="conv-1",
            event_type="row.delete",
            data={"id": "r1"},
        )

    assert sink == []


def test_a_turn_state_passes():
    sink = []

    emit_chat_event(
        sink.append,
        seq=5,
        conversation_uid="conv-1",
        event_type="turn.state",
        data={"turn_id": "t1", "state": "running", "input_ids": ["web-a1"]},
    )

    assert sink == [
        {
            "seq": 5,
            "conversation_uid": "conv-1",
            "type": "turn.state",
            "data": {"turn_id": "t1", "state": "running", "input_ids": ["web-a1"]},
        }
    ]


def test_later_edits_to_the_callers_dict_do_not_change_the_event():
    sink = []
    data = {"id": "web-a1", "state": "queued", "turn_id": None}

    emit_chat_event(
        sink.append,
        seq=6,
        conversation_uid="conv-1",
        event_type="input.state",
        data=data,
    )
    data["state"] = "ended"

    assert data["state"] == "ended"
    assert sink[0]["data"] == {"id": "web-a1", "state": "queued", "turn_id": None}
