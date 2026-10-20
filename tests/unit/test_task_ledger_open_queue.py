"""Durable open queue on the TaskLedger (issue #211).

Open cards are unassigned work that survives hub restarts; claims are
atomic under the card's advisory file lock; stall detection and requeue
close the "agent silently died with work" hole.
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.task_ledger import TaskCard, TaskLedger


@pytest.fixture
def ledger(tmp_path):
    return TaskLedger(tasks_dir=str(tmp_path / "tasks"))


def test_enqueue_creates_open_card_without_assignee(ledger):
    card = ledger.enqueue(assigner="peridot", directive="do the thing")
    assert card.status == "open"
    assert card.assignee == ""
    assert card.report_to == "peridot"
    # Open cards are immediately visible to the supervisor's pool.
    assert [c.id for c in ledger.get_open()] == [card.id]


def test_enqueue_requires_assigner_and_directive(ledger):
    with pytest.raises(ValueError):
        ledger.enqueue(assigner="", directive="x")
    with pytest.raises(ValueError):
        ledger.enqueue(assigner="peridot", directive="  ")


def test_claim_assigns_and_activates(ledger):
    card = ledger.enqueue(assigner="peridot", directive="build it")
    claimed = ledger.claim(card.id, "lapis")
    assert claimed is not None
    assert claimed.status == "active"
    assert claimed.assignee == "lapis"
    assert claimed.last_activity_at > 0
    assert ledger.get_open() == []
    assert [c.id for c in ledger.get_active_for("lapis")] == [card.id]


def test_claim_race_first_wins(ledger):
    """Two claimers race one open card under the file lock: exactly one wins."""
    card = ledger.enqueue(assigner="peridot", directive="one slot")
    first = ledger.claim(card.id, "lapis")
    second = ledger.claim(card.id, "zircon")
    assert first is not None
    assert second is None
    assert ledger.get(card.id).assignee == "lapis"


def test_claim_next_open_takes_highest_priority(ledger):
    low = ledger.enqueue(assigner="a", directive="low", priority=1)
    high = ledger.enqueue(assigner="a", directive="high", priority=9)
    claimed = ledger.claim_next_open("lapis")
    assert claimed.id == high.id
    remaining = ledger.get_open()
    assert [c.id for c in remaining] == [low.id]


def test_project_scoped_claim(ledger):
    elsewhere = ledger.enqueue(assigner="a", directive="other", project="other-proj")
    assert ledger.claim_next_open("lapis", project="myproj") is None
    assert ledger.claim(elsewhere.id, "lapis", project="myproj") is None
    assert ledger.claim(elsewhere.id, "lapis", project="other-proj") is not None


def test_ledger_durability_open_cards_survive_restart(ledger, tmp_path):
    """Acceptance: restart recovery — open queue state is on disk, so a
    fresh TaskLedger over the same dir sees the same open cards."""
    ledger.enqueue(assigner="peridot", directive="survives restarts")
    ledger.enqueue(assigner="peridot", directive="also survives", priority=2)

    fresh = TaskLedger(tasks_dir=str(ledger._tasks_dir))
    reopened = fresh.get_open()
    assert len(reopened) == 2
    assert {c.directive for c in reopened} == {
        "survives restarts",
        "also survives",
    }
    assert fresh.claim_next_open("lapis").directive == "survives restarts"


def test_ledger_durability_active_claim_survives_restart(ledger):
    card = ledger.enqueue(assigner="peridot", directive="half done")
    ledger.claim(card.id, "lapis")
    ledger.checkpoint(card.id, "halfway through")

    fresh = TaskLedger(tasks_dir=str(ledger._tasks_dir))
    got = fresh.get(card.id)
    assert got.assignee == "lapis"
    assert got.status == "active"
    assert got.last_activity_at > 0
    assert got.last_checkpoint_note() == "halfway through"


def test_get_stalled_uses_activity_freshness(ledger):
    card = ledger.enqueue(assigner="peridot", directive="stalled work")
    ledger.claim(card.id, "lapis")
    now = time.time()

    # Just claimed: not stalled.
    assert ledger.get_stalled(900, now=now) == []

    # Simulate silence past the threshold.
    def _backdate(c):
        c.last_activity_at = now - 1200
        return True

    ledger._mutate_locked(card.id, _backdate)
    stalled = ledger.get_stalled(900, now=now)
    assert [c.id for c in stalled] == [card.id]

    # A checkpoint revives the card.
    ledger.checkpoint(card.id, "still alive")
    assert ledger.get_stalled(900, now=now) == []


def test_get_stalled_ignores_standby_open_and_terminal(ledger):
    active = ledger.enqueue(assigner="a", directive="real work")
    ledger.claim(active.id, "lapis")
    open_card = ledger.enqueue(assigner="a", directive="waiting work")
    now = time.time()
    ledger._mutate_locked(
        active.id, lambda c: setattr(c, "last_activity_at", now - 5000) or True
    )
    assert [c.id for c in ledger.get_stalled(900, now=now)] == [active.id]
    assert open_card.status == "open"


def test_requeue_stalled_returns_card_to_open_pool(ledger):
    card = ledger.enqueue(assigner="peridot", directive="took too long")
    ledger.claim(card.id, "lapis")
    now = time.time()
    ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 2000) or True
    )

    requeued = ledger.requeue_stalled(card.id, reason="stalled past nudge grace")
    assert requeued.status == "open"
    assert requeued.assignee == ""
    assert requeued.stall_count == 1
    assert requeued.reassignments == 1
    assert requeued.cron_active is False
    assert [c.id for c in ledger.get_open()] == [card.id]

    # The previous holder is on the audit trail.
    assert any("was lapis" in cp["note"] for cp in requeued.checkpoints)

    # Someone else can pick it up.
    assert ledger.claim(card.id, "zircon") is not None


def test_requeue_stalled_is_noop_for_open_and_terminal(ledger):
    card = ledger.enqueue(assigner="a", directive="never claimed")
    assert ledger.requeue_stalled(card.id) is None
    done = ledger.enqueue(assigner="a", directive="finished")
    ledger.claim(done.id, "lapis")
    fresh = ledger._mutate_locked(
        done.id, lambda c: setattr(c, "status", "done") or True
    )
    assert fresh.status == "done"
    assert ledger.requeue_stalled(done.id) is None


def test_checkpoint_updates_activity_and_audit(ledger):
    card = ledger.enqueue(assigner="a", directive="quiet work")
    ledger.claim(card.id, "lapis")
    before = ledger.get(card.id).last_activity_at
    time.sleep(0.01)
    assert ledger.checkpoint(card.id, "still working") is True
    after = ledger.get(card.id).last_activity_at
    assert after > before
    assert ledger.get(card.id).checkpoints[-1]["note"] == "still working"


def test_legacy_cards_without_activity_field_still_stall(ledger, tmp_path):
    """A card written before last_activity_at existed must not be
    invisible to stall detection: from_dict defaults keep it detectable
    via the updated_at fallback."""
    card = ledger.enqueue(assigner="a", directive="old card")
    ledger.claim(card.id, "lapis")
    now = time.time()
    path = ledger._task_path(card.id)
    data = json.loads(path.read_text())
    data.pop("last_activity_at", None)
    data["updated_at"] = now - 3000
    path.write_text(json.dumps(data))

    fresh = TaskLedger(tasks_dir=str(ledger._tasks_dir))
    stalled = fresh.get_stalled(900, now=now)
    assert [c.id for c in stalled] == [card.id]


def test_task_card_roundtrip_keeps_supervisor_fields(ledger):
    card = ledger.enqueue(assigner="a", directive="fields")
    ledger.claim(card.id, "lapis")
    ledger.requeue_stalled(card.id, reason="test")
    fresh = TaskLedger(tasks_dir=str(ledger._tasks_dir)).get(card.id)
    assert fresh.stall_count == 1
    assert fresh.reassignments == 1
    assert fresh.last_activity_at == 0.0
    assert TaskCard.from_dict(fresh.to_dict()).stall_count == 1
