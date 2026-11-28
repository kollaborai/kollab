"""Schedule delivery, stage 2 (#612, PR #653): start, queue, new instance.

The timer in ``schedules.py`` decides *when* a schedule fires; this module
decides *how* one fire reaches one agent on this device:

* the agent is not running: it is started detached through
  ``kollabor.binary.kollab_argv()`` (never ``sys.executable -m``) with the
  fire's prompt as its initial message, so the very first turn is the run;
* the agent is running and idle: the prompt goes to its socket as a manual
  wake (content heuristics may not park a schedule fire);
* the agent is running and busy: ``when_busy`` picks. ``queue`` sends the
  same message -- the target's busy path holds it until the current turn
  ends, then runs it in the same chat. ``new_instance`` starts a second
  process of the same identity just for this run (``exit_after_turn``: it
  leaves when the run is done) and leaves the original chat alone.

Every launched or sent message carries the schedule id (``[schedule <id>]``
prefix and ``schedule_id`` metadata) so replies and run records line up.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# A started agent has this long to come online before the fire is failed.
ONLINE_TIMEOUT_SECONDS = 45.0
BUSY_STATES = {"working", "booting"}


@dataclass
class LaunchSpec:
    """One detached kollab start, built here, executed by the plugin."""

    argv: List[str]
    identity: str
    initial_message: str
    exit_after_turn: bool
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class DeliveryProbes:
    """The live facts delivery needs; fakes stand in for unit tests."""

    # agent name -> its live unix socket path on this machine (None = off)
    find_socket: Callable[[str], Optional[str]]
    # agent name -> presence state ("idle", "working", ...)
    state_of: Callable[[str], str]
    # run a LaunchSpec; True once the agent came online. Blocking (it polls
    # for the socket); deliver runs it in a thread.
    launch: Callable[[LaunchSpec], bool]
    # deliver a message to a live agent; True when the target acked.
    # Gets the agent name and the socket find_socket returned.
    send: Callable[[str, Optional[str], str, Dict[str, Any]], Awaitable[bool]]


def _local_name(agent: str) -> str:
    """``name@device`` -> ``name``; a bare name stays itself.

    Delivery only ever runs for agents this device owns, so the device part
    (when present) names this machine.
    """
    return agent.split("@", 1)[0]


def _fire_content(row, due: float) -> str:
    return f"[schedule {row.id}] {row.prompt}"


def build_launch_spec(row, agent: str, due: float, *, exit_after_turn: bool) -> LaunchSpec:
    """The detached start for one fire: kollab_argv, never sys.executable -m."""
    from kollabor.binary import kollab_argv

    argv = list(kollab_argv())
    argv.extend(["--detached", "--as", _local_name(agent)])
    env: Dict[str, str] = {}
    if exit_after_turn:
        # The app watches for the initial turn to settle and then exits, so
        # a new-instance run exists only for that run.
        env["KOLLAB_EXIT_AFTER_INITIAL_TURN"] = "1"
    return LaunchSpec(
        argv=argv,
        identity=_local_name(agent),
        initial_message=_fire_content(row, due),
        exit_after_turn=exit_after_turn,
        env=env,
    )


async def deliver(row, agent: str, due: float, now: float, probes: DeliveryProbes) -> Tuple[bool, str]:
    """Deliver one fire to one owned agent. (ok, error)."""
    name = _local_name(agent)
    socket = probes.find_socket(name)

    if socket is None:
        spec = build_launch_spec(row, agent, due, exit_after_turn=False)
        if not await asyncio.to_thread(probes.launch, spec):
            return False, f"{name} did not come online within {ONLINE_TIMEOUT_SECONDS:.0f}s"
        logger.info("schedules: %s started %s for the fire", row.id, name)
        return True, ""

    if probes.state_of(name) in BUSY_STATES and row.when_busy == "new_instance":
        spec = build_launch_spec(row, agent, due, exit_after_turn=True)
        if not await asyncio.to_thread(probes.launch, spec):
            return False, f"{name} run instance did not come online within {ONLINE_TIMEOUT_SECONDS:.0f}s"
        logger.info(
            "schedules: %s started a run instance of %s (busy, new_instance)",
            row.id,
            name,
        )
        return True, ""

    content = _fire_content(row, due)
    metadata = {"schedule_id": row.id, "manual_wake": True}
    if not await probes.send(name, socket, content, metadata):
        return False, f"{name} did not accept the message"
    logger.info("schedules: %s delivered to %s", row.id, name)
    return True, ""
