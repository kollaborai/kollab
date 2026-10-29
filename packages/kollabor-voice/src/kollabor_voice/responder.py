"""Fast voice responder: model choice and what it knows of the running turn.

What it knows is what the human sees: each tool's name and state ("terminal:
running for 40 s"), never a command, a path, file contents or tool output (#388).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

RESPONDER_KEY = "kollabor.voice.responder_model"
MAX_EVENTS = 8
# The display tap events a turn is made of (others, like status lines, are not).
TURN_EVENTS = {"token", "response", "message", "tool_start", "tool_result"}

ASIDE_PROMPT = (
    "You answer the user out loud while their coding agent keeps working. You cannot "
    "run tools or change anything, and the activity below is all you know about the "
    "work.\n"
    "Reply with exactly one line:\n"
    "ANSWER <one or two short spoken sentences> when they ask what the agent is doing "
    "or how it is going, or just chat.\n"
    "FORWARD <one short spoken sentence saying you passed it on> when they ask the "
    "agent to do, change or stop something, or ask what the activity cannot answer.\n"
    "Never invent files, results or progress the activity does not show."
)
# A live GLM reply wrote "FORWARD — I've passed it on": dashes go with the colon.
_VERDICT = re.compile(r"(ANSWER|FORWARD)\b[\s:*\-–—]*(.*)", re.IGNORECASE)


def responder_model(config: Any) -> Optional[str]:
    """Quick-model loadout name for voice replies, or None to use the session model.

    A loadout name carries its own provider profile (endpoint and credentials),
    so the quick model may run on a different provider than the chat — an
    OpenRouter fast model while the chat itself stays where it is. Resolution
    is the /llm one: an explicit loadout, else an implicit per-provider model.
    No kollabor.llm.fast_model fallback: that key holds a bare model id for
    compaction and dreaming, which is not a loadout name.
    """
    if config is None:
        return None
    value = config.get(RESPONDER_KEY, "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def running_turn(events: Iterable[dict]) -> list[dict]:
    """The display tap events since the last turn_complete."""
    turn: list[dict] = []
    for event in events:
        if event.get("type") == "turn_complete":
            turn = []
        else:
            turn.append(event)
    return turn


def tool_entries(events: Iterable[dict], now: float) -> list[tuple[str, str, int]]:
    """(tool name, "running" | "done" | "failed", seconds) per tool call, oldest first.

    Only the name is read: a command's text can carry a token (a git URL, a curl
    header), so no tool's arguments ever reach the Quick Model.
    """
    calls: list[list[Any]] = []  # [name, started, ended or None, succeeded]
    running: dict[str, list[Any]] = {}
    for event in events:
        kind = event.get("type")
        when = float(event.get("ts") or now)
        if kind == "tool_start":
            call = [str(event.get("tool_name") or "a tool")[:40], when, None, False]
            calls.append(call)
            running[str(event.get("tool_id") or "")] = call
        elif kind == "tool_result":
            call = running.pop(str(event.get("tool_id") or ""), None)
            if call is not None:
                call[2], call[3] = when, bool(event.get("success"))
    entries = []
    for name, began, ended, ok in calls:
        if ended is None:
            entries.append((name, "running", round(now - began)))
        else:
            entries.append((name, "done" if ok else "failed", round(ended - began)))
    return entries


def _activity(name: str, state: str, secs: int) -> str:
    if state == "running":
        return f"- {name}: running for {secs} s"
    return f"- {name}: {state} in {secs} s"


def aside_snapshot(events: Iterable[dict], now: float, limit: int = MAX_EVENTS) -> str:
    """The running turn as the Quick Model reads it: how long so far, its newest tools."""
    turn = running_turn(events)
    times = [
        float(e["ts"]) for e in turn if e.get("type") in TURN_EVENTS and e.get("ts")
    ]
    began = min(times, default=now)
    lines = [_activity(*call) for call in tool_entries(turn, now)[-limit:]]
    header = f"The agent's turn has been running for about {round(now - began)} s."
    return "\n".join([header, *(lines or ["- no tool has run yet in this turn"])])


def local_line(events: Iterable[dict], now: float) -> str:
    """The newest tool said plainly, for when the Quick Model gives no answer."""
    calls = tool_entries(running_turn(events), now)
    if not calls:
        return "I'm still working on it. I'll pass that on."
    name, state, secs = calls[-1]
    name = name.replace("_", " ")
    if state == "running":
        unit = "second" if secs == 1 else "seconds"
        return (
            f"Still working: {name} has been running for {secs} {unit}. "
            "I'll pass that on."
        )
    done = "finished" if state == "done" else "failed"
    return f"Still working: {name} just {done}. I'll pass that on."


def parse_aside(text: Optional[str]) -> Optional[tuple[str, str]]:
    """("ANSWER" or "FORWARD", what to say) from the reply's first line, else None."""
    for line in (text or "").splitlines():
        line = line.strip().lstrip("*\"'`")
        if not line:
            continue
        match = _VERDICT.match(line)
        said = match.group(2).strip().strip("*\"'`").strip()[:300] if match else ""
        return (match.group(1).upper(), said) if said else None
    return None
