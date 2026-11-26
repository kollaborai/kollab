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


def outcome_for_stop_reason(stop_reason: Any) -> str:
    """Map provider terminal reasons to the one persisted input outcome."""
    reason = str(stop_reason or "").strip().lower()
    if reason in {"cancelled", "canceled", "interrupted", "stop"}:
        return "cancelled"
    if reason == "end_turn":
        return "completed"
    return "failed"
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
    generation: str
    type: str
    data: dict[str, Any]


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_count(value: Any) -> bool:
    """A non-negative integer; `True` is an int in Python, so it is ruled out."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_revision(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _one_of(value: Any, allowed: frozenset[str]) -> bool:
    """Membership that reports a wrong-typed value instead of raising on it (a list is unhashable)."""
    return isinstance(value, str) and value in allowed


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_presence(value: Any) -> bool:
    return value in ("online", "offline") if isinstance(value, str) else False


def _present(value: Any) -> bool:
    return True


def _preview_marker_problems(
    marker: Any,
    where: str,
    *,
    kind: str,
    row_id: Any,
    count_fields: tuple[str, ...],
    output_type: str,
    preview_required: bool,
) -> list[str]:
    if not isinstance(marker, dict):
        return [f"{where}: preview marker is not an object"]
    problems = []
    if marker.get("kind") != kind:
        problems.append(f"{where}: invalid preview marker kind")
    if not _is_id(row_id) or marker.get("row_id") != row_id:
        problems.append(f"{where}: preview marker row_id does not match its row")
    if "session_id" in marker and not _is_id(marker["session_id"]):
        problems.append(f"{where}: preview marker session_id must be a non-empty string")
    for key in count_fields:
        if not _is_count(marker.get(key)):
            problems.append(f"{where}: preview marker {key} must be a non-negative integer")
    if marker.get("output_type") != output_type:
        problems.append(f"{where}: preview marker has an invalid output_type")
    if not _is_revision(marker.get("output_revision")):
        problems.append(f"{where}: preview marker has an invalid output_revision")
    if preview_required and not _is_str(marker.get("preview")):
        problems.append(f"{where}: preview marker preview must be a string")
    return problems


def _content_preview_problems(
    marker: Any,
    where: str,
    *,
    row_id: Any,
    field: str | None,
) -> list[str]:
    marker_field = marker.get("field") if isinstance(marker, dict) else None
    valid_fields = {"text", "reasoning", "args", "part"}
    if field is None:
        field = (
            marker_field
            if isinstance(marker_field, str) and marker_field in valid_fields
            else "text"
        )
    problems = _preview_marker_problems(
        marker,
        where,
        kind="chat-stream-content-preview",
        row_id=row_id,
        count_fields=("part_index", "output_bytes", "output_chars"),
        output_type="json" if field in {"args", "part"} else "text",
        preview_required=True,
    )
    if not isinstance(marker_field, str) or marker_field not in valid_fields:
        problems.append(f"{where}: preview marker has an invalid field")
    if marker_field != field:
        problems.append(f"{where}: preview marker field does not match its part")
    return problems


def _part_preview_problems(marker: Any, where: str, *, row_id: Any) -> list[str]:
    return _preview_marker_problems(
        marker,
        where,
        kind="chat-stream-part-preview",
        row_id=row_id,
        count_fields=("part_index", "output_bytes", "output_chars"),
        output_type="json",
        preview_required=True,
    )


def _tool_output_preview_problems(
    marker: Any, where: str, *, row_id: Any, tool_call_id: Any
) -> list[str]:
    output_type = marker.get("output_type") if isinstance(marker, dict) else None
    output_type_valid = isinstance(output_type, str) and output_type in {
        "text",
        "json",
    }
    expected_output_type = output_type if output_type_valid else "text"
    problems = _preview_marker_problems(
        marker,
        where,
        kind="chat-stream-tool-output-preview",
        row_id=row_id,
        count_fields=("output_bytes", "output_chars"),
        output_type=expected_output_type,
        preview_required=True,
    )
    if not output_type_valid:
        problems.append(f"{where}: preview marker has an invalid output_type")
    if (
        not _is_id(tool_call_id)
        or not isinstance(marker, dict)
        or marker.get("tool_call_id") != tool_call_id
    ):
        problems.append(f"{where}: preview marker tool_call_id does not match its part")
    return problems


def _parts_preview_problems(marker: Any, where: str, *, row_id: Any) -> list[str]:
    return _preview_marker_problems(
        marker,
        where,
        kind="chat-stream-parts-preview",
        row_id=row_id,
        count_fields=("part_count", "output_bytes", "output_chars"),
        output_type="json",
        preview_required=False,
    )


# How far along its lifecycle each input state is: a state only moves forward.
_STAGE = {"queued": 0, "started": 1, "joined": 1, "ended": 2}

# Each ChatPart type: its required fields, then fields it may carry (absent or null when unused).
_PART_REQUIRED = {
    "text": {"text": _is_str},
    "reasoning": {"text": _is_str},
    "tool-call": {"tool_call_id": _is_id, "tool_name": _is_str, "args": _present},
    "image": {"url": _is_id},
    "prompt": {"prompt_id": _is_id, "data": _present},
    "hub-note": {"from": _is_id, "to": _is_str, "text": _is_str, "observed": _is_bool},
}
_PART_OPTIONAL = {
    "tool-call": {"is_error": _is_bool},
    "hub-note": {
        "kind": _is_str,
        "thread_id": _is_str,
        "reply_to": _is_str,
        "is_direct": _is_bool,
        "presence": _is_presence,
    },
}


def _part_problems(part: Any, where: str, *, row_id: Any) -> list[str]:
    if not isinstance(part, dict):
        return [f"{where}: part is not an object"]
    kind = part.get("type")
    if not isinstance(kind, str) or kind not in _PART_REQUIRED:
        return [f"{where}: unknown part type {kind!r}"]
    problems = [
        f"{where}: {kind} part needs a valid {key}"
        for key, check in _PART_REQUIRED[kind].items()
        if key not in part or not check(part[key])
    ]
    problems += [
        f"{where}: {kind} part has an invalid {key}"
        for key, check in _PART_OPTIONAL.get(kind, {}).items()
        if part.get(key) is not None and not check(part[key])
    ]
    content_preview = part.get("chat_stream_content_preview")
    part_preview = part.get("chat_stream_part_preview")
    if content_preview is not None:
        expected_field = {"text": "text", "reasoning": "reasoning"}.get(kind)
        problems += _content_preview_problems(
            content_preview,
            where,
            row_id=row_id,
            field=expected_field,
        )
    if part_preview is not None:
        problems += _part_preview_problems(part_preview, where, row_id=row_id)
    if kind == "tool-call":
        args_preview = part.get("chat_stream_args_preview")
        if args_preview is not None:
            problems += _content_preview_problems(
                args_preview, f"{where} args", row_id=row_id, field="args"
            )
        result_preview = part.get("chat_stream_result_preview")
        if result_preview is not None:
            problems += _tool_output_preview_problems(
                result_preview,
                f"{where} result",
                row_id=row_id,
                tool_call_id=part.get("tool_call_id"),
            )
    return problems


def _row_problems(row: Any, where: str) -> list[str]:
    if not isinstance(row, dict):
        return [f"{where}: row is not an object"]
    problems = [
        f"{where}: row missing {key}" for key in Row.__annotations__ if key not in row
    ]
    if not _is_id(row.get("id")):
        problems.append(f"{where}: row id must be a non-empty string")
    if not _one_of(row.get("kind"), ROW_KINDS):
        problems.append(f"{where}: unknown row kind {row.get('kind')!r}")
    if not _one_of(row.get("role"), ROLES):
        problems.append(f"{where}: unknown role {row.get('role')!r}")
    parts = row.get("parts")
    if not isinstance(parts, list):
        problems.append(f"{where}: parts must be a list")
    else:
        for index, part in enumerate(parts):
            problems += _part_problems(
                part, f"{where} part {index}", row_id=row.get("id")
            )
    parts_preview = row.get("chat_stream_parts_preview")
    if parts_preview is not None:
        problems += _parts_preview_problems(parts_preview, where, row_id=row.get("id"))
    if "seq" in row and not _is_count(row["seq"]):
        problems.append(f"{where}: row seq must be a non-negative integer")
    if "turn_id" in row and row["turn_id"] is not None and not _is_id(row["turn_id"]):
        problems.append(f"{where}: row turn_id must be a string or null")
    for key in ("status", "created_at"):
        if key in row and not isinstance(row[key], str):
            problems.append(f"{where}: row {key} must be a string")
    return problems


def _turn_problems(turn: Any, where: str) -> list[str]:
    if not isinstance(turn, dict):
        return [f"{where}: turn is not an object"]
    problems = []
    if not _is_id(turn.get("turn_id")):
        problems.append(f"{where}: turn needs turn_id")
    if not _one_of(turn.get("state"), TURN_STATES):
        problems.append(f"{where}: unknown turn state {turn.get('state')!r}")
    input_ids = turn.get("input_ids")
    if not isinstance(input_ids, list) or not all(_is_id(i) for i in input_ids):
        problems.append(f"{where}: turn needs input_ids, a list of ids")
    if "origin" in turn:
        origin = turn["origin"]
        if (not isinstance(origin, dict) or origin.get("kind") != "goal"
                or not _is_id(origin.get("goal_id")) or not _is_id(origin.get("attempt_id"))):
            problems.append(f"{where}: invalid turn origin")
    return problems


def event_problems(event: Any) -> list[str]:
    """Return the shape problems in one event; empty when it matches the contract."""
    if not isinstance(event, dict):
        return ["event is not an object"]
    seq = event.get("seq")
    where = f"seq {seq}"
    problems: list[str] = []
    if not _is_count(seq):
        problems.append(f"{where}: seq must be a non-negative integer")
    if not _is_id(event.get("conversation_uid")):
        problems.append(f"{where}: conversation_uid missing")
    if not _is_id(event.get("generation")):
        problems.append(f"{where}: generation missing")
    kind, data = event.get("type"), event.get("data")
    if not _one_of(kind, EVENT_TYPES):
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
            if not _is_count(data.get("next_seq")):
                problems.append(
                    f"{where}: snapshot needs next_seq, a non-negative integer"
                )
            older = data.get("older", 0)
            if older is not None and not _is_id(older):
                problems.append(
                    f"{where}: snapshot needs older, a cursor string or null"
                )
            for key, check in (("turns", _turn_problems), ("inputs", _input_problems)):
                entries = data.get(key)
                if not isinstance(entries, list):
                    problems.append(f"{where}: snapshot needs {key}")
                    continue
                for index, entry in enumerate(entries):
                    problems += check(entry, f"{where} {key[:-1]} {index}")
    elif kind == "row.upsert":
        problems += _row_problems(data, where)
    elif kind == "row.append":
        if (
            not _is_id(data.get("id"))
            or not _is_count(data.get("part"))
            or not isinstance(data.get("text"), str)
        ):
            problems.append(f"{where}: row.append needs id, part (an index) and text")
    elif kind == "row.delete":
        if not _is_id(data.get("id")):
            problems.append(f"{where}: row.delete needs id")
    elif kind == "input.state":
        problems += _input_problems(data, where)
    elif kind == "turn.state":
        problems += _turn_problems(data, where)
    return problems


def _input_problems(info: Any, where: str) -> list[str]:
    if not isinstance(info, dict):
        return [f"{where}: input info is not an object"]
    problems = []
    if not _is_id(info.get("id")):
        problems.append(f"{where}: input.state needs id")
    state = info.get("state")
    if not _one_of(state, INPUT_STATES):
        problems.append(f"{where}: unknown input state {state!r}")
    turn_id = info.get("turn_id")
    if "turn_id" not in info:
        problems.append(f"{where}: an input needs turn_id, a turn id or null")
    elif turn_id is not None and not _is_id(turn_id):
        problems.append(f"{where}: input turn_id must be a string or null")
    elif state in ("started", "joined") and turn_id is None:
        problems.append(f"{where}: a {state} input needs turn_id")
    if state == "ended":
        if not _one_of(info.get("outcome"), OUTCOMES):
            problems.append(f"{where}: an ended input needs an outcome")
    elif info.get("outcome") is not None:
        problems.append(f"{where}: only an ended input has an outcome")
    return problems


def sequence_problems(events: Iterable[Any], *, complete: bool = True) -> list[str]:
    """Return contract problems across one stream, read from its first event.

    Checks what every client relies on: a snapshot comes first, seq strictly
    increases with no gap (the event after a snapshot is its ``next_seq``, then
    each event is the one before plus one; a gap means an event was lost),
    every input row has a lifecycle under the same id (a row under a
    second id is how a sent message shows twice), an input's state only moves
    forward (queued, then started or joined, then ended), an input ends once
    and never changes after, and an input bound to a turn ends only after that
    turn ends, whatever its outcome. With ``complete`` set, every input must
    also have ended by the last event.
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
    expected: int | None = None

    def note_state(seq: Any, info: dict[str, Any]) -> None:
        seen = states.setdefault(info["id"], [])
        if seen and "ended" not in seen:
            last, state = seen[-1], info["state"]
            if state != last and _STAGE[state] <= _STAGE[last]:
                problems.append(
                    f"seq {seq}: input {info['id']} went from {last} to {state}"
                )
        seen.append(info["state"])
        if info.get("turn_id"):
            previous = turn_of.setdefault(info["id"], info["turn_id"])
            if previous != info["turn_id"]:
                problems.append(
                    f"seq {seq}: input {info['id']} changed turn from {previous} to {info['turn_id']}"
                )

    def note_rows(new_rows: list[dict[str, Any]]) -> None:
        rows.clear()
        rows.update(row["id"] for row in new_rows)
        input_rows.update(row["id"] for row in new_rows if row["kind"] == "input")

    conversation_uid = None
    generation = None
    for event in events:
        shape = event_problems(event)
        if shape:
            problems += shape
            if isinstance(event, dict) and _is_count(event.get("seq")):
                last_seq = max(last_seq, event["seq"])
                expected = last_seq + 1
            continue
        seq, kind, data = event["seq"], event["type"], event["data"]
        if conversation_uid is None:
            conversation_uid = event["conversation_uid"]
        elif event["conversation_uid"] != conversation_uid:
            problems.append(f"seq {seq}: conversation uid changed within stream")
        if generation is None:
            generation = event["generation"]
        elif event["generation"] != generation:
            problems.append(f"seq {seq}: daemon generation changed within stream")
        if seq <= last_seq:
            problems.append(f"seq {seq}: seq must increase (previous {last_seq})")
        elif expected is not None and seq != expected:
            problems.append(
                f"seq {seq}: expected seq {expected}; an event was lost, so the client resyncs"
            )
        last_seq = max(last_seq, seq)
        expected = data["next_seq"] if kind == "snapshot" else last_seq + 1
        if kind == "snapshot":
            note_rows(data["rows"])
            for turn in data["turns"]:
                if turn.get("state") == "ended":
                    ended_turns.add(turn.get("turn_id"))
            for info in data["inputs"]:
                note_state(seq, info)
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
            if "ended" in states.get(data["id"], []):
                problems.append(f"seq {seq}: input {data['id']} changed after it ended")
            note_state(seq, data)
            turn = turn_of.get(data["id"])
            if data["state"] == "ended" and turn and turn not in ended_turns:
                verb = "completed" if data["outcome"] == "completed" else "ended"
                problems.append(
                    f"seq {seq}: input {data['id']} {verb} before its turn {turn} ended"
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
