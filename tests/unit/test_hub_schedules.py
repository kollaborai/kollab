"""Persistent schedules, stage 1: storage + grid timer (#612, PR #653).

Schedules live in ``~/.kollab/schedules.json`` and survive restarts
(requirement 4). Fire times sit on the grid ``anchor + k * interval`` with no
drift, the timer sleeps until the next due time instead of polling
(requirement 8), every fire is recorded as a run (requirement 9), and a
device that was off fires exactly once on return, then continues (requirement
7, timer side). These tests use a fake clock; no real sleep exceeds 0.1 s.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.schedules import (  # noqa: E402
    RunLog,
    ScheduleRow,
    ScheduleStore,
    ScheduleTimer,
    next_due_time,
)


class FakeClock:
    """Callable clock the tests advance by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _row(**overrides) -> ScheduleRow:
    base = dict(
        id="s1",
        agents=["lapis"],
        prompt="check the queue",
        interval_seconds=600.0,
        anchor=1000.0,
    )
    base.update(overrides)
    return ScheduleRow(**base)


# ---------------------------------------------------------------- grid math


def test_next_due_time_walks_the_grid_from_the_anchor():
    # First point is the anchor itself; then anchor + k * interval.
    assert next_due_time(1000.0, 600.0, after=999.0) == 1000.0
    assert next_due_time(1000.0, 600.0, after=1000.0) == 1600.0
    assert next_due_time(1000.0, 600.0, after=1601.5) == 2200.0


def test_next_due_time_ignores_drift_in_last_fire():
    # A fire that happened late (1610, not 1600) still picks the next grid
    # point, never now + interval.
    assert next_due_time(1000.0, 600.0, after=1610.0) == 2200.0


# ------------------------------------------------------------------ storage


def test_store_roundtrip_survives_restart(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.json")
    rows = [
        _row(),
        _row(
            id="s2",
            agents=["infra@home-server", "lapis"],
            prompt="report disk usage",
            skill="ops",
            interval_seconds=3600.0,
            anchor=2000.0,
            active=False,
            when_busy="new_instance",
            created_by="marco",
        ),
    ]
    store.save(rows)

    # A fresh process reads the same rows back (requirement 4).
    reloaded = ScheduleStore(tmp_path / "schedules.json").load()
    assert [r.id for r in reloaded] == ["s1", "s2"]
    assert reloaded[1].agents == ["infra@home-server", "lapis"]
    assert reloaded[1].skill == "ops"
    assert reloaded[1].active is False
    assert reloaded[1].when_busy == "new_instance"
    assert reloaded[1].created_by == "marco"
    assert reloaded[1].revision >= 1


def test_store_defaults_fill_optional_fields(tmp_path):
    path = tmp_path / "schedules.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "handmade",
                    "agents": ["lapis"],
                    "prompt": "p",
                    "interval_seconds": 60.0,
                    "anchor": 5.0,
                }
            ]
        ),
        encoding="utf-8",
    )
    row = ScheduleStore(path).load()[0]
    assert row.active is True
    assert row.when_busy == "queue"
    assert row.skill == ""


def test_store_skips_broken_rows_but_keeps_the_rest(tmp_path):
    path = tmp_path / "schedules.json"
    path.write_text(
        json.dumps(
            [
                {"id": "no-agents", "prompt": "p", "interval_seconds": 60, "anchor": 5},
                {"id": "bad-interval", "agents": ["a"], "prompt": "p", "interval_seconds": 0, "anchor": 5},
                {"id": "bad-when-busy", "agents": ["a"], "prompt": "p",
                 "interval_seconds": 60, "anchor": 5, "when_busy": "explode"},
                _row(id="fine").__dict__,
            ]
        ),
        encoding="utf-8",
    )
    rows = ScheduleStore(path).load()
    assert [r.id for r in rows] == ["fine"]


def test_store_missing_file_is_empty(tmp_path):
    assert ScheduleStore(tmp_path / "nope.json").load() == []


# ----------------------------------------------------------------- run log


def test_run_log_records_every_fire_and_reads_last_run_back(tmp_path):
    log = RunLog(tmp_path / "schedules-runs.jsonl")
    log.append({"id": "s1", "agent": "lapis", "due": 1000.0, "fired_at": 1000.1, "result": "ok", "error": ""})
    log.append({"id": "s1", "agent": "lapis", "due": 1600.0, "fired_at": 1600.2, "result": "error", "error": "refused"})
    log.append({"id": "s2", "agent": "infra@home", "due": 1600.0, "fired_at": 1600.3, "result": "ok", "error": ""})

    # A fresh process (new RunLog) still sees the last run per schedule.
    fresh = RunLog(tmp_path / "schedules-runs.jsonl")
    assert fresh.last_run("s1")["due"] == 1600.0
    assert fresh.last_run("s1")["result"] == "error"
    assert fresh.last_run("s2")["agent"] == "infra@home"
    assert fresh.last_run("s3") is None


# ------------------------------------------------------------------- timer


class Recorder:
    """Fire callback + fake clock the tests inspect."""

    def __init__(self) -> None:
        self.fires: list[tuple[str, str, float]] = []

    async def __call__(self, row, agent, due, now):
        self.fires.append((row.id, agent, due))
        return True, ""


