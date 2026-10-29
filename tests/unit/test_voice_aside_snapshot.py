"""What the Quick Model knows of a running turn, and how its reply reads (#388)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "packages/kollabor-voice/src")
)

from kollabor_voice.responder import (
    aside_snapshot,
    is_stop,
    local_line,
    parse_aside,
    running_turn,
    tool_entries,
)

T = 1000.0
PUSH = "git push https://u:tok@host/r.git"


def ev(kind, ts, **fields):
    return {"type": kind, "ts": ts, **fields}


EVENTS = [
    ev("token", T - 500, text="old turn"),
    ev("turn_complete", T - 490),
    ev("message", T - 60, message_type="user", content="run the tests"),
    ev(
        "tool_start", T - 50, tool_id="a", tool_name="file_read", args={"path": "/s.py"}
    ),
    ev("tool_result", T - 48, tool_id="a", tool_name="file_read", success=True),
    ev("tool_start", T - 40, tool_id="b", tool_name="terminal", command=PUSH),
]


def test_snapshot_is_the_running_turn_names_and_times_only():
    snap = aside_snapshot(EVENTS, T)
    assert snap.splitlines() == [
        "The agent's turn has been running for about 60 s.",
        "- file_read: done in 2 s",
        "- terminal: running for 40 s",
    ]
    assert "tok" not in snap and "s.py" not in snap and "old turn" not in snap


def test_an_aside_in_the_history_leaves_the_running_turn_alone():
    events = EVENTS + [ev("voice_aside", T - 5, text="Still on it.", end_turn=True)]
    assert [e["type"] for e in running_turn(events)][:2] == ["message", "tool_start"]
    assert "- terminal: running for 40 s" in aside_snapshot(events, T)


def test_a_failed_tool_and_a_turn_with_no_tools():
    failed = [
        ev("tool_start", T - 9, tool_id="c", tool_name="terminal"),
        ev("tool_result", T - 4, tool_id="c", success=False),
    ]
    assert tool_entries(failed, T) == [("terminal", "failed", 5)]
    assert aside_snapshot([ev("token", T - 3, text="hm")], T).splitlines() == [
        "The agent's turn has been running for about 3 s.",
        "- no tool has run yet in this turn",
    ]


def test_the_snapshot_keeps_the_newest_eight_tools():
    many = [
        ev("tool_start", T - 30 + i, tool_id=str(i), tool_name=f"tool{i}")
        for i in range(12)
    ]
    lines = aside_snapshot(many, T).splitlines()[1:]
    assert len(lines) == 8
    assert lines[0].startswith("- tool4:") and lines[-1].startswith("- tool11:")


def test_the_local_line_says_the_newest_tool():
    assert local_line(EVENTS, T) == (
        "Still working: terminal has been running for 40 seconds. I'll pass that on."
    )
    done = EVENTS + [ev("tool_result", T - 1, tool_id="b", success=True)]
    assert (
        local_line(done, T)
        == "Still working: terminal just finished. I'll pass that on."
    )
    edit = [ev("tool_start", T - 1, tool_id="d", tool_name="file_edit")]
    assert local_line(edit, T) == (
        "Still working: file edit has been running for 1 second. I'll pass that on."
    )
    assert local_line([], T) == "I'm still working on it. I'll pass that on."


def test_parse_aside_reads_the_first_line_only():
    assert parse_aside("ANSWER The tests are still running.") == (
        "ANSWER",
        "The tests are still running.",
    )
    assert parse_aside("\n  **FORWARD:** I'll tell it to run the linter.\nmore") == (
        "FORWARD",
        "I'll tell it to run the linter.",
    )
    assert parse_aside("answer - Almost done.") == ("ANSWER", "Almost done.")
    assert parse_aside("FORWARD — I've passed it on.") == (
        "FORWARD",
        "I've passed it on.",
    )
    for bad in (
        None,
        "",
        "Sure, the tests run.",
        "ANSWER",
        "ANSWERING now",
        "x\nANSWER y",
    ):
        assert parse_aside(bad) is None, bad
    assert len(parse_aside("ANSWER " + "a" * 500)[1]) == 300


def test_a_stop_verdict_and_a_dash_inside_the_quotes():
    assert parse_aside("STOP Okay, stopping now.") == ("STOP", "Okay, stopping now.")
    assert parse_aside('ANSWER: "— Almost done."') == ("ANSWER", "Almost done.")


@pytest.mark.parametrize(
    "said",
    ["stop", "Stop.", "okay stop", "Please stop.", "stop it", "Stop that now!",
     "cancel", "Cancel that.", "abort", "No, stop.", "stop, stop"],
)  # fmt: skip
def test_a_whole_stop_needs_no_model(said):
    assert is_stop(said)


@pytest.mark.parametrize(
    "said",
    ["", "don't stop", "stop?", "can you stop", "stop the tests and run lint",
     "what are you doing", "unstoppable", "nonstop", "never mind", "forget it"],
)  # fmt: skip
def test_a_stop_inside_other_words_is_no_stop(said):
    assert not is_stop(said)
