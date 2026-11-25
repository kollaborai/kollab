"""Chat stream contract fixtures (#1451): each fixture states the problems it must produce."""

import json
from pathlib import Path

import pytest

from kollabor_events.chat_stream import event_problems, sequence_problems

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "packages/kollabor-webui/frontend/tests/fixtures/chat-stream"
)
CASES = sorted(FIXTURES.glob("*.json"))


def test_fixtures_cover_good_and_bad_streams():
    names = {path.stem for path in CASES}
    assert {
        "steered-input",
        "reload-mid-turn",
        "compaction",
        "bad-reid",
        "bad-never-ends",
        "bad-seq",
        "bad-regress",
    } <= names


@pytest.mark.parametrize("path", CASES, ids=lambda path: path.stem)
def test_fixture_produces_exactly_its_expected_problems(path):
    case = json.loads(path.read_text())
    problems = sequence_problems(case["events"], complete=case["complete"])
    if not case["expect"]:
        assert problems == []
    else:
        assert len(problems) == len(case["expect"]), problems
        for needle in case["expect"]:
            assert any(needle in problem for problem in problems), (needle, problems)


def test_every_fixture_event_has_a_valid_shape():
    for path in CASES:
        for event in json.loads(path.read_text())["events"]:
            assert event_problems(event) == [], (path.stem, event)


@pytest.mark.parametrize(
    "event, needle",
    [
        (
            {"seq": 1, "conversation_uid": "c", "type": "nope", "data": {}},
            "unknown event type",
        ),
        (
            {
                "seq": -1,
                "conversation_uid": "c",
                "type": "row.delete",
                "data": {"id": "a"},
            },
            "non-negative",
        ),
        (
            {
                "seq": True,
                "conversation_uid": "c",
                "type": "row.delete",
                "data": {"id": "a"},
            },
            "non-negative",
        ),
        (
            {
                "seq": 1,
                "conversation_uid": "",
                "type": "row.delete",
                "data": {"id": "a"},
            },
            "conversation_uid",
        ),
        (
            {
                "seq": 1,
                "conversation_uid": "c",
                "type": "input.state",
                "data": {"id": "a", "state": "ended"},
            },
            "outcome",
        ),
        (
            {
                "seq": 1,
                "conversation_uid": "c",
                "type": "input.state",
                "data": {"id": "a", "state": "joined"},
            },
            "turn_id",
        ),
        (
            {
                "seq": 1,
                "conversation_uid": "c",
                "type": "row.append",
                "data": {"id": "a", "text": "x"},
            },
            "part",
        ),
        (
            {
                "seq": 1,
                "conversation_uid": "c",
                "type": "snapshot",
                "data": {"rows": []},
            },
            "next_seq",
        ),
    ],
)
def test_event_problems_names_the_broken_field(event, needle):
    assert any(needle in problem for problem in event_problems(event))


def test_an_input_that_changes_after_it_ended_is_reported():
    base = json.loads((FIXTURES / "steered-input.json").read_text())["events"]
    late = {
        "seq": 99,
        "conversation_uid": "conv-1",
        "type": "input.state",
        "data": {"id": "web-a1", "state": "queued", "turn_id": None},
    }
    problems = sequence_problems([*base, late])
    assert any("changed after it ended" in problem for problem in problems)


def test_a_completed_input_must_wait_for_its_turn():
    events = json.loads((FIXTURES / "steered-input.json").read_text())["events"]
    turn_end = next(
        i
        for i, event in enumerate(events)
        if event["type"] == "turn.state" and event["data"]["state"] == "ended"
    )
    early = [*events[:turn_end], *events[turn_end + 1 :]]
    problems = sequence_problems(early)
    assert any("completed before its turn t1 ended" in problem for problem in problems)


def test_an_unfinished_stream_may_leave_inputs_open():
    events = json.loads((FIXTURES / "bad-never-ends.json").read_text())["events"]
    assert sequence_problems(events, complete=False) == []


