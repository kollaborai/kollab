"""#721: contributor artifacts cannot certify red-first execution.

These histories replay every required finding in the redesign brief. The
automated gate owns reference hygiene and exact-head reviewer authorization;
the independent reviewer owns reproducing the failure and verifying the fix.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.unit.test_pr_gates_dod import (
    BASE_TIP,
    HEAD_SHA,
    PR_NUM,
    _commit,
    _install_fake_gh,
    approved_review,
    ignore_reference_comment,
    make_data,
    pr_gates_dod,
    regression_cfg,
    source_comment,
)

SCENARIOS = (
    "01-backdated-test-and-fix",
    "02-code-no-test-declaration",
    "03-backdated-fix-before-test",
    "04-unrelated-patch-final-blob-id",
    "05-existing-id-unrelated-assertion",
    "06-docs-declaration-without-reason",
    "07-xfail-removal-other-file",
    "08-finding-in-assertion-comment",
    "09-xfail-removal-other-file-repeat",
    "10-code-with-docs-only-reason",
    "11-placeholder-test-name",
    "12-xfail-removal-other-test",
    "13-runtime-then-docs-declaration",
    "14-placeholder-test-name-repeat",
    "15-runtime-then-docs-declaration-repeat",
    "16-xfail-removal-other-test-repeat",
    "17-helper-assertion-after-placeholder",
    "18-always-truthy-object-call",
    "19-always-truthy-object-call-repeat",
    "20-unrelated-runtime-value",
)


def history(scenario):
    finding = "F15" if "declaration" in scenario or "reason" in scenario else "F14"
    if "assertion-comment" in scenario or "other-test" in scenario:
        finding = "F16"
    if "unrelated-runtime" in scenario:
        finding = "F17"
    path = "tests/unit/test_core.py"
    patch = f"+def test_{finding}_placeholder(): assert True"
    if "unrelated-patch" in scenario:
        patch = "@@ -10 +10 @@\n-assert old_value == 1\n+assert old_value == 2"
    elif "existing-id" in scenario:
        patch = "+assert True"
    elif "assertion-comment" in scenario:
        patch = f"+assert True  # {finding}"
    elif "helper-assertion" in scenario:
        patch = f"+def test_{finding}_placeholder(): pass\n+def helper(): assert reproduce_bug()"
    elif "object-call" in scenario:
        patch = f"+def test_{finding}_placeholder(): assert object()"
    elif "unrelated-runtime" in scenario:
        patch = f"+def test_{finding}_placeholder():\n+    value = 1\n+    assert value == 1"
    elif "xfail" in scenario:
        patch = (
            f"+@pytest.mark.xfail(strict=True)\n+def test_{finding}_runtime(): assert broken()\n"
            "+@pytest.mark.xfail(strict=True)\n+def test_other(): assert other()"
        )

    def commit(sha, files, message, patches=None, order=11):
        return SimpleNamespace(
            sha=sha * 40,
            verified=True,
            message=message,
            files=files,
            committed_at="2026-10-01T00:00:00Z",
            timeline_order=order,
            file_patches=patches or {},
        )

    test = commit("a", (path,), f"test: reproduce {finding}", {path: patch})
    fix = commit("b", ("kollabor/core.py",), f"fix: repair {finding}", order=12)
    commits = [test, fix]
    if "backdated" in scenario:
        # Exercise the old timestamp fallback with no server timeline metadata.
        test.timeline_order = fix.timeline_order = None
    if "fix-before-test" in scenario:
        commits = [fix, test, commit("c", ("kollabor/other.py",), "fix: follow-up", order=13)]
    if "declaration" in scenario or "reason" in scenario:
        message = f"fix: repair behavior\n\nNo regression test: {finding}"
        if "without-reason" not in scenario:
            message += " - documentation wording only."
        declared = commit("d", ("docs/guide.md",), message, order=13)
        if "runtime-then-docs" in scenario:
            commits = [fix, declared]
        elif "code" in scenario:
            declared.files = ("kollabor/core.py",)
            commits = [declared]
        else:
            commits = [declared]
    if "xfail" in scenario:
        removed_path = "tests/unit/test_other.py" if "other-file" in scenario else path
        fix.files = ("kollabor/core.py", removed_path)
        fix.file_patches = {removed_path: "@@ -4 +4 @@\n-@pytest.mark.xfail(strict=True)"}
    finding_comment = SimpleNamespace(
        kind="review_comment",
        comment_id=1,
        body=f"{finding}: behavior is broken",
        author_login="reviewer",
        created_at="2026-11-01T12:00:00Z",
        timeline_order=None if "backdated" in scenario else 10,
    )
    return make_data(
        commits=tuple(commits),
        comments=(finding_comment,),
        changed_file_contents={path: f"@pytest.mark.xfail(strict=True)\ndef test_{finding}_runtime(): ..."},
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_red_first_histories_require_independent_verification(tmp_path, scenario):
    report = pr_gates_dod.evaluate(regression_cfg(tmp_path), history(scenario))
    assert "regression_test" in report.failed_names()
    assert "independent current-head approval required" in report.detail_for("regression_test")
    assert "red-first execution is not verified by this gate" in report.summary()


def test_covered_finding_needs_reviewer_even_without_history_metadata(tmp_path):
    data = make_data(
        review_comment_bodies=("F14: runtime defect",),
        changed_file_contents={"tests/test_core.py": "# F14"},
    )
    report = pr_gates_dod.evaluate(regression_cfg(tmp_path), data)
    assert report.exit_code == 1
    assert "independent current-head approval required" in report.summary()


def test_approval_records_manual_responsibility_without_certifying_execution(tmp_path):
    data = history("20-unrelated-runtime-value")
    data = replace(data, reviews=(approved_review(),))
    report = pr_gates_dod.evaluate(regression_cfg(tmp_path), data)
    assert report.exit_code == 0
    assert "red-first execution is not verified by this gate" in report.summary()
    assert "F17" in report.summary()


@pytest.mark.parametrize("reason", ("incidental reference", "documentation wording only; no executable behavior"))
def test_no_test_exception_belongs_to_exact_head_reviewer(tmp_path, reason):
    source = source_comment("issue_comment", 100, "F15: wording is misleading")
    marker = ignore_reference_comment(
        501,
        source_kind=source.kind,
        source_id=100,
        source_body=source.body,
        finding_id="F15",
        occurrence=1,
        extra={"reason": reason},
    )
    data = make_data(comments=(source, marker), reviews=(approved_review(),), changed_file_contents={})
    report = pr_gates_dod.evaluate(regression_cfg(tmp_path), data)
    assert report.exit_code == 0
    assert len(report.audit_notes) == 1
    assert report.audit_notes[0].reason == reason


@pytest.mark.parametrize(
    "changes",
    (
        {"reviews": ()},
        {"reviews": (approved_review(author="outsider"),)},
        {"reviews": (approved_review(author="PR-AUTHOR"),)},
        {"reviews": (approved_review(head="a" * 40),)},
        {"reviews": (approved_review(), approved_review(10, state="CHANGES_REQUESTED"))},
        {"reviews": (approved_review(), approved_review(10, state="DISMISSED"))},
        {"reviews": (approved_review(), approved_review(9))},
        {"reviews": (approved_review(),), "pull_author_login": ""},
        {"reviews": (approved_review(),), "head_sha": ""},
    ),
)
def test_review_boundary_rejects_untrusted_or_stale_evidence(tmp_path, changes):
    data = replace(history("11-placeholder-test-name"), **changes)
    cfg = regression_cfg(tmp_path, trusted_reviewers=("trusted-reviewer", "pr-author"))
    report = pr_gates_dod.evaluate(cfg, data)
    assert report.exit_code == 1
    assert "independent current-head approval required" in report.summary()


def test_collector_does_not_fetch_execution_proxies(tmp_path, monkeypatch):
    calls = []
    pull = {
        "title": "fix: repair finding",
        "body": "Fixes #721",
        "user": {"login": "pr-author"},
        "base": {"ref": "main", "sha": BASE_TIP},
        "head": {"sha": HEAD_SHA},
    }
    pages = {
        "rest": {
            f"repos/o/r/pulls/{PR_NUM}/comments": {1: [{"id": 1, "body": "F14: runtime defect"}]},
            f"repos/o/r/pulls/{PR_NUM}/commits": {1: [_commit("e" * 40)]},
        }
    }
    _install_fake_gh(monkeypatch, calls, pull, BASE_TIP, pages)
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM, root=str(tmp_path))
    assert data.review_comment_bodies == ("F14: runtime defect",)
    assert not any("/timeline" in str(call) or "/commits/" in str(call) for call in calls)


@pytest.mark.parametrize(
    "created,updated",
    (
        ("2026-11-09T00:00:00Z", "2026-11-09T00:00:00Z"),
        ("2026-11-01T12:00:00Z", "2026-11-09T00:00:00Z"),
        ("", ""),
        ("invalid", "invalid"),
        ("2026-11-01T12:00:00Z", "2026-10-01T12:00:00Z"),
        ("2026-11-08T00:00:09Z", "2026-11-08T00:00:09Z"),
    ),
)
def test_approval_must_follow_latest_finding_revision(tmp_path, created, updated):
    data = history("20-unrelated-runtime-value")
    data.comments[0].created_at = created
    data.comments[0].updated_at = updated
    data = replace(data, reviews=(approved_review(),))
    report = pr_gates_dod.evaluate(regression_cfg(tmp_path), data)
    assert report.exit_code == 1
    assert "independent current-head approval required" in report.summary()


def test_mismatched_comment_snapshot_cannot_authorize_approval(tmp_path):
    data = replace(
        history("20-unrelated-runtime-value"),
        reviews=(approved_review(),),
        review_comment_bodies=("F17: different source",),
    )
    report = pr_gates_dod.evaluate(regression_cfg(tmp_path), data)
    assert report.exit_code == 1


def test_comment_events_post_to_collected_head(tmp_path, monkeypatch):
    cfg = tmp_path / "gate.toml"
    cfg.write_text("[pr_gates.dod]\nenabled = true\nchecks = []\n")
    data = make_data()
    posted = []
    monkeypatch.setattr(pr_gates_dod, "collect_pr_data", lambda *_a, **_kw: data)
    monkeypatch.setattr(pr_gates_dod, "main_post_if_enabled", lambda *_a, **kw: posted.append(kw))
    assert pr_gates_dod.main(["--repo", "o/r", "--pr", "1", "--config", str(cfg), "--post"]) == 0
    assert posted == [{"repo": "o/r", "head_sha": data.head_sha}]
