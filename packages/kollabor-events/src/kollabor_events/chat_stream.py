"""Chat stream contract: one transcript and one ordered stream per conversation.

The daemon produces these events and the engine relays them to every client.
Spec: docs/specs/webui-chat-single-stream.md (#1451). The web UI's copy of
the shapes is packages/kollabor-webui/frontend/src/chat-stream.ts, and both
sides test against packages/kollabor-webui/frontend/tests/fixtures/chat-stream/.
"""

from __future__ import annotations

from typing import Any, Iterable, TypedDict

ROW_KINDS = frozenset({"input", "reply", "tool", "note", "prompt", "marker"})
ROLES = frozenset({"user", "assistant", "system"})
INPUT_STATES = frozenset({"queued", "started", "joined", "ended"})
OUTCOMES = frozenset({"completed", "failed", "cancelled"})
TURN_STATES = frozenset({"running", "ended"})
EVENT_TYPES = frozenset(
    {
        "snapshot",
        "row.upsert",
        "row.append",
        "row.delete",
        "replace",
        "input.state",
        "turn.state",
    }
)

# Events the daemon keeps for `after=` replay, besides the current turn.
REPLAY_WINDOW = 2000


class Row(TypedDict):
    id: str
    seq: int
    kind: str
    role: str
    turn_id: str | None
    status: str
    parts: list[dict[str, Any]]
    created_at: str


class Event(TypedDict):
    seq: int
    conversation_uid: str
    type: str
    data: dict[str, Any]


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _row_problems(row: Any, where: str) -> list[str]:
    if not isinstance(row, dict):
        return [f"{where}: row is not an object"]
    problems = [
        f"{where}: row missing {key}" for key in Row.__annotations__ if key not in row
    ]
    if not _is_id(row.get("id")):
        problems.append(f"{where}: row id must be a non-empty string")
    if row.get("kind") not in ROW_KINDS:
        problems.append(f"{where}: unknown row kind {row.get('kind')!r}")
    if row.get("role") not in ROLES:
        problems.append(f"{where}: unknown role {row.get('role')!r}")
    if not isinstance(row.get("parts"), list):
        problems.append(f"{where}: parts must be a list")
    return problems


def event_problems(event: Any) -> list[str]:
    """Return the shape problems in one event; empty when it matches the contract."""
    if not isinstance(event, dict):
        return ["event is not an object"]
    seq = event.get("seq")
    where = f"seq {seq}"
    problems: list[str] = []
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        problems.append(f"{where}: seq must be a non-negative integer")
    if not _is_id(event.get("conversation_uid")):
        problems.append(f"{where}: conversation_uid missing")
    kind, data = event.get("type"), event.get("data")
    if kind not in EVENT_TYPES:
        return problems + [f"{where}: unknown event type {kind!r}"]
    if not isinstance(data, dict):
        return problems + [f"{where}: data must be an object"]
    if kind in ("snapshot", "replace"):
        rows = data.get("rows")
        if not isinstance(rows, list):
            problems.append(f"{where}: {kind} needs rows")
        else:
            for index, row in enumerate(rows):
                problems += _row_problems(row, f"{where} row {index}")
        if kind == "snapshot":
            if not isinstance(data.get("next_seq"), int):
                problems.append(f"{where}: snapshot needs next_seq")
            for key in ("turns", "inputs"):
                if not isinstance(data.get(key), list):
                    problems.append(f"{where}: snapshot needs {key}")
    elif kind == "row.upsert":
        problems += _row_problems(data, where)
    elif kind == "row.append":
        if (
            not _is_id(data.get("id"))
            or not isinstance(data.get("part"), int)
            or not isinstance(data.get("text"), str)
        ):
            problems.append(f"{where}: row.append needs id, part and text")
    elif kind == "row.delete":
        if not _is_id(data.get("id")):
            problems.append(f"{where}: row.delete needs id")
    elif kind == "input.state":
        problems += _input_problems(data, where)
    elif kind == "turn.state":
        if not _is_id(data.get("turn_id")):
            problems.append(f"{where}: turn.state needs turn_id")
        if data.get("state") not in TURN_STATES:
            problems.append(f"{where}: unknown turn state {data.get('state')!r}")
        if not isinstance(data.get("input_ids"), list):
            problems.append(f"{where}: turn.state needs input_ids")
    return problems


