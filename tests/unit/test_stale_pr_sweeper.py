"""Tests for the stale-PR sweeper (issue #718).

Covers the flag-only invariants: inactivity math, linked-issue parsing,
stale-head CI qualification, suggested actions, digest ordering, and the
label path being add-only with no close/comment/remove capability.
"""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "stale_pr_sweeper.py"


def _load():
    spec = importlib.util.spec_from_file_location("stale_pr_sweeper", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sweeper = _load()
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(
    number,
    *,
    draft=False,
    created=NOW - timedelta(days=10),
    last_commit=None,
    reviews=None,
    body=None,
    merge_state="CLEAN",
    mergeable="MERGEABLE",
    rollup=None,
    head="abc123",
    title="t",
):
    commits = []
    if last_commit is not None:
        commits = [{"committedDate": _iso(last_commit)}]
    return {
        "number": number,
        "title": title,
        "isDraft": draft,
        "createdAt": _iso(created),
        "body": body,
        "labels": [],
        "headRefOid": head,
        "mergeable": mergeable,
        "mergeStateStatus": merge_state,
        "statusCheckRollup": rollup or [],
        "commits": commits,
        "reviews": reviews or [],
    }


# --- inactivity -----------------------------------------------------------


def test_stale_after_threshold_with_no_activity():
    pr = _pr(1, created=NOW - timedelta(days=7))
    assert sweeper.is_stale(pr, NOW, days=5)


def test_fresh_commit_resets_the_clock():
    pr = _pr(2, created=NOW - timedelta(days=30), last_commit=NOW - timedelta(days=1))
    assert not sweeper.is_stale(pr, NOW, days=5)


def test_fresh_review_resets_the_clock():
    pr = _pr(
        3,
        created=NOW - timedelta(days=30),
        last_commit=NOW - timedelta(days=30),
        reviews=[{"submittedAt": _iso(NOW - timedelta(days=2))}],
    )
    assert not sweeper.is_stale(pr, NOW, days=5)


def test_label_churn_does_not_count_as_activity():
    # updatedAt-style noise (labels) is not in the activity inputs at all;
    # a PR last committed 6d ago is stale even if edited yesterday.
    pr = _pr(4, created=NOW - timedelta(days=6), last_commit=NOW - timedelta(days=6))
    assert sweeper.is_stale(pr, NOW, days=5)


def test_boundary_exact_threshold_is_stale():
    pr = _pr(5, created=NOW - timedelta(days=5))
    assert sweeper.is_stale(pr, NOW, days=5)


# --- linked issues ---------------------------------------------------------


def test_parse_linked_issues_keywords():
    body = "Fixes #1 and closes #2. refs #3, part of #4. resolves #5"
    assert sweeper.parse_linked_issues(body) == [1, 2, 3, 4, 5]


def test_parse_linked_issues_ignores_bare_hashes():
    assert sweeper.parse_linked_issues("see #12 and #13 for context") == []


def test_parse_linked_issues_empty():
    assert sweeper.parse_linked_issues(None) == []
    assert sweeper.parse_linked_issues("") == []


# --- head vs main and CI qualification ------------------------------------


def test_conflicted_state_reported():
    pr = _pr(6, merge_state="DIRTY", mergeable="CONFLICTING")
    assert "CONFLICTED" in sweeper.head_vs_main(pr, {"behind_by": 3, "ahead_by": 1})


def test_behind_state_includes_count():
    pr = _pr(7, merge_state="BEHIND")
    assert "BEHIND main by 9 commit(s)" in sweeper.head_vs_main(
        pr, {"behind_by": 9, "ahead_by": 0}
    )


def test_green_ci_on_stale_head_is_qualified_as_unproven():
    pr = _pr(8, merge_state="BEHIND", rollup=[{"conclusion": "SUCCESS"}])
    verdict = sweeper.ci_verdict(pr, "BEHIND main by 12 commit(s)")
    assert verdict.startswith("green at head, but")
    assert "unproven vs current main" in verdict


def test_green_ci_on_current_head_not_qualified():
    pr = _pr(9, rollup=[{"conclusion": "SUCCESS"}])
    verdict = sweeper.ci_verdict(pr, "even with main")
    assert verdict == "green"


def test_red_and_running_verdicts():
    red = sweeper.ci_verdict(
        _pr(10, rollup=[{"conclusion": "FAILURE"}]), "even with main"
    )
    assert red == "red"
    running = sweeper.ci_verdict(
        _pr(11, rollup=[{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS"}]),
        "even with main",
    )
    assert running == "running"
    empty = sweeper.ci_verdict(_pr(12, rollup=[]), "even with main")
    assert empty == "no checks recorded"


# --- suggested actions ------------------------------------------------------


def test_action_conflicting_takes_precedence():
    pr = _pr(
        13,
        draft=True,
        merge_state="DIRTY",
        mergeable="CONFLICTING",
        last_commit=NOW - timedelta(days=60),
    )
    action = sweeper.suggested_action(pr, [5], "CONFLICTED with main", 60.0, {})
    assert action.startswith("rebase/update (conflict repair)")


def test_action_behind_suggests_rebase_update():
    pr = _pr(14, merge_state="BEHIND", last_commit=NOW - timedelta(days=6))
    action = sweeper.suggested_action(pr, [5], "BEHIND main by 4 commit(s)", 6.0, {})
    assert action.startswith("rebase/update (update-branch from main)")


def test_action_close_candidate_for_old_draft():
    pr = _pr(15, draft=True, last_commit=NOW - timedelta(days=25))
    action = sweeper.suggested_action(pr, [5], "even with main", 25.0, {})
    assert action.startswith("close candidate (draft, 25d inactive)")


def test_action_close_for_very_old_any_pr():
    pr = _pr(16, last_commit=NOW - timedelta(days=50))
    action = sweeper.suggested_action(pr, [5], "even with main", 50.0, {})
    assert action.startswith("close (50d inactive)")


def test_action_consolidate_for_shared_issue():
    pr = _pr(17, last_commit=NOW - timedelta(days=6))
    action = sweeper.suggested_action(pr, [630], "even with main", 6.0, {630: [17, 18]})
    assert "consolidate with #18" in action


def test_action_notes_missing_linked_issue():
    pr = _pr(18, last_commit=NOW - timedelta(days=6))
    action = sweeper.suggested_action(pr, [], "even with main", 6.0, {})
    assert action.startswith("finish")
    assert "link its issue" in action


# --- digest -----------------------------------------------------------------


def test_digest_lists_drafts_first_and_includes_conflicted_section():
    old_ready = _pr(
        20,
        draft=False,
        title="ready but old",
        last_commit=NOW - timedelta(days=8),
        body="Fixes #1",
        head="h20",
        merge_state="CLEAN",
        rollup=[{"conclusion": "SUCCESS"}],
    )
    new_draft_conflicted = _pr(
        19,
        draft=True,
        title="draft | with pipe",
        last_commit=NOW - timedelta(days=6),
        body="Closes #2",
        head="h19",
        merge_state="DIRTY",
        mergeable="CONFLICTING",
        rollup=[{"conclusion": "SUCCESS"}],
    )
    rows = sweeper.collect_rows(
        [old_ready, new_draft_conflicted],
        {19: {"behind_by": 2, "ahead_by": 0}, 20: {"behind_by": 0, "ahead_by": 0}},
        NOW,
        days=5,
    )
    digest = sweeper.build_digest(rows, NOW, 5, dry_run=True)
    assert digest.index("#19") < digest.index("#20"), "drafts must sort first"
    assert "Conflicted backlog disposition" in digest
    assert "#19: disposition" in digest
    assert "DRY RUN" in digest
    # Table-cell safety: pipe in a title must be escaped so rows stay intact.
    assert "draft \\| with pipe" in digest


def test_digest_empty_state_renders_cleanly():
    digest = sweeper.build_digest([], NOW, 5, dry_run=False)
    assert "0 stale PR(s)" in digest
    assert "Conflicted backlog disposition" not in digest


def test_digest_marks_unproven_ci_for_moved_heads():
    pr = _pr(
        21,
        title="behind but green",
        last_commit=NOW - timedelta(days=6),
        body="Refs #3",
        merge_state="BEHIND",
        head="h21",
        rollup=[{"conclusion": "SUCCESS"}],
    )
    rows = sweeper.collect_rows([pr], {21: {"behind_by": 7, "ahead_by": 0}}, NOW, 5)
    digest = sweeper.build_digest(rows, NOW, 5, dry_run=True)
    assert "unproven vs current main" in digest


# --- write-safety invariants -------------------------------------------------


def test_label_constant_is_stale():
    assert sweeper.STALE_LABEL == "stale"


def test_dry_run_never_invokes_gh(monkeypatch):
    def boom(*a, **k):  # any gh call in dry-run is a bug
        raise AssertionError("gh must not be called in dry-run mode")

    monkeypatch.setattr(sweeper.subprocess, "run", boom)
    assert sweeper.add_stale_label(22, "kollaborai/kollab", dry_run=True) == "DRYRUN"


def test_label_command_is_add_only(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    monkeypatch.setattr(sweeper.subprocess, "run", fake_run)
    result = sweeper.add_stale_label(23, "kollaborai/kollab", dry_run=False)
    assert result == "LABELED"
    cmd = captured["cmd"]
    assert "--add-label" in cmd and "stale" in cmd
    for forbidden in (
        "--remove-label",
        "--close",
        "--comment",
        "--ready",
        "--draft",
        "--reopen",
    ):
        assert forbidden not in cmd
    assert cmd[0:2] == ["gh", "pr"]


def test_gh_failure_surfaces_stderr(monkeypatch):
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stderr = "HTTP 403: Must have admin rights"
            stdout = ""

        return R()

    monkeypatch.setattr(sweeper.subprocess, "run", fake_run)
    result = sweeper.add_stale_label(24, "kollaborai/kollab", dry_run=False)
    assert result.startswith("FAILED:")
    assert "403" in result


def test_fetch_open_prs_shallow_list_then_per_pr_view(monkeypatch):
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if args[0] == "pr" and args[1] == "list":
            return '[{"number": 30}, {"number": 31}]'
        number = next(int(a) for a in args if str(a).isdigit())
        import json as _json

        return _json.dumps(
            {"number": number, "title": "x", "commits": [], "reviews": []}
        )

    monkeypatch.setattr(sweeper, "gh", fake_gh)
    prs = sweeper.fetch_open_prs("kollaborai/kollab")
    assert [p["number"] for p in prs] == [30, 31]
    list_calls = [c for c in calls if c[0] == "pr" and c[1] == "list"]
    view_calls = [c for c in calls if c[0] == "pr" and c[1] == "view"]
    assert len(list_calls) == 1, "one shallow list call"
    assert len(view_calls) == 2, "nested fields fetched per PR via view"


def test_main_rejects_missing_repo(capsys, monkeypatch):
    monkeypatch.delenv("GH_REPO", raising=False)
    # main() returns the exit code; the __main__ wrapper raises SystemExit.
    assert sweeper.main([]) == 2
    assert "--repo or $GH_REPO is required" in capsys.readouterr().err
