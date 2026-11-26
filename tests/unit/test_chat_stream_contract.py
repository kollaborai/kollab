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
        "bad-gap",
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


@pytest.mark.parametrize(
    ("outcome", "should_report"),
    [("completed", True), ("cancelled", False)],
)
def test_queued_input_can_only_end_without_a_turn_when_cancelled(
    outcome, should_report
):
    events = [
        {
            "seq": 0,
            "conversation_uid": "c",
            "type": "snapshot",
            "data": {
                "rows": [
                    {
                        "id": "i",
                        "seq": 0,
                        "kind": "input",
                        "role": "user",
                        "turn_id": None,
                        "status": "queued",
                        "parts": [],
                        "created_at": "now",
                    }
                ],
                "next_seq": 1,
                "older": None,
                "turns": [],
                "inputs": [{"id": "i", "state": "queued", "turn_id": None}],
            },
        },
        {
            "seq": 1,
            "conversation_uid": "c",
            "type": "input.state",
            "data": {
                "id": "i",
                "state": "ended",
                "turn_id": None,
                "outcome": outcome,
            },
        },
    ]

    problems = sequence_problems(events)
    invalid_completion = any(
        "queued" in problem and "ended" in problem for problem in problems
    )
    assert invalid_completion is should_report, problems


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


def test_an_input_cannot_start_after_its_turn_ended():
    events = [
        _event("snapshot", {**SNAPSHOT, "next_seq": 1}, seq=0),
        _event(
            "turn.state",
            {"turn_id": "t1", "state": "ended", "input_ids": []},
            seq=1,
        ),
        _event("row.upsert", {**ROW, "id": "i", "seq": 2, "turn_id": "t1"}, seq=2),
        _event("input.state", {"id": "i", "state": "started", "turn_id": "t1"}, seq=3),
        _event(
            "input.state",
            {"id": "i", "state": "ended", "turn_id": "t1", "outcome": "completed"},
            seq=4,
        ),
    ]

    problems = sequence_problems(events)
    assert any("started after turn t1 ended" in problem for problem in problems)


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
            {
                **SNAPSHOT,
                "turns": [{"turn_id": "t1", "state": "paused", "input_ids": []}],
            },
            "unknown turn state",
        ),
        ({**SNAPSHOT, "turns": [{"turn_id": "t1", "state": "running"}]}, "input_ids"),
        ({**SNAPSHOT, "inputs": [{}]}, "input 0"),
    ],
)
def test_snapshot_problems_check_cursors_and_entries(data, needle):
    problems = event_problems(_event("snapshot", data))
    assert any(needle in problem for problem in problems), problems


def test_snapshot_next_seq_cannot_precede_snapshot_seq():
    snapshot = _event("snapshot", {**SNAPSHOT, "next_seq": 5}, seq=10)
    problems = sequence_problems([snapshot])
    assert any(
        "next_seq" in problem and "seq" in problem for problem in problems
    ), problems


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
    assert any(
        "went from joined to started" in problem for problem in problems
    ), problems


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_an_input_bound_to_a_turn_ends_after_that_turn_whatever_the_outcome(outcome):
    events = _lifecycle(
        {"id": "web-a1", "state": "started", "turn_id": "t1"},
        {"turn_id": "t1", "state": "running", "input_ids": ["web-a1"]},
        {"id": "web-a1", "state": "ended", "turn_id": "t1", "outcome": outcome},
        {"turn_id": "t1", "state": "ended", "input_ids": ["web-a1"]},
    )
    problems = sequence_problems(events)
    assert any(
        "ended before its turn t1 ended" in problem for problem in problems
    ), problems


PARTS = [
    {"type": "text", "text": "hi"},
    {"type": "reasoning", "text": "thinking"},
    {
        "type": "tool-call",
        "tool_call_id": "call-1",
        "tool_name": "shell",
        "args": {"cmd": "ls"},
    },
    {
        "type": "tool-call",
        "tool_call_id": "call-2",
        "tool_name": "shell",
        "args": None,
        "result": "ok",
        "is_error": False,
    },
    {"type": "image", "url": "data:image/png;base64,AAAA"},
    {"type": "prompt", "prompt_id": "p1", "data": {"options": []}},
    {"type": "hub-note", "from": "lapis", "to": "", "text": "done", "observed": True},
    {
        "type": "hub-note",
        "from": "lapis",
        "to": "me",
        "text": "done",
        "observed": False,
        "kind": "reply",
        "thread_id": "th-1",
        "reply_to": "m-1",
        "is_direct": True,
        "presence": "online",
    },
]


