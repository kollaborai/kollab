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


def test_event_requires_generation_to_make_sequence_numbers_restart_safe():
    event = {
        "seq": 1,
        "conversation_uid": "c",
        "type": "row.delete",
        "data": {"id": "a"},
    }
    assert any("generation" in problem for problem in event_problems(event))


def test_an_input_that_changes_after_it_ended_is_reported():
    base = json.loads((FIXTURES / "steered-input.json").read_text())["events"]
    late = {
        "seq": 99,
        "conversation_uid": "conv-1",
        "generation": base[0]["generation"],
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
    return {
        "seq": seq,
        "conversation_uid": "c",
        "generation": "daemon-1",
        "type": kind,
        "data": data,
    }


def _content_preview(*, field="text", row_id="r1", **overrides):
    marker = {
        "kind": "chat-stream-content-preview",
        "row_id": row_id,
        "part_index": 0,
        "field": field,
        "output_bytes": 40_000,
        "output_chars": 40_000,
        "output_type": "json" if field in {"args", "part"} else "text",
        "output_revision": "a" * 64,
        "preview": "bounded preview",
    }
    marker.update(overrides)
    return marker


def _part_preview(*, row_id="r1", **overrides):
    marker = {
        "kind": "chat-stream-part-preview",
        "row_id": row_id,
        "part_index": 0,
        "output_bytes": 40_000,
        "output_chars": 40_000,
        "output_type": "json",
        "output_revision": "b" * 64,
        "preview": "bounded preview",
    }
    marker.update(overrides)
    return marker


def _parts_preview(*, row_id="r1", **overrides):
    marker = {
        "kind": "chat-stream-parts-preview",
        "row_id": row_id,
        "part_count": 12,
        "output_bytes": 500_000,
        "output_chars": 420_000,
        "output_type": "json",
        "output_revision": "c" * 64,
    }
    marker.update(overrides)
    return marker


def _without(data, key):
    return {k: v for k, v in data.items() if k != key}


def test_a_valid_row_and_snapshot_have_no_problems():
    assert event_problems(_event("row.upsert", ROW)) == []
    assert event_problems(_event("snapshot", SNAPSHOT)) == []
    assert event_problems(_event("snapshot", {**SNAPSHOT, "older": "cursor-1"})) == []


def test_daemon_preview_markers_are_valid_before_engine_adds_session_identity():
    cases = [
        {
            **ROW,
            "parts": [
                {
                    "type": "text",
                    "text": "bounded preview",
                    "chat_stream_content_preview": _content_preview(),
                }
            ],
        },
        {
            **ROW,
            "parts": [
                {
                    "type": "tool-call",
                    "tool_call_id": "call-1",
                    "tool_name": "shell",
                    "args": _content_preview(field="args"),
                }
            ],
        },
        {
            **ROW,
            "parts": [
                {
                    "type": "text",
                    "text": "[Large part.]",
                    "chat_stream_part_preview": _part_preview(),
                }
            ],
        },
        {
            **ROW,
            "parts": [{"type": "text", "text": "[Large chat row.]"}],
            "chat_stream_parts_preview": _parts_preview(),
        },
    ]

    for row in cases:
        assert event_problems(_event("row.upsert", row)) == []


@pytest.mark.parametrize(
    "marker",
    [
        _content_preview(kind="unknown-preview"),
        _content_preview(row_id="another-row"),
        _content_preview(part_index=True),
        _content_preview(output_bytes=True),
        _content_preview(output_chars=-1),
        _content_preview(output_type="json"),
        _content_preview(output_revision="A" * 64),
        _content_preview(preview=42),
        _content_preview(session_id=""),
    ],
)
def test_contract_rejects_invalid_content_preview_metadata(marker):
    row = {
        **ROW,
        "parts": [
            {
                "type": "text",
                "text": "bounded preview",
                "chat_stream_content_preview": marker,
            }
        ],
    }
    assert event_problems(_event("row.upsert", row))


def test_contract_checks_content_preview_field_and_type_on_tool_args():
    for marker in (
        _content_preview(field="text"),
        _content_preview(field="args", output_type="text"),
    ):
        row = {
            **ROW,
            "parts": [
                {
                    "type": "tool-call",
                    "tool_call_id": "call-1",
                    "tool_name": "shell",
                    "args": marker,
                }
            ],
        }
        assert event_problems(_event("row.upsert", row))


@pytest.mark.parametrize(
    "marker",
    [
        _part_preview(kind="unknown-preview"),
        _part_preview(row_id="another-row"),
        _part_preview(part_index=True),
        _part_preview(output_bytes=-1),
        _part_preview(output_chars=True),
        _part_preview(output_type="text"),
        _part_preview(output_revision="b" * 63),
        _part_preview(preview=None),
        _part_preview(session_id=None),
    ],
)
def test_contract_rejects_invalid_part_preview_metadata(marker):
    row = {
        **ROW,
        "parts": [
            {
                "type": "text",
                "text": "[Large part.]",
                "chat_stream_part_preview": marker,
            }
        ],
    }
    assert event_problems(_event("row.upsert", row))


@pytest.mark.parametrize(
    "marker",
    [
        _parts_preview(kind="unknown-preview"),
        _parts_preview(row_id="another-row"),
        _parts_preview(part_count=True),
        _parts_preview(output_bytes=-1),
        _parts_preview(output_chars=True),
        _parts_preview(output_type="text"),
        _parts_preview(output_revision="c" * 63),
        _parts_preview(session_id=""),
    ],
)
def test_contract_rejects_invalid_parts_preview_metadata(marker):
    row = {
        **ROW,
        "parts": [{"type": "text", "text": "[Large chat row.]"}],
        "chat_stream_parts_preview": marker,
    }
    assert event_problems(_event("row.upsert", row))


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
            "generation": "daemon-1",
            "type": "snapshot",
            "data": {
                "rows": [],
                "next_seq": 2,
                "older": None,
                "turns": [],
                "inputs": [],
            },
        },
        {
            "seq": 2,
            "conversation_uid": "b",
            "generation": "daemon-1",
            "type": "replace",
            "data": {"rows": []},
        },
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


def test_goal_origin_requires_complete_identity():
    turn = {"turn_id": "t", "state": "running", "input_ids": []}
    valid = {"kind": "goal", "goal_id": "g", "attempt_id": "a"}
    assert not event_problems(_event("turn.state", {**turn, "origin": valid}))
    for origin in (
        None,
        {},
        {"kind": "goal", "goal_id": "g"},
        {"kind": "user", "goal_id": "g", "attempt_id": "a"},
    ):
        assert event_problems(_event("turn.state", {**turn, "origin": origin}))