ROW = {
    "id": "r1",
    "seq": 1,
    "kind": "input",
    "role": "user",
    "turn_id": None,
    "status": "queued",
    "parts": [],
    "created_at": "2026-11-25T10:00:00Z",
}
SNAPSHOT = {"rows": [], "next_seq": 1, "older": None, "turns": [], "inputs": []}


def _event(kind, data, seq=1):
    return {"seq": seq, "conversation_uid": "c", "type": kind, "data": data}


def _without(data, key):
    return {k: v for k, v in data.items() if k != key}


def test_a_valid_row_and_snapshot_have_no_problems():
    assert event_problems(_event("row.upsert", ROW)) == []
    assert event_problems(_event("snapshot", SNAPSHOT)) == []
    assert event_problems(_event("snapshot", {**SNAPSHOT, "older": "cursor-1"})) == []


@pytest.mark.parametrize(
    "row, needle",
    [
        ({**ROW, "role": "alien"}, "unknown role"),
        (_without(ROW, "created_at"), "missing created_at"),
        ({**ROW, "seq": True}, "row seq"),
        ({**ROW, "seq": 1.5}, "row seq"),
        ({**ROW, "seq": -1}, "row seq"),
        ({**ROW, "turn_id": 5}, "row turn_id"),
        ({**ROW, "status": None}, "row status"),
        ({**ROW, "created_at": 0}, "row created_at"),
    ],
)
def test_row_problems_check_every_field(row, needle):
    problems = event_problems(_event("row.upsert", row))
    assert any(needle in problem for problem in problems), problems


@pytest.mark.parametrize(
    "data, needle",
    [
        ({**SNAPSHOT, "next_seq": True}, "next_seq"),
        ({**SNAPSHOT, "next_seq": -1}, "next_seq"),
        (_without(SNAPSHOT, "older"), "older"),
        ({**SNAPSHOT, "older": 5}, "older"),
        ({**SNAPSHOT, "turns": ["not a turn"]}, "turn 0"),
        (
            {**SNAPSHOT, "turns": [{"turn_id": "t1", "state": "paused", "input_ids": []}]},
            "unknown turn state",
        ),
        ({**SNAPSHOT, "turns": [{"turn_id": "t1", "state": "running"}]}, "input_ids"),
        ({**SNAPSHOT, "inputs": [{}]}, "input 0"),
    ],
)
def test_snapshot_problems_check_cursors_and_entries(data, needle):
    problems = event_problems(_event("snapshot", data))
    assert any(needle in problem for problem in problems), problems


def _lifecycle(*states):
    """A stream with one input row and the given input.state events, in order."""
    events = [
        _event("snapshot", {**SNAPSHOT, "next_seq": 2}),
        _event("row.upsert", {**ROW, "id": "web-a1", "seq": 2}, seq=2),
    ]
    for offset, data in enumerate(states):
        kind = "turn.state" if "input_ids" in data else "input.state"
        events.append(_event(kind, data, seq=3 + offset))
    return events


def test_a_queued_input_may_end_without_starting():
    events = _lifecycle(
        {"id": "web-a1", "state": "queued", "turn_id": None},
        {"id": "web-a1", "state": "ended", "turn_id": None, "outcome": "cancelled"},
    )
    assert sequence_problems(events) == []


def test_a_joined_input_cannot_start_its_own_turn_after():
    events = _lifecycle(
        {"id": "web-a1", "state": "joined", "turn_id": "t1"},
        {"id": "web-a1", "state": "started", "turn_id": "t2"},
    )
    problems = sequence_problems(events, complete=False)
    assert any("went from joined to started" in problem for problem in problems), problems


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_an_input_bound_to_a_turn_ends_after_that_turn_whatever_the_outcome(outcome):
    events = _lifecycle(
        {"id": "web-a1", "state": "started", "turn_id": "t1"},
        {"turn_id": "t1", "state": "running", "input_ids": ["web-a1"]},
        {"id": "web-a1", "state": "ended", "turn_id": "t1", "outcome": outcome},
        {"turn_id": "t1", "state": "ended", "input_ids": ["web-a1"]},
    )
    problems = sequence_problems(events)
    assert any("ended before its turn t1 ended" in problem for problem in problems), problems
