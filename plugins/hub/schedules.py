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

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import locked_file

from .device_names import parse_handle

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 1.0
WHEN_BUSY_CHOICES = ("queue", "new_instance")
RETRY_INITIAL_SECONDS = 30.0

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
        if not isinstance(data, dict):
            raise TypeError("a schedule row must be an object")
        agents = data.get("agents")
        if not isinstance(agents, list) or any(
            not isinstance(agent, str) for agent in agents
        ):
            raise ValueError("schedule agents must be a list of names")
        for field in ("id", "prompt", "when_busy", "skill", "created_by"):
            if field in data and not isinstance(data[field], str):
                raise ValueError(f"schedule {field} must be a string")
        active = data.get("active", True)
        if not isinstance(active, bool):
            raise ValueError("schedule active must be a boolean")
        numeric = {}
        for field, default in (
            ("interval_seconds", 0),
            ("anchor", 0),
            ("created_at", 0),
            ("updated_at", 0),
        ):
            value = data.get(field, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"schedule {field} must be a number")
            try:
                number = float(value)
            except (OverflowError, ValueError):
                raise ValueError(f"schedule {field} must be finite") from None
            if not math.isfinite(number):
                raise ValueError(f"schedule {field} must be finite")
            numeric[field] = number
        revision = data.get("revision", 1)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("schedule revision must be a positive integer")
        row = cls(
            id=data.get("id", ""),
            agents=list(agents),
            prompt=data.get("prompt", ""),
            interval_seconds=numeric["interval_seconds"],
            anchor=numeric["anchor"],
            active=active,
            when_busy=data.get("when_busy", "queue"),
            skill=data.get("skill", ""),
            created_by=data.get("created_by", ""),
            created_at=numeric["created_at"],
            updated_at=numeric["updated_at"],
            revision=revision,
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
        lock_path = self._path.with_name(self._path.name + ".lock")
        with locked_file(lock_path, "a", private=True):
            write_text_atomic(self._path, payload + "\n")


class RunLog:
    """Appends one JSON line per fire and reads the last run per schedule.

    The in-memory index is built on first use and kept current by every
    append. A process that starts later rebuilds it from the file, which is
    what makes missed grid points catch up after a restart.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._last: Optional[Dict[str, dict]] = None
        self._last_by_agent: Optional[Dict[Tuple[str, str], dict]] = None

    def append(self, record: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(self._path, "a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        if self._last is not None:
            self._last[str(record.get("id"))] = record
        if self._last_by_agent is not None:
            self._last_by_agent[(str(record.get("id")), str(record.get("agent")))] = record

    def _load_index(self) -> None:
        if self._last is not None:
            return
        last: Dict[str, dict] = {}
        by_agent: Dict[Tuple[str, str], dict] = {}
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            schedule_id = str(record.get("id"))
            agent = str(record.get("agent"))
            last[schedule_id] = record
            by_agent[(schedule_id, agent)] = record
        self._last = last
        self._last_by_agent = by_agent

    def last_run(self, schedule_id: str) -> Optional[dict]:
        self._load_index()
        assert self._last is not None
        return self._last.get(schedule_id)

    def last_run_for(self, schedule_id: str, agent: str) -> Optional[dict]:
        """The last attempt for one agent, including its pending retry data."""
        self._load_index()
        assert self._last_by_agent is not None
        return self._last_by_agent.get((schedule_id, agent))


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
        # (schedule id, agent) -> (retry time, original grid point, prior delay)
        self._retries: Dict[Tuple[str, str], Tuple[float, float, float]] = {}

    # -- ownership -----------------------------------------------------

    def _owned_agents(self, row: ScheduleRow) -> List[str]:
        """The row's agents that run on this device (requirement 7's Waiting).

        Only ``name@device`` identifies an owner. Bare or invalid names do not
        identify a device, so they stay Waiting instead of firing everywhere.
        With no rows owned here the schedule is never fired or deleted.
        """
        mine = _device_name(self._device_name)
        owned = []
        for agent in row.agents:
            handle = parse_handle(agent)
            if handle is None:
                logger.warning(
                    "schedules: %s has unqualified agent %r; leaving it Waiting",
                    row.id,
                    agent,
                )
                continue
            if handle[1] == mine:
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
                # The next grid point supersedes any pending retry from the
                # previous interval; this fire is the new scheduled attempt.
                for agent in owned:
                    self._retries.pop((row.id, agent), None)
                due_rows.append((row, owned, due))
            else:
                if upcoming is None or due < upcoming:
                    upcoming = due
                # Retry a failed agent before the next grid point. Other
                # agents in the same row keep their own delivery state.
                for agent in owned:
                    retry = self._retries.get((row.id, agent))
                    if retry is None:
                        last = self._runs.last_run_for(row.id, agent)
                        if (
                            last
                            and last.get("result") == "error"
                            and isinstance(last.get("retry_at"), (int, float))
                            and isinstance(last.get("retry_due"), (int, float))
                            and isinstance(last.get("retry_delay"), (int, float))
                        ):
                            retry = (
                                float(last["retry_at"]),
                                float(last["retry_due"]),
                                float(last["retry_delay"]),
                            )
                            self._retries[(row.id, agent)] = retry
                    if retry is None:
                        continue
                    retry_at, retry_due, _delay = retry
                    if retry_at >= due:
                        self._retries.pop((row.id, agent), None)
                    elif retry_at <= now:
                        due_rows.append((row, [agent], retry_due))
                    elif upcoming is None or retry_at < upcoming:
                        upcoming = retry_at

        for row, owned, due in due_rows:
            logger.info(
                "schedules: %s due at %.0f, firing for %s", row.id, due, owned
            )
            for agent in owned:
                fired_at = self._clock()
                try:
                    ok, error = await self._fire(row, agent, due, fired_at)
                except Exception as exc:  # a failing fire must not kill the timer
                    ok, error = False, f"fire failed ({type(exc).__name__})"
                    logger.warning("schedules: %s fire error: %s", row.id, exc)
                key = (row.id, agent)
                record = {
                    "id": row.id,
                    "agent": agent,
                    "due": due,
                    "fired_at": fired_at,
                    "result": "ok" if ok else "error",
                    "error": error or "",
                }
                if ok:
                    self._retries.pop(key, None)
                else:
                    previous = self._retries.get(key)
                    delay = (
                        RETRY_INITIAL_SECONDS
                        if previous is None
                        else previous[2] * 2
                    )
                    retry_at = fired_at + delay
                    next_grid = next_due_time(
                        row.anchor, row.interval_seconds, fired_at
                    )
                    if retry_at < next_grid:
                        self._retries[key] = (retry_at, due, delay)
                        record.update(
                            retry_at=retry_at,
                            retry_due=due,
                            retry_delay=delay,
                        )
                        if upcoming is None or retry_at < upcoming:
                            upcoming = retry_at
                    else:
                        self._retries.pop(key, None)
                self._runs.append(record)

                # A catch-up fire resumes on the first grid point after now,
                # while an on-time fire resumes at the following grid point.
                nxt = next_due_time(row.anchor, row.interval_seconds, fired_at)
                if upcoming is None or nxt < upcoming:
                    upcoming = nxt

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