def _timer(tmp_path, clock, fires, device_name="mac", rows=None):
    store = ScheduleStore(tmp_path / "schedules.json")
    if rows is not None:
        store.save(rows)
    return ScheduleTimer(
        store,
        RunLog(tmp_path / "schedules-runs.jsonl"),
        fires,
        device_name=device_name,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_timer_fires_on_the_grid_without_drift(tmp_path):
    clock = FakeClock(1000.0)
    fires = Recorder()
    timer = _timer(tmp_path, clock, fires, rows=[_row()])

    # t=1000: the anchor is due right now.
    delay = await timer.pass_due()
    assert fires.fires == [("s1", "lapis", 1000.0)]
    assert delay == pytest.approx(600.0)

    # The fire landed a little late; the next due time is still on the grid,
    # so the remaining wait shrinks by exactly the lateness.
    clock.advance(605.0)
    delay = await timer.pass_due()
    assert fires.fires[-1] == ("s1", "lapis", 1600.0)
    assert delay == pytest.approx(595.0)

    clock.advance(600.0)
    await timer.pass_due()
    assert [f[2] for f in fires.fires] == [1000.0, 1600.0, 2200.0]


@pytest.mark.asyncio
async def test_timer_sleeps_until_next_due_and_returns_none_when_empty(tmp_path):
    clock = FakeClock(1200.0)
    fires = Recorder()
    timer = _timer(tmp_path, clock, fires, rows=[_row(anchor=1600.0)])

    # Next grid point is 1600; nothing is due yet.
    assert await timer.pass_due() == pytest.approx(400.0)
    assert fires.fires == []

    # With no schedules at all there is nothing to wait for.
    timer._store.save([])
    assert await timer.pass_due() is None


@pytest.mark.asyncio
async def test_timer_restart_fires_catchup_once_then_continues(tmp_path):
    clock = FakeClock(1000.0)
    fires = Recorder()
    rows = [_row()]
    first = _timer(tmp_path, clock, fires, rows=rows)
    await first.pass_due()  # anchor fired at t=1000, recorded to the run log

    # Two grid points (1600, 2200) pass while the device is off.
    clock.advance(1400.0)  # now = 2400

    # Back online: a fresh timer (new process, same files) fires exactly once.
    timer = _timer(tmp_path, clock, fires, rows=rows)
    await timer.pass_due()
    assert len(fires.fires) == 2  # the anchor fire plus exactly one catch-up
    assert fires.fires[-1][2] == 1600.0  # stands in for the earliest missed point

    # The run record says it fired late but for the missed grid point.
    record = timer._runs.last_run("s1")
    assert record["due"] == 1600.0
    assert record["fired_at"] == 2400.0

    # Next due continues from the first grid point after now: 2800, not 3000.
    delay = await timer.pass_due()
    assert delay == pytest.approx(400.0)

    # No burst: another pass with no time passed fires nothing new.
    assert await timer.pass_due() == pytest.approx(400.0)
    assert len(fires.fires) == 2


@pytest.mark.asyncio
async def test_timer_ignores_other_devices_schedules_and_keeps_them(tmp_path):
    clock = FakeClock(1000.0)
    fires = Recorder()
    rows = [_row(id="remote", agents=["infra@home-server"], anchor=1000.0)]
    timer = _timer(tmp_path, clock, fires, device_name="mac", rows=rows)

    assert await timer.pass_due() is None
    assert fires.fires == []
    # Waiting, never deleted: the schedule is still in the store.
    remaining = ScheduleStore(tmp_path / "schedules.json").load()
    assert [r.id for r in remaining] == ["remote"]


@pytest.mark.asyncio
async def test_timer_fires_only_owned_agents_of_a_mixed_row(tmp_path):
    clock = FakeClock(1000.0)
    fires = Recorder()
    rows = [_row(agents=["infra@home-server", "lapis@mac"])]
    timer = _timer(tmp_path, clock, fires, device_name="home-server", rows=rows)

    await timer.pass_due()
    assert fires.fires == [("s1", "infra@home-server", 1000.0)]


@pytest.mark.asyncio
async def test_timer_skips_paused_schedules(tmp_path):
    clock = FakeClock(1000.0)
    fires = Recorder()
    timer = _timer(tmp_path, clock, fires, rows=[_row(active=False)])

    assert await timer.pass_due() is None
    assert fires.fires == []


@pytest.mark.asyncio
async def test_timer_records_errors_from_the_fire_callback(tmp_path):
    clock = FakeClock(1000.0)

    async def failing(row, agent, due, now):
        return False, "refused"

    store = ScheduleStore(tmp_path / "schedules.json")
    store.save([_row()])
    timer = ScheduleTimer(
        store,
        RunLog(tmp_path / "schedules-runs.jsonl"),
        failing,
        device_name="mac",
        clock=clock,
    )

    await timer.pass_due()
    record = timer._runs.last_run("s1")
    assert record["result"] == "error"
    assert record["error"] == "refused"

    # A failed fire still counts as fired: the next try is the next grid
    # point, not an immediate retry loop.
    assert await timer.pass_due() == pytest.approx(600.0)


@pytest.mark.asyncio
async def test_timer_run_wakes_early_on_wake_call(tmp_path):
    # Real production sleep with a 0.05 s grid: wake() must interrupt it.
    clock = FakeClock()
    fires = Recorder()
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save([_row(interval_seconds=50.0, anchor=clock.now + 50.0)])
    timer = ScheduleTimer(
        store,
        RunLog(tmp_path / "schedules-runs.jsonl"),
        fires,
        device_name="mac",
        clock=clock,
    )

    task = asyncio.create_task(timer.run())
    await asyncio.sleep(0.02)
    assert fires.fires == []
    store.save([_row(interval_seconds=50.0, anchor=clock.now)])  # due now
    timer.wake()  # a file change recomputes the wait
    await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert fires.fires == [("s1", "lapis", pytest.approx(clock.now))]
