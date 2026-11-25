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
