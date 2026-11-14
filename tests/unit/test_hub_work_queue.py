"""Hub work queue: orphan requeue and stale-item sweeping."""

import json
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
    stale = queue.add(
        "open DRAFT PR for feat/whatever @ merged in #219", upstream_refs=["#219"]
    )

    with caplog.at_level(logging.INFO, logger="plugins.hub.coordinator"):
        retired = queue.sweep_stale(lambda ref: f"{ref} merged")

    assert [(slot.id, slot.retired_reason) for slot in retired] == [
        (stale.id, "#219 merged")
    ]

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


def test_sweep_scopes_same_upstream_ref_to_each_slot_repository(
    tmp_path, monkeypatch
):
    """A shared legacy queue must resolve identical refs in each slot's repo."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    open_slot = queue.add(
        "work tied to repo-a #12", project="/repos/repo-a", upstream_refs=["#12"]
    )
    merged_slot = queue.add(
        "work tied to repo-b #12", project="/repos/repo-b", upstream_refs=["#12"]
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        repo = argv[argv.index("-R") + 1] if "-R" in argv else "/repos/repo-b"
        state = "OPEN" if repo == "/repos/repo-a" else "MERGED"
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"state": state, "mergedAt": "now"}),
        )

    monkeypatch.setattr(
        coordinator, "subprocess", types.SimpleNamespace(run=fake_run)
    )

    retired = queue.sweep_stale()

    assert [slot.id for slot in retired] == [merged_slot.id]
    assert queue.get_pending() == [open_slot]
    assert calls
    assert all(
        call[call.index("-R") + 1] in {"/repos/repo-a", "/repos/repo-b"}
        for call in calls
    )


def test_sweep_stale_keeps_open_and_refless_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    open_pr = queue.add("work PR #101 still open", upstream_refs=["#101"])
    no_ref = queue.add("tidy the vault with no upstream at all")

    assert queue.sweep_stale(lambda ref: None if ref == "#101" else "gone") == []

    fresh = WorkQueue()
    assert [s.id for s in fresh.get_pending()] == [open_pr.id, no_ref.id]
    assert all(s.status == "pending" for s in fresh._load())


def test_legacy_unlinked_slot_is_report_only_without_parsing_text(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    legacy = queue.add("follow-up for merged #219; do not touch #220")
    legacy.upstream_refs = None
    queue._save([legacy])

    def must_not_check(ref):
        pytest.fail(f"unstructured legacy ref was inferred: {ref}")

    assert queue.sweep_stale(must_not_check) == []

    saved = WorkQueue()._load()[0]
    assert saved.status == "pending"
    assert saved.task_card_id is None
    assert saved.upstream_refs is None
    assert "report-only" in (saved.stale_upstream_note or "").lower()
    assert "#219" not in saved.stale_upstream_note
    assert "report-only" in caplog.text.lower()


def test_sweep_stale_notes_terminal_upstream_without_retiring_assigned_work(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    claimed = queue.add("agent is actively on PR #219", upstream_refs=["#219"])
    assert queue.claim_by_id(claimed.id, "lapis")

    assert queue.sweep_stale(lambda ref: f"{ref} merged") == []
    slot = WorkQueue()._load()[0]
    assert slot.status == "assigned"
    assert slot.assigned_to == "lapis"
    assert slot.retired_reason is None
    assert slot.stale_upstream_note == "terminal upstream: #219 merged"

    saves = []
    monkeypatch.setattr(WorkQueue, "_save", lambda self, slots: saves.append(1))
    assert queue.sweep_stale(lambda ref: f"{ref} merged") == []
    assert saves == []
    assert WorkQueue()._load()[0].stale_upstream_note == slot.stale_upstream_note


def test_sweep_stale_requires_every_ref_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    mixed = queue.add("fix #100 and #101 together", upstream_refs=["#100", "#101"])
    done = queue.add("both #100 and #102 landed", upstream_refs=["#100", "#102"])

    def checker(ref):
        return None if ref == "#101" else f"{ref} merged"

    retired = queue.sweep_stale(checker)
    assert [(slot.id, slot.retired_reason) for slot in retired] == [
        (done.id, "#100 merged; #102 merged")
    ]
    assert [s.id for s in WorkQueue().get_pending()] == [mixed.id]


def test_sweep_stale_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    queue.add("done via #219", upstream_refs=["#219"])
    queue.sweep_stale(lambda ref: f"{ref} merged")

    saves = []
    monkeypatch.setattr(WorkQueue, "_save", lambda self, slots: saves.append(1))
    assert queue.sweep_stale(lambda ref: f"{ref} merged") == []
    assert saves == []


def test_sweep_stale_checker_exception_keeps_work(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    slot = queue.add(
        "upstream is #219 but the checker is flaky", upstream_refs=["#219"]
    )

    def boom(ref):
        raise RuntimeError("gh exploded")

    assert queue.sweep_stale(boom) == []
    fresh = WorkQueue()
    assert [s.id for s in fresh.get_pending()] == [slot.id]


def test_sweep_stale_skips_save_when_nothing_retired(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    queue.add("live work on #101", upstream_refs=["#101"])

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
            ("gh", "pr", "view", "220"): (
                0,
                '{"state": "CLOSED", "mergedAt": "2026-11-02T15:00:00Z"}',
            ),
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
    assert coordinator.github_terminal_state("#220") == "#220 merged"
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


def test_github_terminal_state_uses_repository_remote_for_project_path(
    tmp_path, monkeypatch
):
    project = tmp_path / "repo-a"
    project.mkdir()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if argv[:2] == ["git", "-C"]:
            return types.SimpleNamespace(
                returncode=0,
                stdout="git@github.com:owner/repo-a.git\n",
            )
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"state": "MERGED", "mergedAt": "now"}),
        )

    monkeypatch.setattr(coordinator, "subprocess", types.SimpleNamespace(run=fake_run))

    assert coordinator.github_terminal_state("#12", str(project)) == "#12 merged"
    gh_call = next(argv for argv, _ in calls if argv[0] == "gh")
    assert gh_call[gh_call.index("-R") + 1] == "github.com/owner/repo-a"


import threading


def test_concurrent_add_survives_sweep(tmp_path, monkeypatch):
    """Regression (issue #645 race): sweep_stale must not erase a slot
    appended while its checkers run. The checker blocks until the
    concurrent add has landed, then returns a terminal verdict; the
    sweep's save must still preserve the appended slot."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    stale = queue.add("wrap up #571 docs", upstream_refs=["#571"])
    entered, release = threading.Event(), threading.Event()

    def paused_checker(ref):
        entered.set()
        assert release.wait(timeout=10)
        return f"{ref} closed"

    result = {}

    def sweep():
        result["retired"] = queue.sweep_stale(paused_checker)

    sweeper = threading.Thread(target=sweep)
    sweeper.start()
    assert entered.wait(timeout=10)  # sweep is mid-checker on its snapshot
    added = queue.add("brand new task queued during the sweep")
    release.set()
    sweeper.join(timeout=10)

    assert [(slot.id, slot.retired_reason) for slot in result["retired"]] == [
        (stale.id, "#571 closed")
    ]
    states = {s.id: s.status for s in queue.get_all()}
    assert states[added.id] == "pending", "concurrent append was erased"
    assert states[stale.id] == "retired"


def test_concurrent_claim_survives_sweep(tmp_path, monkeypatch):
    """Regression (issue #645 race): a claim landing while the sweep's
    checkers run must not be clobbered back to pending by the sweep."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    stale = queue.add("close out #571 followups", upstream_refs=["#571"])
    claimable = queue.add(
        "no refs, claimable", priority=9
    )  # higher number claims first
    entered, release = threading.Event(), threading.Event()

    def paused_checker(ref):
        entered.set()
        assert release.wait(timeout=10)
        return f"{ref} closed"

    result = {}

    def sweep():
        result["retired"] = queue.sweep_stale(paused_checker)

    sweeper = threading.Thread(target=sweep)
    sweeper.start()
    assert entered.wait(timeout=10)
    claimed = queue.claim_next("ruby")
    release.set()
    sweeper.join(timeout=10)

    assert claimed is not None and claimed.id == claimable.id
    states = {s.id: s.status for s in queue.get_all()}
    assert states[claimable.id] == "assigned", "claim was clobbered by sweep"
    assert states[stale.id] == "retired"


def test_sweep_does_not_retire_slot_claimed_after_decision(tmp_path, monkeypatch):
    """A terminal-ref slot claimed between the sweep's decision and its
    save is left alone: claims beat retirement decisions."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    contested = queue.add("finish #571, refs terminal", upstream_refs=["#571"])
    entered, release = threading.Event(), threading.Event()

    def paused_checker(ref):
        entered.set()
        assert release.wait(timeout=10)
        return f"{ref} merged"

    result = {}

    def sweep():
        result["retired"] = queue.sweep_stale(paused_checker)

    sweeper = threading.Thread(target=sweep)
    sweeper.start()
    assert entered.wait(timeout=10)
    assert queue.claim_by_id(contested.id, "ruby")
    release.set()
    sweeper.join(timeout=10)

    assert result["retired"] == [], "claimed slot was retired anyway"
    slot = queue._load()[0]
    assert slot.status == "assigned"
    assert slot.assigned_to == "ruby"


def test_parallel_adds_do_not_lose_slots(tmp_path, monkeypatch):
    """Writers racing on the same queue file from separate queue
    instances (mirrors separate processes): every add lands."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    errors = []

    def hammer(n):
        try:
            for i in range(n):
                WorkQueue().add(f"parallel task {threading.get_ident()}-{i}")
        except Exception as e:  # pragma: no cover - failure reporting
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(20,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert len(WorkQueue().get_all()) == 60, "lost-update race dropped slots"


# --- review blockers: F1 throttle, F2 strict load, F3 budget, F6 explicit refs ---


def test_sweep_ignores_refs_in_free_text(tmp_path, monkeypatch):
    """Review F6 (false retirement): a #NNN mentioned as background in
    the task text must never retire the slot. Only refs registered
    explicitly on the slot (upstream_refs) drive retirement."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    mention = queue.add("Follow-up to #577: add the retention knob")

    retired = queue.sweep_stale(lambda ref: f"{ref} merged")

    assert retired == [], "free-text mention retired live work"
    states = {s.id: s.status for s in queue.get_all()}
    assert states[mention.id] == "pending"


def test_sweep_uses_only_explicit_upstream_refs(tmp_path, monkeypatch):
    """Explicitly registered refs still retire genuinely stale work."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    stale = queue.add("wrap up #571 docs", upstream_refs=["#571"])

    retired = queue.sweep_stale(lambda ref: f"{ref} merged")
    assert [(slot.id, slot.retired_reason) for slot in retired] == [
        (stale.id, "#571 merged")
    ]


def test_sweep_budget_bounds_upstream_checks(tmp_path, monkeypatch):
    """Review F3 (fail-open budget): at most MAX_UPSTREAM_CHECKS unique
    upstream refs are checked per sweep; slots beyond the budget are
    kept (fail open), never retired on a missing answer."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    for n in range(coordinator.MAX_UPSTREAM_CHECKS + 10):
        queue.add(f"task {n}", upstream_refs=[f"#{1000 + n}"])

    calls = []

    def counting_checker(ref):
        calls.append(ref)
        return f"{ref} merged"

    retired = queue.sweep_stale(counting_checker)

    assert len(calls) == coordinator.MAX_UPSTREAM_CHECKS
    assert len(retired) == coordinator.MAX_UPSTREAM_CHECKS
    assert len(queue.get_all()) == coordinator.MAX_UPSTREAM_CHECKS + 10


def test_sweep_dedupes_shared_refs_across_slots(tmp_path, monkeypatch):
    """Review F3 (ref dedup): two slots citing the same ref cost one
    upstream check, not two."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    queue.add("first", upstream_refs=["#801", "#802"])
    queue.add("second", upstream_refs=["#801", "#803"])

    calls = []

    def counting_checker(ref):
        calls.append(ref)
        return f"{ref} merged"

    retired = queue.sweep_stale(counting_checker)
    assert len(retired) == 2
    assert sorted(calls) == ["#801", "#802", "#803"]


def test_sweep_retires_completed_slots(tmp_path, monkeypatch):
    """Review F3 (#654 fold): completed slots are dead weight and are
    always retired, without spending any upstream check."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    done = queue.add("finished long ago")
    queue.complete(done.id)
    refless = queue.add("still pending, no refs")

    calls = []

    def counting_checker(ref):
        calls.append(ref)
        return None

    retired = queue.sweep_stale(counting_checker)

    assert [(slot.id, slot.retired_reason) for slot in retired] == [
        (done.id, "terminal slot (completed)")
    ]
    assert calls == [], "completed retirement must not touch upstream"
    states = {s.id: s.status for s in queue.get_all()}
    assert states[done.id] == "retired"
    assert states[refless.id] == "pending"


def test_mutate_raises_on_corrupt_queue_file(tmp_path, monkeypatch):
    """Review F2: a corrupt queue file must fail loudly inside the
    locked mutate path, never silently replace the queue with a fresh
    single-slot file."""
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    queue.add("real queued work")
    (tmp_path / "work-queue.json").write_text("{not json at all")

    with pytest.raises(Exception):
        queue.add("would have wiped the queue")

    # The failed add must not have saved: the file on disk still holds
    # the corrupt bytes, not a fresh one-slot queue.
    assert (tmp_path / "work-queue.json").read_text() == "{not json at all"


def test_sweep_scheduler_throttles_between_intervals():
    """Review F1 (unbounded schedule): the sweep scheduler fires on
    boot, then at most once per interval."""
    sched = coordinator.SweepScheduler(interval_secs=900)

    assert sched.due(now=1000.0) is True, "boot sweep must run"
    assert sched.due(now=1005.0) is False, "second tick must not re-sweep"
    assert sched.due(now=1899.9) is False
    assert sched.due(now=1900.0) is True, "interval elapsed: sweep again"


@pytest.mark.parametrize(
    "ref, pr_answers, issue_answers, expected_first, expected_second",
    [
        (
            "#219",
            [
                (0, '{"state": "CLOSED", "mergedAt": null}'),
                (0, '{"state": "OPEN"}'),
            ],
            [],
            None,
            None,
        ),
        (
            "#220",
            [(1, ""), (1, "")],
            [
                (0, '{"state": "CLOSED", "stateReason": "COMPLETED"}'),
                (0, '{"state": "OPEN"}'),
            ],
            "#220 issue closed (COMPLETED)",
            None,
        ),
    ],
)
def test_closed_items_are_rechecked_after_reopen(
    ref, pr_answers, issue_answers, expected_first, expected_second, monkeypatch
):
    """Closed issues and unmerged PRs may reopen; only merged PRs are immutable."""
    number = ref.lstrip("#")
    answers = {
        ("gh", "pr", "view", number): list(pr_answers),
        ("gh", "issue", "view", number): list(issue_answers),
    }
    calls = []

    def fake_run(argv, capture_output, text, timeout):
        calls.append(list(argv))
        sequence = answers.get(tuple(argv[:4]), [])
        if not sequence:
            return types.SimpleNamespace(returncode=1, stdout="")
        returncode, stdout = sequence.pop(0)
        return types.SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(coordinator, "subprocess", types.SimpleNamespace(run=fake_run))

    assert coordinator.github_terminal_state(ref) == expected_first
    assert coordinator.github_terminal_state(ref) == expected_second
    assert len(calls) == len(pr_answers) + len(issue_answers)


@pytest.mark.parametrize("first_now", [0.0, 899.9])
def test_sweep_scheduler_runs_immediately_on_cold_start(first_now):
    sched = coordinator.SweepScheduler(interval_secs=900)

    assert sched.due(now=first_now) is True, "first boot tick must sweep immediately"
    assert sched.due(now=first_now + 899.9) is False
    assert sched.due(now=first_now + 900.0) is True
