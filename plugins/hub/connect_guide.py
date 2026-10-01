"""First-launch guided setup for the agent network (issue #121, Story 1).

Only the pure pieces live here: the words the guide shows, the once-per-machine
marker, and the gate that keeps it out of pipe mode, detached and spawned
agents and any launch without a terminal. The views are in
``plugins/altview/connect_altview.py``; the hub plugin runs the flow.

Nothing here touches a join code, a key or a relay address.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .device_names import display_name

# Machine-global, not per workspace: one answer covers every project. Tests set
# the variable so they never write the real marker.
MARKER_ENV = "KOLLAB_CONNECT_GUIDE_MARKER"
MARKER_NAME = "connect-guide-seen"
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
    "1) kollab --upgrade (or pip install -U kollab)",
    "2) run kollab and press Enter on the same notice",
    "3) choose Join with a code and type the code",
)


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
    """The one message after a successful join. `primary` is empty until its
    first sealed config has landed, and the line names the code's issuer then."""
    source = display_name(primary) if primary else "the device that issued the code"
    return (
        f"settings arrive sealed from {source}, and a ChatGPT login does not "
        "travel: run /login on this computer."
    )
