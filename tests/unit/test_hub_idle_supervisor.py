"""Idle supervisor decision logic (issue #211).

The acceptance criterion under test: a hub with open ledger items never
stays idle past the wake cooldown without an operator nudge. evaluate()
is pure except for requeue writes it performs on the ledger so the wake
text always matches disk state.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.supervisor import (
    DEFAULT_MAX_REASSIGNMENTS,
    SupervisorState,
    evaluate,
)
from plugins.hub.task_ledger import TaskLedger


@pytest.fixture
def ledger(tmp_path):
    return TaskLedger(tasks_dir=str(tmp_path / "tasks"))


def _roster(*agents):
    """agents: (identity, state, is_coordinator) tuples."""
    return [
        {
            "identity": identity,
            "state": state,
            "is_coordinator": is_coord,
            "project": "proj",
        }
        for identity, state, is_coord in agents
    ]


def _coord(state="idle"):
    return ("koordinator", state, True)


def _worker(identity="lapis", state="idle"):
    return (identity, state, False)


def test_open_card_wakes_idle_coordinator(ledger):
    """Acceptance: open work + idle coordinator -> wake within one pass."""
    ledger.enqueue(assigner="peridot", directive="queued work")
    decisions, _ = evaluate(
        ledger,
        _roster(_coord(), _worker()),
        now=time.time(),
    )
    wakes = [d for d in decisions if d.kind == "wake_coordinator"]
    assert len(wakes) == 1
    assert wakes[0].worker == "koordinator"
    assert "queued work" in wakes[0].text
    assert "[idle-supervisor]" in wakes[0].text


def test_requeued_card_counts_as_open_work(ledger):
    """Acceptance: a card that stalled and was requeued still triggers the
    wake — work did not vanish because the worker died."""
    card = ledger.enqueue(assigner="peridot", directive="orphaned work")
    ledger.claim(card.id, "lapis")
    now = time.time()
    ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 2000) or True
    )
    decisions, _ = evaluate(
        ledger,
        _roster(_coord()),  # worker lapis offline
        now=now,
    )
    kinds = [d.kind for d in decisions]
    assert "requeue_stalled" in kinds
    assert "wake_coordinator" in kinds
    assert ledger.get_open() != []  # back on the durable pool


def test_wake_cooldown_suppresses_repeat_wakes(ledger):
    ledger.enqueue(assigner="peridot", directive="work")
    now = time.time()
    roster = _roster(_coord())

    decisions, state = evaluate(ledger, roster, now=now)
    assert [d.kind for d in decisions] == ["wake_coordinator"]

    # Same pass conditions 10s later: within cooldown, no second wake.
    decisions2, state = evaluate(ledger, roster, now=now + 10, state=state)
    assert [d.kind for d in decisions2] == []

    # After the cooldown the open card wakes the coordinator again — a hub
    # with open work can never go quiet for longer than the cooldown.
    decisions3, _ = evaluate(ledger, roster, now=now + 121, state=state)
    assert [d.kind for d in decisions3] == ["wake_coordinator"]


def test_no_coordinator_on_roster_yields_no_wake_but_stalls_still_requeue(ledger):
    ledger.enqueue(assigner="peridot", directive="nobody to wake")
    now = time.time()
    decisions, _ = evaluate(ledger, _roster(_worker()), now=now)
    assert [d.kind for d in decisions] == []


def test_busy_coordinator_is_still_wakeable_target(ledger):
    """A 'working' coordinator is not idle, but it is the only one who can
    assign — the wake re-invokes it rather than skipping assignment."""
    ledger.enqueue(assigner="peridot", directive="work")
    decisions, _ = evaluate(
        ledger,
        _roster(_coord("working"), _worker()),
        now=time.time(),
    )
    wakes = [d for d in decisions if d.kind == "wake_coordinator"]
    assert len(wakes) == 1


def test_idle_workers_listed_in_wake_text(ledger):
    ledger.enqueue(assigner="peridot", directive="assign me")
    decisions, _ = evaluate(
        ledger,
        _roster(_coord(), _worker("lapis"), _worker("zircon", "working")),
        now=time.time(),
    )
    text = decisions[0].text
    assert "idle workers: lapis" in text
    assert "zircon" not in text.split("idle workers:")[1].splitlines()[0]


def test_stalled_active_card_gets_one_nudge_first(ledger):
    card = ledger.enqueue(assigner="peridot", directive="slow worker")
    ledger.claim(card.id, "lapis")
    now = time.time()
    ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 1000) or True
    )
    decisions, state = evaluate(
        ledger,
        _roster(_coord(), _worker("lapis", "working")),
        now=now,
    )
    nudges = [d for d in decisions if d.kind == "nudge_worker"]
    assert len(nudges) == 1
    assert nudges[0].worker == "lapis"
    assert nudges[0].card_id == card.id
    # Not requeued yet — the nudge is the grace window.
    assert ledger.get(card.id).status == "active"
    assert nudges[0].kind == "nudge_worker"


def test_stalled_card_requeued_after_nudge_grace(ledger):
    card = ledger.enqueue(assigner="peridot", directive="silent worker")
    ledger.claim(card.id, "lapis")
    now = time.time()
    ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 1000) or True
    )
    _, state = evaluate(ledger, _roster(_coord(), _worker("lapis", "working")), now=now)

    # Same window: nudge already recorded, still stalled -> requeue now.
    decisions, state = evaluate(
        ledger,
        _roster(_coord(), _worker("lapis", "working")),
        now=now + 5,
        state=state,
    )
    assert any(d.kind == "requeue_stalled" for d in decisions)
    fresh = ledger.get(card.id)
    assert fresh.status == "open"
    assert fresh.assignee == ""


def test_offline_worker_skips_nudge_and_requeues_immediately(ledger):
    card = ledger.enqueue(assigner="peridot", directive="dead worker")
    ledger.claim(card.id, "lapis")
    now = time.time()
    ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 1000) or True
    )
    decisions, _ = evaluate(ledger, _roster(_coord()), now=now)  # lapis not on roster
    kinds = [d.kind for d in decisions]
    assert "nudge_worker" not in kinds
    assert "requeue_stalled" in kinds
    assert "wake_coordinator" in kinds


def test_checkpoint_resets_stall_and_clears_nudge_record(ledger):
    card = ledger.enqueue(assigner="peridot", directive="recovering")
    ledger.claim(card.id, "lapis")
    now = time.time()
    ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 1000) or True
    )
    _, state = evaluate(ledger, _roster(_coord(), _worker("lapis", "working")), now=now)
    assert card.id in state.nudges  # nudge recorded

    # Worker checks in: freshness resets, nudge record cleared.
    ledger.checkpoint(card.id, "made progress")
    decisions, state = evaluate(
        ledger,
        _roster(_coord(), _worker("lapis", "working")),
        now=now + 5,
        state=state,
    )
    assert decisions == []
    assert card.id not in state.nudges


def test_max_reassignments_blocks_endless_churn(ledger):
    card = ledger.enqueue(assigner="peridot", directive="hot potato")
    for _ in range(DEFAULT_MAX_REASSIGNMENTS):
        ledger.claim(card.id, "lapis")
        now = time.time()
        ledger._mutate_locked(
            card.id, lambda c: setattr(c, "last_activity_at", now - 1000) or True
        )
        ledger.requeue_stalled(card.id, reason="cycle")
    assert ledger.get(card.id).reassignments == DEFAULT_MAX_REASSIGNMENTS

    # Stall once more: this time it is blocked, not requeued again.
    now = time.time()
    ledger.claim(card.id, "zircon")
    ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 1000) or True
    )
    decisions, _ = evaluate(ledger, _roster(_coord()), now=now)
    assert any(d.kind == "requeue_stalled" for d in decisions) is False
    assert ledger.get(card.id).status == "active"
    # ...but the coordinator still gets woken: blocked work is open work.
    assert any(d.kind == "wake_coordinator" for d in decisions)
    assert "blocked at max reassignments" in next(
        d.text for d in decisions if d.kind == "wake_coordinator"
    )


def test_empty_ledger_is_silent(ledger):
    decisions, _ = evaluate(ledger, _roster(_coord(), _worker()), now=time.time())
    assert decisions == []


def test_supervisor_state_is_carried_not_recreated(ledger):
    state = SupervisorState(last_wake=123.0)
    ledger.enqueue(assigner="a", directive="w")
    _, out = evaluate(
        ledger, _roster(_coord()), now=200.0, state=state
    )  # within cooldown of last_wake=123? no: 200-123=77 < 120 -> suppressed
    assert [d.kind for d in out and []] == []
