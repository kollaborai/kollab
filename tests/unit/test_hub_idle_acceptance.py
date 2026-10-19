"""Issue #211 acceptance: open hub work never sits idle past ten minutes.

Deterministic simulation of the idle supervisor's decision loop on a fake
clock. No sleeps, no hub socket: each pass is evaluate() on a real
TaskLedger rooted in tmp_path, with the supervisor state carried between
passes the way the poll loop carries it.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.supervisor import (
    DEFAULT_STALL_THRESHOLD,
    DEFAULT_WAKE_COOLDOWN,
    SupervisorState,
    evaluate,
)
from plugins.hub.task_ledger import TaskLedger

POLL_SECONDS = 30
TEN_MINUTES = 600


@pytest.fixture
def tasks_dir(tmp_path):
    return str(tmp_path / "tasks")


def _roster(worker_state="working"):
    return [
        {
            "identity": "koordinator",
            "state": "idle",
            "is_coordinator": True,
            "project": "proj",
        },
        {
            "identity": "lapis",
            "state": worker_state,
            "is_coordinator": False,
            "project": "proj",
        },
    ]


def _run_passes(ledger, roster, start, seconds, state):
    """Run evaluate every POLL_SECONDS; return [(t, decision)] per decision."""
    events = []
    t = 0.0
    while t <= seconds:
        decisions, state = evaluate(ledger, roster, now=start + t, state=state)
        events.extend((t, d) for d in decisions)
        t += POLL_SECONDS
    return events, state


def test_open_work_never_idles_past_ten_minutes(tasks_dir):
    """Acceptance: open ledger work, idle coordinator, no operator nudge.

    The coordinator must be woken at the first pass and then again at
    least every wake cooldown, so no gap between wakes reaches ten minutes
    across a full hour.
    """
    ledger = TaskLedger(tasks_dir=tasks_dir)
    ledger.enqueue(assigner="peridot", directive="ship the thing")
    start = time.time()

    events, _ = _run_passes(ledger, _roster("idle"), start, 3600, SupervisorState())

    wake_times = [t for t, d in events if d.kind == "wake_coordinator"]
    assert wake_times, "open work never woke the coordinator"
    assert wake_times[0] <= POLL_SECONDS
    gaps = [b - a for a, b in zip(wake_times, wake_times[1:])]
    assert max(gaps) <= TEN_MINUTES
    assert max(gaps) <= DEFAULT_WAKE_COOLDOWN + POLL_SECONDS
    assert wake_times[-1] >= 3600 - TEN_MINUTES


def test_stalled_claim_is_nudged_then_requeued_and_wakes_coordinator(tasks_dir):
    """A silent worker gets one nudge at the stall threshold, then its card
    goes back to the open queue and the coordinator is woken to reassign."""
    ledger = TaskLedger(tasks_dir=tasks_dir)
    card = ledger.enqueue(assigner="peridot", directive="long task")
    ledger.claim(card.id, "lapis")
    start = time.time()

    events, _ = _run_passes(ledger, _roster("working"), start, 1800, SupervisorState())

    nudges = [t for t, d in events if d.kind == "nudge_worker"]
    requeues = [t for t, d in events if d.kind == "requeue_stalled"]
    assert len(nudges) == 1
    assert nudges[0] >= DEFAULT_STALL_THRESHOLD
    assert len(requeues) == 1
    assert requeues[0] > nudges[0]
    assert ledger.get(card.id).status == "open"
    assert ledger.get(card.id).assignee in ("", None)

    wake_after_requeue = [
        t for t, d in events if d.kind == "wake_coordinator" and t >= requeues[0]
    ]
    assert wake_after_requeue, "requeued card did not wake the coordinator"
    assert wake_after_requeue[0] - requeues[0] <= TEN_MINUTES


def test_restart_recovers_open_and_stalled_work(tasks_dir):
    """Kill the hub mid-stall: a fresh ledger and fresh supervisor state
    must still see the open card, the stalled claim and the wake."""
    first = TaskLedger(tasks_dir=tasks_dir)
    open_card = first.enqueue(assigner="peridot", directive="queued while down")
    stalled = first.enqueue(assigner="peridot", directive="was running")
    first.claim(stalled.id, "lapis")
    first._mutate_locked(
        stalled.id,
        lambda c: setattr(c, "last_activity_at", time.time() - 2000) or True,
    )

    restarted = TaskLedger(tasks_dir=tasks_dir)
    assert restarted.get(open_card.id).status == "open"
    assert restarted.get(stalled.id).assignee == "lapis"

    now = time.time()
    decisions, _ = evaluate(
        restarted, _roster("working"), now=now, state=SupervisorState()
    )
    kinds = [d.kind for d in decisions]
    assert "nudge_worker" in kinds
    wakes = [d for d in decisions if d.kind == "wake_coordinator"]
    assert len(wakes) == 1
    assert open_card.id in wakes[0].text
