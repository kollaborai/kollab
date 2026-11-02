"""Hub work queue: orphan requeue and stale-item sweeping."""

import logging
import types

import pytest

from plugins.hub import coordinator
from plugins.hub.coordinator import WorkQueue


@pytest.fixture(autouse=True)
def _clear_terminal_cache():
    """The gh terminal cache is process-global; tests must not share it."""
    coordinator._TERMINAL_CACHE.clear()
    yield
    coordinator._TERMINAL_CACHE.clear()


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


def test_sweep_stale_retires_merged_pr_across_restart(tmp_path, monkeypatch, caplog):
    """Regression for issue #640: a queued item referencing an already
    merged PR is retired on the next coordinator sweep, with the reason
    persisted and logged."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    stale = queue.add("open DRAFT PR for feat/whatever @ merged in #219")

    with caplog.at_level(logging.INFO, logger="plugins.hub.coordinator"):
        retired = queue.sweep_stale(lambda ref: f"{ref} merged")

    assert retired == [(stale.id, "#219 merged")]

    # A fresh WorkQueue (the "restart") must not serve the slot again.
    fresh = WorkQueue()
    assert fresh.get_pending() == []
    slot = fresh._load()[0]
    assert slot.status == "retired"
    assert slot.retired_reason == "#219 merged"

    # And the retirement is logged with what and why.
    assert any(
        stale.id in r.getMessage() and "#219 merged" in r.getMessage()
        for r in caplog.records
    )


def test_sweep_stale_keeps_open_and_refless_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    open_pr = queue.add("work PR #101 still open")
    no_ref = queue.add("tidy the vault with no upstream at all")

    assert queue.sweep_stale(lambda ref: None if ref == "#101" else "gone") == []

    fresh = WorkQueue()
    assert [s.id for s in fresh.get_pending()] == [open_pr.id, no_ref.id]
    assert all(s.status == "pending" for s in fresh._load())


def test_sweep_stale_spares_assigned_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    claimed = queue.add("agent is actively on PR #219")
    assert queue.claim_by_id(claimed.id, "lapis")

    assert queue.sweep_stale(lambda ref: f"{ref} merged") == []
    slot = WorkQueue()._load()[0]
    assert slot.status == "assigned"
    assert slot.retired_reason is None


def test_sweep_stale_requires_every_ref_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    mixed = queue.add("fix #100 and #101 together")
    done = queue.add("both #100 and #102 landed")

    def checker(ref):
        return None if ref == "#101" else f"{ref} merged"

    assert queue.sweep_stale(checker) == [(done.id, "#100 merged; #102 merged")]
    assert [s.id for s in WorkQueue().get_pending()] == [mixed.id]


def test_sweep_stale_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    queue.add("done via #219")
    queue.sweep_stale(lambda ref: f"{ref} merged")

    saves = []
    monkeypatch.setattr(WorkQueue, "_save", lambda self, slots: saves.append(1))
    assert queue.sweep_stale(lambda ref: f"{ref} merged") == []
    assert saves == []


def test_sweep_stale_checker_exception_keeps_work(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    slot = queue.add("upstream is #219 but the checker is flaky")

    def boom(ref):
        raise RuntimeError("gh exploded")

    assert queue.sweep_stale(boom) == []
    fresh = WorkQueue()
    assert [s.id for s in fresh.get_pending()] == [slot.id]


def test_sweep_stale_skips_save_when_nothing_retired(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    queue.add("live work on #101")

    saves = []
    monkeypatch.setattr(WorkQueue, "_save", lambda self, slots: saves.append(1))
    assert queue.sweep_stale(lambda ref: None) == []
    assert saves == []


def _fake_gh(responses_by_argv_prefix):
    """Build a coordinator.subprocess stand-in with scripted gh answers."""

    class FakeCompleted:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout

    calls = []

    def fake_run(argv, capture_output, text, timeout):
        calls.append(list(argv))
        for prefix, answer in responses_by_argv_prefix.items():
            if argv[: len(prefix)] == list(prefix):
                rc, out = answer
                return FakeCompleted(rc, out)
        return FakeCompleted(1, "")

    return types.SimpleNamespace(run=fake_run), calls


def test_github_terminal_state_parses_gh_output(tmp_path, monkeypatch):
    fake, calls = _fake_gh(
        {
            ("gh", "pr", "view", "219"): (0, '{"state": "MERGED"}'),
            ("gh", "pr", "view", "220"): (0, '{"state": "CLOSED"}'),
            ("gh", "pr", "view", "221"): (0, '{"state": "OPEN"}'),
            ("gh", "pr", "view", "222"): (1, ""),
            ("gh", "issue", "view", "222"): (
                0,
                '{"state": "CLOSED", "stateReason": "COMPLETED"}',
            ),
            ("gh", "pr", "view", "223"): (1, ""),
            ("gh", "issue", "view", "223"): (0, '{"state": "OPEN"}'),
            ("gh", "pr", "view", "224"): (1, ""),
            ("gh", "issue", "view", "224"): (1, ""),
        }
    )
    monkeypatch.setattr(coordinator, "subprocess", fake)

    assert coordinator.github_terminal_state("#219") == "#219 merged"
    assert coordinator.github_terminal_state("#220") == "#220 closed"
    assert coordinator.github_terminal_state("#221") is None
    assert coordinator.github_terminal_state("#222") == "#222 issue closed (COMPLETED)"
    assert coordinator.github_terminal_state("#223") is None
    # gh missing/both views failing: unknown means keep.
    assert coordinator.github_terminal_state("#224") is None
    assert calls, "checker must query gh"


def test_github_terminal_state_caches_terminal_only(tmp_path, monkeypatch):
    fake, calls = _fake_gh(
        {
            ("gh", "pr", "view", "219"): (0, '{"state": "MERGED"}'),
            ("gh", "pr", "view", "221"): (0, '{"state": "OPEN"}'),
        }
    )
    monkeypatch.setattr(coordinator, "subprocess", fake)

    assert coordinator.github_terminal_state("#219") == "#219 merged"
    assert coordinator.github_terminal_state("#219") == "#219 merged"
    assert len([c for c in calls if c[3] == "219"]) == 1  # terminal cached

    assert coordinator.github_terminal_state("#221") is None
    assert coordinator.github_terminal_state("#221") is None
    assert len([c for c in calls if c[3] == "221"]) == 2  # open re-checked
