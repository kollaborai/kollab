"""Persistent schedules, stage 1: storage and grid timer (#612, PR #653).

A schedule sends a prompt to one or more agents every ``interval_seconds``.
This module owns the parts that must not depend on delivery (stage 2), sync
(stage 3), engine routes (stage 4) or any UI (stage 5):

* storage: ``~/.kollab/schedules.json``, one row per schedule, written
  atomically under ``kollabor_config.file_lock`` so restarts keep them
  (requirement 4);
* run records: ``~/.kollab/schedules-runs.jsonl``, one line per fire; the
  last line per schedule is the Last run a card shows (requirement 9);
* the grid timer: fire times sit on ``anchor + k * interval_seconds`` and
  never drift, and the timer sleeps until the earliest due time instead of
  polling (requirement 8);
* catch-up: grid points missed while this device was off produce exactly one
  fire on return, then the schedule continues from the next grid point after
  now (requirement 7, timer side). A schedule whose agents belong to another
  device is never fired here and never deleted: it is Waiting, and that
  device's coordinator fires it.

One process per device runs the timer: the hub coordinator (the ``flock``
holder on ``hub.lock``), wired in ``plugins/hub/plugin.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, Union

from kollabor_config.file_lock import locked_file

from .device_names import parse_handle

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 1.0
WHEN_BUSY_CHOICES = ("queue", "new_instance")

# fire(row, agent, due, now) -> (ok, error). `due` is the grid point the fire
# stands for; `now` is when it actually went out.
FireFn = Callable[["ScheduleRow", str, float, float], Awaitable[Tuple[bool, str]]]
DeviceName = Union[str, Callable[[], str]]


def _device_name(value: DeviceName) -> str:
    return value() if callable(value) else value


def next_due_time(anchor: float, interval: float, after: float) -> float:
    """First grid point ``anchor + k * interval`` (k >= 0) after ``after``.

    The grid is fixed: a fire that happened late still lands the next fire on
    the next grid point, never on ``last_fire + interval`` (no drift).
    """
    if interval <= 0:
        raise ValueError(f"interval must be positive, got {interval}")
    k = math.floor((after - anchor) / interval) + 1
    return anchor + max(0, k) * interval


@dataclass
class ScheduleRow:
    """One schedule as stored in ``schedules.json``."""

    id: str
    agents: List[str]  # "name" (this device) or "name@device"
    prompt: str
    interval_seconds: float
    anchor: float  # epoch of the first grid point
    active: bool = True
    when_busy: str = "queue"  # queue | new_instance (delivery picks, stage 2)
    skill: str = ""
    created_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    revision: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleRow":
        row = cls(
            id=str(data.get("id", "")),
            agents=[str(a) for a in data.get("agents") or []],
            prompt=str(data.get("prompt", "")),
            interval_seconds=float(data.get("interval_seconds", 0)),
            anchor=float(data.get("anchor", 0)),
            active=bool(data.get("active", True)),
            when_busy=str(data.get("when_busy", "queue")),
            skill=str(data.get("skill", "")),
            created_by=str(data.get("created_by", "")),
            created_at=float(data.get("created_at", 0)),
            updated_at=float(data.get("updated_at", 0)),
            revision=int(data.get("revision", 1)),
        )
        if not row.id or not row.agents or not row.prompt:
            raise ValueError("a schedule needs id, agents and prompt")
        if row.interval_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval under {MIN_INTERVAL_SECONDS}s")
        if row.when_busy not in WHEN_BUSY_CHOICES:
            raise ValueError(f"when_busy must be one of {WHEN_BUSY_CHOICES}")
        return row

    def to_dict(self) -> dict:
        return asdict(self)


class ScheduleStore:
    """Reads and writes ``schedules.json`` (one file, one writer per device)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> List[ScheduleRow]:
        """Every valid row; a broken row is skipped with a warning."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.warning("schedules: could not read %s (%s)", self._path, exc)
            return []
        rows: List[ScheduleRow] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                rows.append(ScheduleRow.from_dict(item))
            except (TypeError, ValueError) as exc:
                logger.warning("schedules: skipping broken row %r (%s)", item, exc)
        return rows

    def save(self, rows: List[ScheduleRow]) -> None:
        """Replace the file's contents, atomically under the file lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([row.to_dict() for row in rows], indent=2)
        with locked_file(self._path, "w") as handle:
            handle.write(payload + "\n")


