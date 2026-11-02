"""Hub work queue: work held by a dead agent must go back to pending."""

import json
import logging
import subprocess

import pytest

from plugins.hub import coordinator
from plugins.hub.coordinator import WorkQueue, WorkSlot, github_upstream_check


def test_requeue_orphans_saves_the_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    dead = queue.add("task held by a dead agent")
    alive = queue.add("task held by a live agent")
    assert queue.claim_by_id(dead.id, "peridot")
    assert queue.claim_by_id(alive.id, "lapis")

    assert queue.requeue_orphans({"lapis"}) == [(dead.id, "peridot")]

    # A fresh load sees the reset, so the next pass has nothing to redo.
    assert [s.id for s in WorkQueue().get_pending()] == [dead.id]
    assert queue.requeue_orphans({"lapis"}) == []


# --- issue #640: boot-time sweep of dead queue slots ---


def _store_slots(tmp_path, slots):
    (tmp_path / "work-queue.json").write_text(
        json.dumps({"slots": [s.to_dict() for s in slots]})
    )


def test_sweep_retires_completed_slots_and_keeps_refless_pending(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    done = WorkSlot(task="merged long ago", status="completed")
    live = WorkSlot(task="still vague, no refs", status="pending")
    _store_slots(tmp_path, [done, live])

    retired = WorkQueue().sweep(upstream_check=lambda n: pytest.fail("no refs"))

    assert [s.id for s, _ in retired] == [done.id]
    assert "completed" in retired[0][1]
    assert [s.id for s in WorkQueue().get_all()] == [live.id]


def test_sweep_retires_pending_slot_whose_upstream_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    slot = WorkSlot(task="PR-sweep lane 1 — kollaborai/kollab PR #358", status="pending")
    _store_slots(tmp_path, [slot])
    checked = []

    retired = WorkQueue().sweep(
        upstream_check=lambda n: (checked.append(n) or "closed")
    )

    assert checked == [358]
    assert retired == [(slot, "upstream closed/merged: #358")]
    assert WorkQueue().get_all() == []


def test_sweep_keeps_pending_slot_when_upstream_open(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    slot = WorkSlot(task="fix dispatch durability #636", status="pending")
    _store_slots(tmp_path, [slot])

    retired = WorkQueue().sweep(upstream_check=lambda n: "open")

    assert retired == []
    assert [s.id for s in WorkQueue().get_all()] == [slot.id]


def test_sweep_fails_open_on_unknown_upstream_state(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    slot = WorkSlot(task="check #42 eventually", status="pending")
    _store_slots(tmp_path, [slot])

    retired = WorkQueue().sweep(upstream_check=lambda n: None)

    assert retired == []
    assert [s.id for s in WorkQueue().get_all()] == [slot.id]


def test_sweep_fails_open_when_checker_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    slot = WorkSlot(task="boom on #7", status="pending")
    _store_slots(tmp_path, [slot])

    def explode(number):
        raise RuntimeError("gh exploded")

    retired = WorkQueue().sweep(upstream_check=explode)

    assert retired == []
    assert [s.id for s in WorkQueue().get_all()] == [slot.id]


def test_sweep_never_touches_assigned_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    slot = WorkSlot(
        task="finish PR #210", status="assigned", assigned_to="lapis"
    )
    _store_slots(tmp_path, [slot])

    retired = WorkQueue().sweep(upstream_check=lambda n: "closed")

    assert retired == []
    assert [s.id for s in WorkQueue().get_all()] == [slot.id]


def test_sweep_requires_every_reference_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    slot = WorkSlot(
        task="ship the #219 wave, context mentions #220 follow-up",
        context="also unblocks #641",
        status="pending",
    )
    _store_slots(tmp_path, [slot])
    states = {219: "closed", 220: "closed", 641: "open"}

    retired = WorkQueue().sweep(upstream_check=lambda n: states[n])

    assert retired == []
    assert [s.id for s in WorkQueue().get_all()] == [slot.id]


def test_sweep_checks_each_unique_number_once(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    first = WorkSlot(task="lane for #5", status="pending")
    second = WorkSlot(task="same ref again #5", context="see #5 in context", status="pending")
    _store_slots(tmp_path, [first, second])
    checked = []

    WorkQueue().sweep(upstream_check=lambda n: (checked.append(n) or "open"))

    assert checked == [5]


def test_sweep_check_budget_bounds_upstream_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    refs = " ".join(f"#{n}" for n in range(100, 125))
    slot = WorkSlot(task=refs, status="pending")
    _store_slots(tmp_path, [slot])
    checked = []

    retired = WorkQueue().sweep(
        upstream_check=lambda n: (checked.append(n) or "open"),
        max_checks=3,
    )

    assert len(checked) == 3
    assert retired == []
    assert [s.id for s in WorkQueue().get_all()] == [slot.id]


def test_sweep_empty_queue_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)

    assert WorkQueue().sweep(upstream_check=lambda n: "closed") == []
    assert not (tmp_path / "work-queue.json").exists()


def test_restart_retires_stale_entries_with_log(tmp_path, monkeypatch, caplog):
    """Issue #640 acceptance: stale entries die on restart, live ones stay."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    stale = WorkSlot(task="merge PR #219", status="completed")
    merged_ref = WorkSlot(task="ship the #219/#220 wave", status="pending")
    open_ref = WorkSlot(task="investigate #641", status="pending")
    assigned = WorkSlot(
        task="finish PR #210", status="assigned", assigned_to="lapis"
    )
    _store_slots(tmp_path, [stale, merged_ref, open_ref, assigned])
    states = {219: "closed", 220: "closed", 641: "open", 210: "open"}

    with caplog.at_level(logging.INFO, logger="plugins.hub.coordinator"):
        retired = WorkQueue().sweep(upstream_check=lambda n: states[n])

    assert {s.id for s, _ in retired} == {stale.id, merged_ref.id}
    assert any(
        "upstream closed/merged: #219, #220" in why for _, why in retired
    )
    survivors = {s.id for s in WorkQueue().get_all()}
    assert survivors == {open_ref.id, assigned.id}
    assert any("Swept work slot" in rec.message for rec in caplog.records)


def test_sweep_without_retirement_rewrites_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    slot = WorkSlot(task="live #640", status="pending")
    _store_slots(tmp_path, [slot])
    path = tmp_path / "work-queue.json"
    before = path.read_text()

    WorkQueue().sweep(upstream_check=lambda n: "open")

    assert path.read_text() == before


def test_github_upstream_check_parses_gh_api_states(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="closed\n", stderr="")

    monkeypatch.setattr(coordinator.subprocess, "run", fake_run)
    check = github_upstream_check()

    assert check(219) == "closed"
    argv, kwargs = calls[0]
    assert argv[:4] == ["gh", "api", "repos/kollaborai/kollab/issues/219", "--jq"]
    assert argv[-1] == ".state"
    assert kwargs["timeout"] == coordinator.UPSTREAM_CHECK_TIMEOUT_SECS


def test_github_upstream_check_fails_open_on_errors(monkeypatch):
    def failing_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(coordinator.subprocess, "run", failing_run)
    assert github_upstream_check()(1) is None

    def timeout_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=5)

    monkeypatch.setattr(coordinator.subprocess, "run", timeout_run)
    assert github_upstream_check()(1) is None

    monkeypatch.setattr(
        coordinator.subprocess, "run", lambda argv, **k: (_ for _ in ()).throw(OSError)
    )
    assert github_upstream_check()(1) is None

    def weird_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="merging?", stderr="")

    monkeypatch.setattr(coordinator.subprocess, "run", weird_run)
    assert github_upstream_check()(1) is None