def _input_problems(info: Any, where: str) -> list[str]:
    if not isinstance(info, dict):
        return [f"{where}: input info is not an object"]
    problems = []
    if not _is_id(info.get("id")):
        problems.append(f"{where}: input.state needs id")
    state = info.get("state")
    if state not in INPUT_STATES:
        problems.append(f"{where}: unknown input state {state!r}")
    if state in ("started", "joined") and not _is_id(info.get("turn_id")):
        problems.append(f"{where}: a {state} input needs turn_id")
    if state == "ended" and info.get("outcome") not in OUTCOMES:
        problems.append(f"{where}: an ended input needs an outcome")
    return problems


def sequence_problems(events: Iterable[Any], *, complete: bool = True) -> list[str]:
    """Return contract problems across one stream, read from its first event.

    Checks what every client relies on: a snapshot comes first, seq strictly
    increases, every input row has a lifecycle under the same id (a row under a
    second id is how a sent message shows twice), an input ends once and never
    changes after, and a completed input ends only after its turn. With
    ``complete`` set, every input must also have ended by the last event.
    """
    events = list(events)
    problems: list[str] = []
    if (
        not events
        or not isinstance(events[0], dict)
        or events[0].get("type") != "snapshot"
    ):
        problems.append("a stream starts with a snapshot")
    input_rows: set[str] = set()
    rows: set[str] = set()
    states: dict[str, list[str]] = {}
    turn_of: dict[str, str] = {}
    ended_turns: set[str] = set()
    last_seq = -1

    def note_rows(new_rows: list[dict[str, Any]]) -> None:
        rows.clear()
        rows.update(row["id"] for row in new_rows)
        input_rows.update(row["id"] for row in new_rows if row["kind"] == "input")

    for event in events:
        shape = event_problems(event)
        if shape:
            problems += shape
            continue
        seq, kind, data = event["seq"], event["type"], event["data"]
        if seq <= last_seq:
            problems.append(f"seq {seq}: seq must increase (previous {last_seq})")
        last_seq = max(last_seq, seq)
        if kind == "snapshot":
            note_rows(data["rows"])
            for turn in data["turns"]:
                if turn.get("state") == "ended":
                    ended_turns.add(turn.get("turn_id"))
            for info in data["inputs"]:
                states.setdefault(info["id"], []).append(info["state"])
                if info.get("turn_id"):
                    turn_of[info["id"]] = info["turn_id"]
        elif kind == "replace":
            note_rows(data["rows"])
        elif kind == "row.upsert":
            rows.add(data["id"])
            if data["kind"] == "input":
                input_rows.add(data["id"])
        elif kind in ("row.append", "row.delete"):
            if data["id"] not in rows:
                problems.append(f"seq {seq}: {kind} for unknown row {data['id']}")
            elif kind == "row.delete":
                rows.discard(data["id"])
        elif kind == "input.state":
            seen = states.setdefault(data["id"], [])
            if "ended" in seen:
                problems.append(f"seq {seq}: input {data['id']} changed after it ended")
            seen.append(data["state"])
            if data.get("turn_id"):
                turn_of[data["id"]] = data["turn_id"]
            turn = turn_of.get(data["id"])
            if (
                data["state"] == "ended"
                and data["outcome"] == "completed"
                and turn
                and turn not in ended_turns
            ):
                problems.append(
                    f"seq {seq}: input {data['id']} completed before its turn {turn} ended"
                )
        elif kind == "turn.state" and data["state"] == "ended":
            ended_turns.add(data["turn_id"])

    for row_id in sorted(input_rows - set(states)):
        problems.append(
            f"input row {row_id} has no input.state: an input saved under a second id"
        )
    for input_id in sorted(set(states) - input_rows):
        problems.append(f"input {input_id} never got a row")
    for input_id, seen in sorted(states.items()):
        ends = seen.count("ended")
        if ends > 1:
            problems.append(f"input {input_id} ended {ends} times")
        elif complete and ends == 0:
            problems.append(f"input {input_id} never ended")
    return problems
