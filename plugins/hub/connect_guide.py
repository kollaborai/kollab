"""First-launch guided setup for the agent network (issue #121, Story 1).

Only the pure pieces live here: the words the guide shows, the once-per-machine
marker, and the gate that keeps it out of pipe mode, detached and spawned
agents and any launch without a terminal. The views are in
``plugins/altview/connect_altview.py``; the hub plugin runs the flow.

Nothing here touches a join code, a key or a relay address.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .device_names import display_name

# Machine-global, not per workspace: one answer covers every project. Tests set
# the variable so they never write the real marker.
MARKER_ENV = "KOLLAB_CONNECT_GUIDE_MARKER"
MARKER_NAME = "connect-guide-seen"
# How long the post-join line waits for the primary's name, and how often it looks.
JOIN_LINE_PATIENCE = 30.0
JOIN_LINE_POLL = 1.0
DEFAULT_DOMAIN = "kollabor.ai"

NOTICE_LINES = (
    "New: connect your agents across computers.",
    "Enter sets it up now; Esc for later (/connect any time).",
)
NO_NETWORK_LINE = "This computer is not on a network yet."
CHOICE_NEW_NETWORK = "Start a new network on kollabor.ai"
CHOICE_JOIN = "Join with a code"
CHOICES = (CHOICE_NEW_NETWORK, CHOICE_JOIN)

OTHER_COMPUTER_TITLE = "On your other computer"
OTHER_COMPUTER_STEPS = (
    "1) kollab --upgrade",
    "2) run kollab and press Enter on the same notice",
    "3) choose Join with a code and type the code",
)


# Why a join failed, by the fixed code the enrollment client reports. Display
# text only: nothing here can carry a code, a key or an id.
JOIN_FAILURE_REASONS = {
    "unavailable": "that code was not found, was already used, or has expired",
    "invalid_request": "that is not a valid join code",
    "bound": "that code is in use by another device",
    "claimed": "that code is in use by another device",
    "conflict": "this device is already on a network",
    "rate_limited": "too many attempts; wait a minute and try again",
    "capacity": "the relay is full; try again later",
    "backend_unavailable": "the relay is unavailable; try again later",
    "transport": "could not reach the relay; check the connection",
    "invalid_response": "the relay sent a reply this device could not verify",
    "internal": "this device hit an unexpected error; see the kollab log",
}


def join_failure_reason(code: Any) -> str:
    """One short line saying why a join failed, for a fixed failure code."""
    reason = JOIN_FAILURE_REASONS.get(code) if isinstance(code, str) else None
    return reason or "the relay did not accept the request"


def marker_path() -> Path:
    override = os.environ.get(MARKER_ENV)
    return Path(override) if override else Path.home() / ".kollab" / MARKER_NAME


def guide_seen() -> bool:
    return marker_path().exists()


def mark_guide_seen() -> None:
    """Record the answer. A failed write only means the notice shows again."""
    path = marker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass


def guide_applies(args: Any, *, interactive: bool) -> bool:
    """True for a person at a terminal who has not answered the notice yet.

    Pipe mode, detached and daemon agents (the daemon runs as ``--detached``,
    and so does every hub-spawned agent), a CLI query, the web UI, a ``--hub``
    command and any launch without a terminal never see it.
    """
    if not interactive or guide_seen():
        return False
    if any(getattr(args, flag, None) for flag in ("pipe", "detached", "query", "web_ui")):
        return False
    if getattr(args, "hub", None) is not None:
        return False
    return not os.environ.get("KOLLAB_PARENT_PID")  # set for spawned children


def post_join_line(primary: str = "") -> str:
    """The one message after a successful join. An empty `primary` names the
    code's issuer instead; `JoinLine` only falls back to that after a wait."""
    source = display_name(primary) if primary else "the device that issued the code"
    return (
        f"Settings arrive sealed from {source}. Run /login on this computer: "
        "a ChatGPT login does not travel."
    )


class JoinLine:
    """The post-join line of one join, said once.

    It names the primary as soon as this device knows the name (the join binds
    the issuer's, and the first sealed config carries it). Only after
    `patience` seconds without a name does it say "the device that issued the
    code" instead.
    """

    def __init__(
        self,
        primary_name: Callable[[], str],
        *,
        patience: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._primary_name = primary_name
        self._clock = clock
        self._deadline = clock() + (JOIN_LINE_PATIENCE if patience is None else patience)
        self.said = False

    def text(self) -> str:
        """The line, or "" while the name is still worth waiting for."""
        name = self._primary_name()
        if name or self._clock() >= self._deadline:
            return post_join_line(name)
        return ""

    async def say(
        self,
        emit: Callable[[str], None],
        *,
        poll: float | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        """Hand the line to `emit` once, as soon as there is one."""
        while not (line := self.text()):
            await sleep(JOIN_LINE_POLL if poll is None else poll)
        if not self.said:
            self.said = True
            emit(line)