def test_a_row_may_carry_every_part_type():
    assert event_problems(_event("row.upsert", {**ROW, "parts": PARTS})) == []


@pytest.mark.parametrize(
    "part",
    [
        None,
        "text",
        {"type": "nope"},
        {"type": "text"},
        {"type": "text", "text": 5},
        {"type": "reasoning"},
        {"type": "tool-call", "tool_name": "shell", "args": {}},
        {"type": "tool-call", "tool_call_id": "c", "args": {}},
        {"type": "tool-call", "tool_call_id": "c", "tool_name": "shell"},
        {
            "type": "tool-call",
            "tool_call_id": "c",
            "tool_name": "shell",
            "args": {},
            "is_error": "no",
        },
        {"type": "image"},
        {"type": "image", "url": ""},
        {"type": "prompt", "data": {}},
        {"type": "prompt", "prompt_id": "p1"},
        {"type": "hub-note", "from": "lapis", "to": "", "text": "x"},
        {"type": "hub-note", "from": "lapis", "to": "", "text": "x", "observed": "yes"},
        {"type": "hub-note", "from": "lapis", "text": "x", "observed": True},
        {
            "type": "hub-note",
            "from": "lapis",
            "to": "",
            "text": "x",
            "observed": True,
            "presence": "away",
        },
        {
            "type": "hub-note",
            "from": "lapis",
            "to": "",
            "text": "x",
            "observed": True,
            "is_direct": 1,
        },
    ],
)
def test_each_part_is_checked_against_its_type(part):
    problems = event_problems(_event("row.upsert", {**ROW, "parts": [part]}))
    assert any("part 0" in problem for problem in problems), problems


@pytest.mark.parametrize(
    "info, needle",
    [
        ({"id": "a", "state": "queued"}, "turn_id"),
        ({"id": "a", "state": "queued", "turn_id": 5}, "turn_id"),
        (
            {"id": "a", "state": "ended", "turn_id": 5, "outcome": "completed"},
            "turn_id",
        ),
        (
            {"id": "a", "state": "queued", "turn_id": None, "outcome": "completed"},
            "outcome",
        ),
        (
            {"id": "a", "state": "started", "turn_id": "t1", "outcome": "nope"},
            "outcome",
        ),
    ],
)
def test_input_info_checks_turn_id_and_outcome(info, needle):
    for event in (
        _event("input.state", info),
        _event("snapshot", {**SNAPSHOT, "inputs": [info]}),
    ):
        problems = event_problems(event)
        assert any(needle in problem for problem in problems), (event["type"], problems)


@pytest.mark.parametrize("part", [-1, True, 1.0])
def test_row_append_needs_a_part_index(part):
    problems = event_problems(
        _event("row.append", {"id": "r1", "part": part, "text": "x"})
    )
    assert any("part" in problem for problem in problems), problems


@pytest.mark.parametrize(
    "event",
    [
        _event("row.delete", {"id": "a"}) | {"type": []},
        _event("row.upsert", {**ROW, "kind": []}),
        _event("row.upsert", {**ROW, "role": {}}),
        _event("input.state", {"id": "a", "state": [], "turn_id": None}),
        _event(
            "input.state", {"id": "a", "state": "ended", "turn_id": None, "outcome": []}
        ),
        _event("turn.state", {"turn_id": "t1", "state": [], "input_ids": []}),
        _event("row.upsert", {**ROW, "parts": [{"type": []}]}),
    ],
)
def test_values_of_the_wrong_type_are_problems_not_crashes(event):
    assert event_problems(event)