class RunLog:
    """Appends one JSON line per fire and reads the last run per schedule.

    The in-memory index is built on first use and kept current by every
    append. A process that starts later rebuilds it from the file, which is
    what makes missed grid points catch up after a restart.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._last: Optional[Dict[str, dict]] = None

    def append(self, record: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(self._path, "a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        if self._last is not None:
            self._last[str(record.get("id"))] = record

    def last_run(self, schedule_id: str) -> Optional[dict]:
        if self._last is None:
            index: Dict[str, dict] = {}
            try:
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        index[str(record.get("id"))] = record
            except OSError:
                pass
            self._last = index
        return self._last.get(schedule_id)


class ScheduleTimer:
    """Fires due schedules on their grid; sleeps between due times.

    ``pass_due`` is one deterministic pass (fire what is due, return the
    seconds until the earliest next due time, or None when nothing is
    scheduled on this device); ``run`` wraps it in the sleep-until loop with
    an early ``wake`` for file changes. No fixed-interval polling.
    """

    def __init__(
        self,
        store: ScheduleStore,
        runs: RunLog,
        fire: FireFn,
        *,
        device_name: DeviceName = "",
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        import time

        self._store = store
        self._runs = runs
        self._fire = fire
        self._device_name = device_name
        self._clock = clock or time.time
        self._wake = asyncio.Event()

    # -- ownership -----------------------------------------------------

    def _owned_agents(self, row: ScheduleRow) -> List[str]:
        """The row's agents that run on this device (requirement 7's Waiting).

        A bare name is this device's agent; ``name@device`` belongs to that
        device only. With no rows owned here the schedule is Waiting and is
        never fired or deleted.
        """
        mine = _device_name(self._device_name)
        owned = []
        for agent in row.agents:
            handle = parse_handle(agent)
            if handle is None or handle[1] == mine:
                owned.append(agent)
        return owned

    # -- one deterministic pass ------------------------------------------

    async def pass_due(self) -> Optional[float]:
        """Fire every owned, active, due schedule once.

        Returns the seconds until the earliest next due time, or None when no
        owned schedule is active. A catch-up fire records the earliest missed
        grid point as ``due``; the next due time is then the first grid point
        after now, so missed points never burst.
        """
        now = self._clock()
        upcoming: Optional[float] = None
        due_rows: List[Tuple[ScheduleRow, List[str], float]] = []
        rows = self._store.load()
        logger.info(
            "schedules: pass at %.0f: %d row(s) loaded", now, len(rows)
        )
        for row in rows:
            if not row.active:
                continue
            owned = self._owned_agents(row)
            if not owned:
                continue  # Waiting: another device fires it
            try:
                after = self._after(row)
                due = next_due_time(row.anchor, row.interval_seconds, after)
            except ValueError:
                logger.warning("schedules: %s skipped: bad interval", row.id)
                continue
            if due <= now:
                due_rows.append((row, owned, due))
            elif upcoming is None or due < upcoming:
                upcoming = due

        for row, owned, due in due_rows:
            logger.info(
                "schedules: %s due at %.0f, firing for %s", row.id, due, owned
            )
            # A fired row owes its next grid point too, so the timer knows
            # when to wake up again.
            nxt = due + row.interval_seconds
            if upcoming is None or nxt < upcoming:
                upcoming = nxt
            for agent in owned:
                fired_at = self._clock()
                try:
                    ok, error = await self._fire(row, agent, due, fired_at)
                except Exception as exc:  # a failing fire must not kill the timer
                    ok, error = False, f"fire failed ({type(exc).__name__})"
                    logger.warning("schedules: %s fire error: %s", row.id, exc)
                self._runs.append(
                    {
                        "id": row.id,
                        "agent": agent,
                        "due": due,
                        "fired_at": fired_at,
                        "result": "ok" if ok else "error",
                        "error": error or "",
                    }
                )

        if upcoming is None:
            return None
        return max(0.0, upcoming - self._clock())

    def _after(self, row: ScheduleRow) -> float:
        """Where the grid search resumes: the last actual fire (or the anchor).

        Using fired_at (not the recorded due) is what makes catch-up fire
        once: after a late catch-up at time `now`, the next grid point is the
        first one after `now`, not the next missed one.
        """
        last = self._runs.last_run(row.id)
        if last and isinstance(last.get("fired_at"), (int, float)):
            return float(last["fired_at"])
        # No fire yet: the anchor itself is the first candidate.
        return row.anchor - row.interval_seconds

    # -- the sleeping loop ------------------------------------------------

    def wake(self) -> None:
        """Recompute the wait early: the file changed (edit or sync)."""
        self._wake.set()

    async def run(self) -> None:
        while True:
            self._wake.clear()
            delay = await self.pass_due()
            if delay is None:
                await self._wake.wait()
            else:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