def test_a_missing_event_is_a_gap():
    events = json.loads((FIXTURES / "bad-gap.json").read_text())["events"]
    problems = sequence_problems(events, complete=False)
    assert any("expected seq 3" in problem for problem in problems), problems


def test_sequence_rejects_events_from_another_conversation():
    events = [
        {
            "seq": 1,
            "conversation_uid": "a",
            "type": "snapshot",
            "data": {
                "rows": [],
                "next_seq": 2,
                "older": None,
                "turns": [],
                "inputs": [],
            },
        },
        {"seq": 2, "conversation_uid": "b", "type": "replace", "data": {"rows": []}},
    ]
    assert any("conversation" in problem for problem in sequence_problems(events))


def test_input_cannot_change_its_turn_binding_to_end_early():
    events = _lifecycle(
        {"id": "web-a1", "state": "started", "turn_id": "t1"},
        {"turn_id": "t2", "state": "ended", "input_ids": []},
        {"id": "web-a1", "state": "ended", "turn_id": "t2", "outcome": "completed"},
    )
    problems = sequence_problems(events)
    assert any("turn" in problem and "t1" in problem for problem in problems), problems


def test_input_cannot_drop_turn_binding_when_ending():
    events = _lifecycle(
        {"id": "web-a1", "state": "started", "turn_id": "t1"},
        {"turn_id": "t1", "state": "ended", "input_ids": ["web-a1"]},
        {"id": "web-a1", "state": "ended", "turn_id": None, "outcome": "completed"},
    )
    problems = sequence_problems(events)
    assert any("turn" in problem and "t1" in problem for problem in problems), problems


def _turn_event(kind, turn, seq):
    """The turn as its own turn.state event, or in a snapshot that holds only it."""
    if kind == "snapshot":
        return _event(
            "snapshot", {**SNAPSHOT, "next_seq": seq + 1, "turns": [turn]}, seq=seq
        )
    return _event("turn.state", turn, seq=seq)


@pytest.mark.parametrize(
    "first, second",
    [
        ("turn.state", "turn.state"),
        ("turn.state", "snapshot"),
        ("snapshot", "turn.state"),
        ("snapshot", "snapshot"),
    ],
)
def test_a_turn_cannot_resume_after_it_ended(first, second):
    ended = {"turn_id": "t1", "state": "ended", "input_ids": []}
    running = {"turn_id": "t1", "state": "running", "input_ids": []}
    events = [
        _event("snapshot", {**SNAPSHOT, "next_seq": 2}, seq=1),
        _turn_event(first, ended, seq=2),
        _turn_event(second, running, seq=3),
    ]
    problems = sequence_problems(events)
    assert any(
        "turn t1" in problem and "running" in problem for problem in problems
    ), problems


def test_a_turn_may_restate_its_state_without_going_back():
    running = {"turn_id": "t1", "state": "running", "input_ids": []}
    ended = {"turn_id": "t1", "state": "ended", "input_ids": []}
    events = [
        _event("snapshot", {**SNAPSHOT, "next_seq": 2}, seq=1),
        _turn_event("turn.state", running, seq=2),
        _turn_event("snapshot", running, seq=3),
        _turn_event("turn.state", ended, seq=4),
        _turn_event("snapshot", ended, seq=5),
        _turn_event("turn.state", ended, seq=6),
    ]
    assert sequence_problems(events) == []


@pytest.mark.parametrize("state, turn_id", [("queued", None), ("started", "t1")])
def test_a_snapshot_cannot_reopen_an_ended_input(state, turn_id):
    ended = {"id": "web-a1", "state": "ended", "turn_id": None, "outcome": "cancelled"}
    reopened = {"id": "web-a1", "state": state, "turn_id": turn_id}
    events = [
        _event("snapshot", {**SNAPSHOT, "next_seq": 2}, seq=1),
        _event("row.upsert", {**ROW, "id": "web-a1", "seq": 2}, seq=2),
        _event("input.state", ended, seq=3),
        _event("snapshot", {**SNAPSHOT, "next_seq": 5, "inputs": [reopened]}, seq=4),
    ]
    problems = sequence_problems(events, complete=False)
    assert any("changed after it ended" in problem for problem in problems), problems
