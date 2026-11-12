"""Red-first tests for scripts/pr_gates_dod.py (issue #724).

Contract: a definition-of-done check that runs per PR, evaluates the
enabled checks from .github/pr-gates.toml, names every failed item in
one check run, and never posts comments.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pr_gates_dod.py"
CONFIG_FILE = REPO_ROOT / ".github" / "pr-gates.toml"
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "pr-gates-dod.yml"

spec = importlib.util.spec_from_file_location("pr_gates_dod", SCRIPT)
pr_gates_dod = importlib.util.module_from_spec(spec)
# Register before exec: dataclasses resolves string annotations through
# sys.modules[cls.__module__]; without this the module fails to import.
sys.modules["pr_gates_dod"] = pr_gates_dod
spec.loader.exec_module(pr_gates_dod)


ALL_CHECKS = [
    "signed_commits",
    "docs_paths",
    "threads_resolved",
    "base_current",
    "conventional_title",
    "linked_issue",
    "regression_test",
    "spec_or_fix_brief",
]


def make_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pr-gates.toml"
    path.write_text(body, encoding="utf-8")
    return path


def make_data(**overrides):
    base = dict(
        title="fix(ci): a conventional title",
        body="Some body.\n\nFixes #724\n",
        commits=[
            pr_gates_dod.Commit(sha="a" * 40, message="fix(ci): one", verified=True),
            pr_gates_dod.Commit(sha="b" * 40, message="fix(ci): two", verified=True),
        ],
        changed_files=["kollabor/x.py", "tests/unit/test_x.py"],
        unresolved_threads=0,
        review_comment_bodies=[],
        comments=(),
        reviews=(),
        pull_author_login="pr-author",
        head_sha="c" * 40,
        default_branch="main",
        default_branch_head="d" * 40,
        merge_base_sha="d" * 40,
        commits_behind_base=0,
    )
    base.update(overrides)
    if "comments" in overrides and "review_comment_bodies" not in overrides:
        base["review_comment_bodies"] = [comment.body for comment in base["comments"]]
    return pr_gates_dod.PRData(**base)


ENABLED = """
[pr_gates.dod]
enabled = true
"""


def regression_cfg(tmp_path: Path, trusted_reviewers=("trusted-reviewer",)):
    trusted = json.dumps(list(trusted_reviewers))
    config = make_config(
        tmp_path,
        ENABLED
        + '\nchecks = ["regression_test"]\n'
        + "\n[pr_gates.dod.regression_profile]\n"
        + f"trusted_reviewers = {trusted}\n",
    )
    return pr_gates_dod.load_config(config)


def source_comment(kind, comment_id, body, author="reviewer"):
    return pr_gates_dod.PRComment(
        kind=kind, comment_id=comment_id, body=body, author_login=author
    )


def approved_review(review_id=9, author="trusted-reviewer", state="APPROVED", head=None):
    return pr_gates_dod.PRReview(
        review_id=review_id,
        author_login=author,
        state=state,
        commit_id=head or "c" * 40,
        submitted_at=f"2026-11-08T00:{review_id // 60:02d}:{review_id % 60:02d}Z",
    )


def ignore_reference_comment(
    marker_id,
    *,
    source_kind,
    source_id,
    source_body,
    finding_id,
    occurrence,
    author="trusted-reviewer",
    body_hash=None,
    extra=None,
):
    payload = {
        "source_kind": source_kind,
        "source_id": source_id,
        "source_body_sha256": body_hash
        or hashlib.sha256(source_body.encode("utf-8")).hexdigest(),
        "finding_id": finding_id,
        "occurrence": occurrence,
        "reason": "This exact occurrence names a test result, not a review finding.",
    }
    if extra:
        payload.update(extra)
    body = "<!-- pr-gates-ignore-reference:v1\n" + json.dumps(payload) + "\n-->"
    return source_comment("issue_comment", marker_id, body, author=author)


# --- config loading --------------------------------------------------------


def test_config_defaults_disabled(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(tmp_path / "missing.toml")
    assert cfg.enabled is False
    assert cfg.check_name == "dod/ready-for-review"
    assert cfg.checks == ALL_CHECKS
    assert cfg.docs_paths == {}
    assert cfg.spec_or_fix_brief_mode == "block"
    assert cfg.regression.finding_id_pattern == pr_gates_dod.DEFAULT_FINDING_PATTERN
    assert cfg.regression.test_paths == pr_gates_dod.DEFAULT_TEST_PATHS
    assert cfg.regression.trusted_reviewers == list(pr_gates_dod.DEFAULT_TRUSTED_REVIEWERS)


def test_config_enabled_and_custom(tmp_path: Path) -> None:
    path = make_config(
        tmp_path,
        """
[pr_gates.dod]
enabled = true
check_name = "custom/dod"
checks = ["signed_commits", "docs_paths"]

[pr_gates.dod.docs_paths]
"^packages/" = ["CHANGELOG.md"]

[pr_gates.dod.regression_profile]
finding_id_pattern = "BUG-\\\\d+"
test_paths = ["tests/regressions/"]
trusted_reviewers = ["trusted-reviewer"]
""",
    )
    cfg = pr_gates_dod.load_config(path)
    assert cfg.enabled is True
    assert cfg.check_name == "custom/dod"
    assert cfg.checks == ["signed_commits", "docs_paths"]
    assert cfg.docs_paths == {"^packages/": ["CHANGELOG.md"]}
    assert cfg.regression.finding_id_pattern == r"BUG-\d+"
    assert cfg.regression.test_paths == ["tests/regressions/"]
    assert cfg.regression.trusted_reviewers == ["trusted-reviewer"]


def test_config_rejects_duplicate_trusted_reviewers(tmp_path: Path) -> None:
    path = make_config(
        tmp_path,
        ENABLED
        + '\n[pr_gates.dod.regression_profile]\n'
        + 'trusted_reviewers = ["Maarco", "maarco"]\n',
    )
    with pytest.raises(pr_gates_dod.ConfigError, match="duplicates"):
        pr_gates_dod.load_config(path)


def test_repo_config_allows_only_the_two_owner_accounts_to_annotate():
    cfg = pr_gates_dod.load_config(CONFIG_FILE)
    assert cfg.regression.trusted_reviewers == ["kollaborai", "maarco"]


def test_config_rejects_unknown_check(tmp_path: Path) -> None:
    path = make_config(tmp_path, '[pr_gates.dod]\nenabled = true\nchecks = ["nope"]\n')
    with pytest.raises(pr_gates_dod.ConfigError, match="nope"):
        pr_gates_dod.load_config(path)


# --- evaluate: disabled/off behavior ---------------------------------------


def test_disabled_config_skips_all_checks(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(tmp_path / "missing.toml")
    report = pr_gates_dod.evaluate(cfg, make_data())
    assert report.ran is False
    assert report.failed == []
    assert report.exit_code == 0


def test_checks_toggleable_by_list(tmp_path: Path) -> None:
    path = make_config(tmp_path, ENABLED + '\nchecks = ["signed_commits"]\n')
    cfg = pr_gates_dod.load_config(path)
    data = make_data(
        title="not conventional",
        body="no issue link",
        unresolved_threads=3,
    )
    report = pr_gates_dod.evaluate(cfg, data)
    assert report.ran is True
    assert report.evaluated == ["signed_commits"]
    assert report.failed == []  # the only enabled check passes


# --- individual checks: positive + negative --------------------------------


def test_signed_commits_pass_and_fail(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    ok = pr_gates_dod.evaluate(cfg, make_data())
    assert "signed_commits" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(
        cfg,
        make_data(
            commits=[pr_gates_dod.Commit(sha="c" * 40, message="m", verified=False)]
        ),
    )
    assert "signed_commits" in bad.failed_names()


def test_docs_paths_pass_and_fail(tmp_path: Path) -> None:
    path = make_config(
        tmp_path,
        ENABLED + '\n[pr_gates.dod.docs_paths]\n"^packages/" = ["CHANGELOG.md"]\n',
    )
    cfg = pr_gates_dod.load_config(path)
    ok = pr_gates_dod.evaluate(
        cfg, make_data(changed_files=["packages/x.py", "CHANGELOG.md"])
    )
    assert "docs_paths" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(cfg, make_data(changed_files=["packages/x.py"]))
    assert "docs_paths" in bad.failed_names()
    assert "CHANGELOG.md" in bad.detail_for("docs_paths")


def test_docs_paths_ignores_unmatched_paths(tmp_path: Path) -> None:
    path = make_config(
        tmp_path,
        ENABLED + '\n[pr_gates.dod.docs_paths]\n"^packages/" = ["CHANGELOG.md"]\n',
    )
    cfg = pr_gates_dod.load_config(path)
    report = pr_gates_dod.evaluate(cfg, make_data(changed_files=["docs/guide.md"]))
    assert "docs_paths" not in report.failed_names()


def test_threads_resolved_pass_and_fail(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    ok = pr_gates_dod.evaluate(cfg, make_data(unresolved_threads=0))
    assert "threads_resolved" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(cfg, make_data(unresolved_threads=2))
    assert "threads_resolved" in bad.failed_names()


def test_base_current_pass_and_fail(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    ok = pr_gates_dod.evaluate(
        cfg,
        make_data(default_branch_head="d" * 40, merge_base_sha="d" * 40),
    )
    assert "base_current" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(
        cfg,
        make_data(
            default_branch_head="e" * 40,
            merge_base_sha="d" * 40,
            commits_behind_base=3,
        ),
    )
    assert "base_current" in bad.failed_names()


def test_conventional_title_pass_and_fail(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    ok = pr_gates_dod.evaluate(cfg, make_data(title="feat(ci): adds dod check"))
    assert "conventional_title" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(cfg, make_data(title="adds the thing"))
    assert "conventional_title" in bad.failed_names()


def test_linked_issue_pass_and_fail(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    ok = pr_gates_dod.evaluate(cfg, make_data(body="Fixes #724\n"))
    assert "linked_issue" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(cfg, make_data(body="no links here"))
    assert "linked_issue" in bad.failed_names()


def spec_gate_config(tmp_path: Path, mode="warn"):
    return pr_gates_dod.load_config(
        make_config(
            tmp_path,
            ENABLED
            + '\nchecks = ["spec_or_fix_brief"]\n'
            + f'spec_or_fix_brief_mode = "{mode}"\n',
        )
    )


@pytest.mark.parametrize(
    "data",
    [
        lambda: make_data(
            title="feat(cli): add a command",
            changed_files=["docs/specs/command.md"],
        ),
        lambda: make_data(
            title="feat(cli): add a command",
            body="Fixes #984",
            linked_issue_bodies={984: "Spec: docs/specs/command.md"},
        ),
    ],
)
def test_spec_or_fix_brief_feature_passes_with_pr_or_issue_spec(tmp_path: Path, data) -> None:
    report = pr_gates_dod.evaluate(spec_gate_config(tmp_path), data())
    assert "spec_or_fix_brief" not in report.failed_names()
    assert report.warnings == []


def test_spec_or_fix_brief_feature_without_spec_warns(tmp_path: Path) -> None:
    report = pr_gates_dod.evaluate(
        spec_gate_config(tmp_path), make_data(title="feat(cli): add a command")
    )
    assert "spec_or_fix_brief" not in report.failed_names()
    assert [name for name, _ in report.warnings] == ["spec_or_fix_brief"]
    assert report.exit_code == 0
    assert "warning" in report.summary().lower()


@pytest.mark.parametrize(
    "body,issue_bodies",
    [
        (
            "Fixes #984\n\n## Fix brief\nRoot cause: wrong source.\nRegression test: tests/unit/test_x.py.",
            {},
        ),
        ("Fixes #984", {984: "## Fix brief\nRoot cause: wrong source.\nRegression test: tests/unit/test_x.py."}),
    ],
)
def test_spec_or_fix_brief_fix_passes_with_pr_or_issue_brief(
    tmp_path: Path, body, issue_bodies
) -> None:
    report = pr_gates_dod.evaluate(
        spec_gate_config(tmp_path),
        make_data(title="fix(cli): correct the command", body=body, linked_issue_bodies=issue_bodies),
    )
    assert report.failed_names() == []
    assert report.warnings == []


def test_spec_or_fix_brief_fix_without_brief_warns_or_blocks(tmp_path: Path) -> None:
    data = make_data(title="fix(cli): correct the command")
    warn = pr_gates_dod.evaluate(spec_gate_config(tmp_path, "warn"), data)
    assert warn.failed_names() == []
    assert [name for name, _ in warn.warnings] == ["spec_or_fix_brief"]
    block = pr_gates_dod.evaluate(spec_gate_config(tmp_path, "block"), data)
    assert block.failed_names() == ["spec_or_fix_brief"]


def test_warn_mode_posts_a_successful_check_with_warning_details(tmp_path, monkeypatch):
    cfg = spec_gate_config(tmp_path, "warn")
    report = pr_gates_dod.evaluate(
        cfg, make_data(title="feat(cli): add a command")
    )
    payloads = []
    monkeypatch.setattr(
        pr_gates_dod,
        "github_api",
        lambda endpoint, payload: payloads.append(payload) or {"id": 1},
    )
    pr_gates_dod.post_check_run(cfg, report, repo="o/r", head_sha="c" * 40)
    assert payloads[0]["conclusion"] == "success"
    assert payloads[0]["output"]["title"] == "Definition of done: passed with warnings"
    assert "WARNING spec_or_fix_brief" in payloads[0]["output"]["summary"]


@pytest.mark.parametrize("title", [
    "docs: clarify command guide",
    "chore(ci): tidy workflow",
    "test(cli): cover parser",
    "ci: update checks",
    "release: publish 1.2.3",
])
def test_spec_or_fix_brief_exempts_non_feature_and_fix_types(tmp_path: Path, title) -> None:
    report = pr_gates_dod.evaluate(spec_gate_config(tmp_path), make_data(title=title))
    assert report.failed_names() == []
    assert report.warnings == []


def test_regression_test_no_findings_passes(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    report = pr_gates_dod.evaluate(
        cfg, make_data(review_comment_bodies=["looks fine to me"])
    )
    assert "regression_test" not in report.failed_names()


def test_regression_test_covered_and_uncovered(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    data_kw = dict(review_comment_bodies=["F14: the psutil bypass is open"])
    ok = pr_gates_dod.evaluate(
        cfg,
        make_data(
            changed_files=[
                "kollabor/x.py",
                "tests/unit/test_guard.py",
            ],
            changed_file_contents={
                "tests/unit/test_guard.py": "def test_f14_bypass(): ..."
            },
            **data_kw,
        ),
    )
    assert "regression_test" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(
        cfg,
        make_data(
            changed_files=["kollabor/x.py", "tests/unit/test_guard.py"],
            changed_file_contents={
                "tests/unit/test_guard.py": "def test_unrelated(): ..."
            },
            **data_kw,
        ),
    )
    assert "regression_test" in bad.failed_names()
    assert "F14" in bad.detail_for("regression_test")


# --- report shape -----------------------------------------------------------


def test_report_names_every_failed_item(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    report = pr_gates_dod.evaluate(
        cfg,
        make_data(
            title="no convention here",
            body="no link",
            commits=[pr_gates_dod.Commit(sha="c" * 40, message="m", verified=False)],
        ),
    )
    assert set(report.failed_names()) == {
        "signed_commits",
        "conventional_title",
        "linked_issue",
    }
    assert report.exit_code == 1


def test_summary_text_lists_failed_items(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    report = pr_gates_dod.evaluate(cfg, make_data(title="bad title", body="no link"))
    text = report.summary()
    assert "conventional_title" in text
    assert "linked_issue" in text


# --- posting: one check run, never comments ---------------------------------


def test_post_check_run_posts_once_with_configured_name(tmp_path, monkeypatch) -> None:
    cfg = pr_gates_dod.load_config(
        make_config(tmp_path, ENABLED + '\ncheck_name = "dod/custom"\n')
    )
    report = pr_gates_dod.evaluate(cfg, make_data())
    calls: list[tuple[str, dict]] = []

    def fake_api(endpoint: str, payload: dict) -> dict:
        calls.append((endpoint, payload))
        return {"id": 1}

    monkeypatch.setattr(pr_gates_dod, "github_api", fake_api)
    pr_gates_dod.post_check_run(cfg, report, repo="o/r", head_sha="a" * 40)
    check_calls = [c for c in calls if "/check-runs" in c[0]]
    assert len(check_calls) == 1
    assert "comments" not in "".join(e for e, _ in calls)
    assert check_calls[0][1]["name"] == "dod/custom"
    assert check_calls[0][1]["status"] == "completed"


def test_post_check_run_never_comments(tmp_path, monkeypatch) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    report = pr_gates_dod.evaluate(cfg, make_data())
    endpoints: list[str] = []
    monkeypatch.setattr(
        pr_gates_dod,
        "github_api",
        lambda endpoint, payload: endpoints.append(endpoint) or {"id": 1},
    )
    pr_gates_dod.post_check_run(cfg, report, repo="o/r", head_sha="a" * 40)
    assert all("comment" not in e for e in endpoints)


def test_disabled_never_posts(tmp_path, monkeypatch) -> None:
    cfg = pr_gates_dod.load_config(tmp_path / "missing.toml")
    report = pr_gates_dod.evaluate(cfg, make_data())
    posted = []
    monkeypatch.setattr(
        pr_gates_dod, "post_check_run", lambda *a, **k: posted.append(a)
    )
    pr_gates_dod.main_post_if_enabled(cfg, report)
    assert posted == []


# --- shipped files ----------------------------------------------------------


def test_repo_config_is_on_without_signed_commits() -> None:
    cfg = pr_gates_dod.load_config(CONFIG_FILE)
    assert cfg.enabled is True
    assert cfg.spec_or_fix_brief_mode == "warn"
    assert "spec_or_fix_brief" in cfg.checks
    # Agent commits are not signed yet (#755), so this would fail every agent PR.
    assert "signed_commits" not in cfg.checks

def test_workflow_file_exists_least_privilege() -> None:
    text = WORKFLOW_FILE.read_text(encoding="utf-8")
    assert "pull_request" in text
    # Only what a check run needs; no comment posting, no admin surface.
    assert "checks: write" in text
    assert "pull-requests: write" not in text
    assert "issues: read" in text
    assert "contents: read" in text


def test_workflow_runs_the_base_copy_and_no_pr_code() -> None:
    workflow = yaml.load(WORKFLOW_FILE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    # Only pull_request_target runs the base branch's copy, so a PR cannot rewrite
    # its own grade or reach the token.
    assert list(workflow["on"]) == ["pull_request_target"]
    # The title and body are graded, so an edit has to grade again.
    assert {"opened", "synchronize", "reopened", "ready_for_review", "edited"} <= set(
        workflow["on"]["pull_request_target"]["types"]
    )
    steps = workflow["jobs"]["dod"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert "ref" not in checkout.get("with", {})


def test_audit_lines_record_actions(tmp_path, capsys) -> None:
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    report = pr_gates_dod.evaluate(cfg, make_data())
    pr_gates_dod.audit(
        "post_check_run", repo="o/r", name=cfg.check_name, outcome=report.outcome
    )
    out = capsys.readouterr().out
    assert "post_check_run" in out
    assert "dod/ready-for-review" in out
    # Secrets are never logged: no token-like values in audit output.
    assert not re.search(r"(ghp_|github_pat_)[A-Za-z0-9_]+", out)


# --- collector semantics (red-first regression tests) ------------------------------
#
# Two false-green defects (bismuth exact-head review of PR #740):
#   1. collectors read only the first REST page (commits default page is 30,
#      files/comments cap at 100) and the first GraphQL reviewThreads page,
#      so later-page unsigned commits / finding ids / unresolved threads are
#      silently ignored.
#   2. base_current compared two base-branch facts (pull.base.sha vs default
#      branch tip) and never the PR head, so a PR cut from an old point
#      passed while missing commits from main. Correct contract: pass iff the
#      PR head's merge-base with the target branch equals the target tip.
#
# These tests fail before the fix (TypeError/AttributeError against the old
# PRData contract, short counts from single-page collection) and must be
# green after it.


def _install_fake_gh(monkeypatch, calls, pull_payload, branch_tip, pages):
    """Route pr_gates_dod._gh to canned payloads; record every call."""

    def fake_gh(*args):
        calls.append(list(args))
        if args[:2] == ("api", "graphql"):
            cursor = None
            for flag in args:
                if flag.startswith("cursor="):
                    cursor = flag.split("=", 1)[1]
            graph_pages = pages.get("graphql")
            if graph_pages is None:  # default: one terminal, empty page
                return {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                    "nodes": [],
                                }
                            }
                        }
                    }
                }
            return graph_pages[cursor]
        endpoint = args[1]
        if endpoint.startswith("repos/o/r/git/trees/"):
            return {"truncated": False, "tree": []}
        if endpoint.startswith("repos/o/r/compare/"):
            if "compare" not in pages:  # default: head contains base tip
                return {
                    "merge_base_commit": {"sha": branch_tip},
                    "behind_by": 0,
                    "ahead_by": 0,
                }
            return pages["compare"]
        if endpoint.startswith("repos/o/r/branches/"):
            return {"commit": {"sha": branch_tip}}
        for prefix, by_page in pages["rest"].items():
            if endpoint.startswith(prefix):
                query = endpoint.split("?", 1)[1] if "?" in endpoint else ""
                page = 1
                for kv in query.split("&"):
                    if kv.startswith("page="):
                        page = int(kv.split("=", 1)[1])
                return by_page[page]
        if endpoint == f"repos/o/r/pulls/{PR_NUM}":
            return pull_payload
        return []  # unregistered REST list endpoint: default single empty page

    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)


PR_NUM = 401
BASE_TIP = "d" * 40
HEAD_SHA = "c" * 40
MERGE_BASE_STALE = "m" * 40


def _commit(sha, verified=True):
    return {
        "sha": sha,
        "commit": {"message": "fix: x", "verification": {"verified": verified}},
    }


def _commits_pages():
    page1 = [_commit(f"{i:03d}" + "a" * 37, verified=True) for i in range(100)]
    page2 = [_commit(f"{i:03d}" + "b" * 37, verified=False) for i in range(100)]
    page3 = [_commit("z" * 40, verified=False)]
    return {1: page1, 2: page2, 3: page3}


def test_collect_commits_reads_every_page(monkeypatch):
    calls: list[list] = []
    pull = {
        "title": "t",
        "body": "Fixes #1",
        "base": {"ref": "main", "sha": BASE_TIP},
        "head": {"sha": HEAD_SHA},
    }
    pages = {"rest": {f"repos/o/r/pulls/{PR_NUM}/commits": _commits_pages()}}
    _install_fake_gh(monkeypatch, calls, pull, BASE_TIP, pages)
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM, root="/nonexistent")
    assert len(data.commits) == 201
    assert data.commits[-1].sha == "z" * 40
    requested = [a[1] for a in calls if a[0] == "api" and "commits" in a[1]]
    assert any("page=2" in e for e in requested), "second commits page never requested"
    assert any("page=3" in e for e in requested), "third commits page never requested"
    assert any(
        "per_page=100" in e for e in requested
    ), "commits fetched with REST default page size"


def test_collect_files_and_comments_read_every_page(monkeypatch):
    calls: list[list] = []
    files = {
        1: [{"filename": f"f{i}.py"} for i in range(100)],
        2: [{"filename": "late.py"}],
    }
    review = {1: [{"body": f"r{i}"} for i in range(100)], 2: [{"body": "late-finding"}]}
    pull = {
        "title": "t",
        "body": "Fixes #1",
        "base": {"ref": "main", "sha": BASE_TIP},
        "head": {"sha": HEAD_SHA},
    }
    pages = {
        "rest": {
            f"repos/o/r/pulls/{PR_NUM}/files": files,
            f"repos/o/r/pulls/{PR_NUM}/comments": review,
            f"repos/o/r/issues/{PR_NUM}/comments": {1: []},
            f"repos/o/r/pulls/{PR_NUM}/commits": {1: []},
        }
    }
    _install_fake_gh(monkeypatch, calls, pull, BASE_TIP, pages)
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM, root="/nonexistent")
    assert len(data.changed_files) == 101 and "late.py" in data.changed_files
    assert "late-finding" in data.review_comment_bodies


def test_collect_reads_bodies_of_explicitly_linked_issues(monkeypatch, tmp_path):
    calls: list[list] = []
    pull = {
        "title": "feat(cli): add a command",
        "body": "Fixes #984 and references #985",
        "base": {"ref": "main", "sha": BASE_TIP},
        "head": {"sha": HEAD_SHA},
    }
    pages = {
        "rest": {
            "repos/o/r/issues/984": {1: {"body": "Spec: docs/specs/command.md"}},
            "repos/o/r/issues/985": {1: {"body": "No specification link"}},
        }
    }
    _install_fake_gh(monkeypatch, calls, pull, BASE_TIP, pages)
    data = pr_gates_dod.collect_pr_data(
        "o/r", PR_NUM, root="/nonexistent", need_spec_or_fix_brief=True
    )
    assert data.linked_issue_bodies == {984: "Spec: docs/specs/command.md", 985: "No specification link"}
    assert "spec_or_fix_brief" not in pr_gates_dod.evaluate(
        spec_gate_config(tmp_path, mode="warn"), data
    ).failed_names()


def test_collect_reads_test_blob_from_pr_head_not_base_checkout(monkeypatch, tmp_path):
    blob_sha = "a" * 40
    pull = {"title": "t", "body": "Fixes #1", "base": {"ref": "main"}, "head": {"sha": HEAD_SHA}}
    pages = {"rest": {f"repos/o/r/pulls/{PR_NUM}/files": {1: [
        # The mutable PR listing may have seen a transient different head.
        {"filename": "tests/test_x.py", "sha": "b" * 40, "status": "modified"},
    ]}}}
    _install_fake_gh(monkeypatch, [], pull, BASE_TIP, pages)
    old_gh = pr_gates_dod._gh

    def fake_gh(*args):
        if args[1] == f"repos/o/r/git/trees/{HEAD_SHA}?recursive=1":
            return {"truncated": False, "tree": [{"path": "tests/test_x.py", "type": "blob", "mode": "100644",
                                                   "sha": blob_sha, "size": len(b"# F1 head test")}]}
        return old_gh(*args)

    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)
    monkeypatch.setattr(pr_gates_dod, "_gh_blob", lambda endpoint, limit: {
        "sha": blob_sha, "size": len(b"# F1 head test"), "encoding": "base64",
        "content": base64.b64encode(b"# F1 head test").decode(),
    })
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text("# base without the finding")
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM, root=str(tmp_path))
    assert data.changed_file_contents == {"tests/test_x.py": "# F1 head test"}

    monkeypatch.setattr(pr_gates_dod, "_gh_blob", lambda endpoint, limit: {
        "sha": "b" * 40, "encoding": "base64", "content": "",
    })
    with pytest.raises(RuntimeError, match="invalid blob response"):
        pr_gates_dod.collect_pr_data("o/r", PR_NUM, root=str(tmp_path))

    monkeypatch.setattr(pr_gates_dod, "_gh_blob", lambda endpoint, limit: {
        "sha": blob_sha, "size": len(b"# F1 head test"), "encoding": "base64",
        "content": base64.b64encode(b"# F1 head test").decode(),
    })

    pull_reads = 0

    def changed_head(*args):
        nonlocal pull_reads
        if args[1] == f"repos/o/r/pulls/{PR_NUM}":
            pull_reads += 1
            if pull_reads == 2:
                return {**pull, "head": {"sha": "b" * 40}}
        return fake_gh(*args)

    monkeypatch.setattr(pr_gates_dod, "_gh", changed_head)
    with pytest.raises(RuntimeError, match="head changed"):
        pr_gates_dod.collect_pr_data("o/r", PR_NUM, root=str(tmp_path))


def test_blob_response_is_bounded_before_json_decode(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read1(self, size): return b"x" * size

    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setattr(pr_gates_dod.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="exceeds limit"):
        pr_gates_dod._gh_blob("fake", 8)


def test_blob_deadline_ends_slow_stream(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read1(self, _size): return b"x"

    ticks = iter((0, 1, 31))
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-token")
    monkeypatch.setattr(pr_gates_dod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(pr_gates_dod.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="timed out"):
        pr_gates_dod._gh_blob("fake", 100)


def test_changed_test_tree_excludes_unchanged_and_deleted_and_rejects_truncation(monkeypatch):
    pull = {"title": "t", "body": "Fixes #1", "base": {"ref": "main"}, "head": {"sha": HEAD_SHA}}
    _install_fake_gh(monkeypatch, [], pull, BASE_TIP, {"rest": {}})
    old_gh = pr_gates_dod._gh
    def entry(path, sha):
        return {"path": path, "type": "blob", "mode": "100644", "sha": sha, "size": 1}
    base = [entry("tests/same.py", "a" * 40), entry("tests/deleted.py", "b" * 40),
            entry("tests/changed.py", "c" * 40)]
    head = [entry("tests/same.py", "a" * 40), entry("tests/changed.py", "d" * 40),
            entry("tests/added.py", "e" * 40)]
    truncated = False
    def fake_gh(*args):
        if args[1] == f"repos/o/r/git/trees/{BASE_TIP}?recursive=1":
            return {"truncated": truncated, "tree": base}
        if args[1] == f"repos/o/r/git/trees/{HEAD_SHA}?recursive=1":
            return {"truncated": False, "tree": head}
        return old_gh(*args)
    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)
    monkeypatch.setattr(pr_gates_dod, "_gh_blob", lambda endpoint, limit: {
        "sha": endpoint.rsplit("/", 1)[1], "size": 1, "encoding": "base64", "content": "eA==",
    })
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM)
    assert data.changed_file_contents == {"tests/changed.py": "x", "tests/added.py": "x"}
    truncated = True
    with pytest.raises(RuntimeError, match="incomplete commit tree"):
        pr_gates_dod.collect_pr_data("o/r", PR_NUM)


def test_oversized_test_tree_entry_is_rejected_before_blob_fetch(monkeypatch):
    pull = {"title": "t", "body": "Fixes #1", "base": {"ref": "main"}, "head": {"sha": HEAD_SHA}}
    _install_fake_gh(monkeypatch, [], pull, BASE_TIP, {"rest": {}})
    old_gh = pr_gates_dod._gh
    def fake_gh(*args):
        if args[1] == f"repos/o/r/git/trees/{HEAD_SHA}?recursive=1":
            return {"truncated": False, "tree": [{"path": "tests/huge.py", "type": "blob", "mode": "100644",
                                                   "sha": "a" * 40, "size": 1_000_001}]}
        return old_gh(*args)
    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)
    def unexpected_blob(*_args):
        pytest.fail("oversized blob must not be fetched")
    monkeypatch.setattr(pr_gates_dod, "_gh_blob", unexpected_blob)
    with pytest.raises(RuntimeError, match="invalid test blob size"):
        pr_gates_dod.collect_pr_data("o/r", PR_NUM)


def test_collect_pr_data_keeps_comment_identity_and_review_approval_metadata(monkeypatch):
    calls: list[list] = []
    pull = {
        "title": "t",
        "body": "Fixes #1",
        "user": {"login": "pr-author"},
        "base": {"ref": "main", "sha": BASE_TIP},
        "head": {"sha": HEAD_SHA},
    }
    review_comment = {
        "id": 100,
        "body": "F11 was reported by the test suite.",
        "user": {"login": "inline-reviewer"},
    }
    issue_comment = {
        "id": 200,
        "body": "<!-- pr-gates-ignore-reference:v1 -->",
        "user": {"login": "trusted-reviewer"},
    }
    review = {
        "id": 300,
        "body": "",
        "user": {"login": "trusted-reviewer"},
        "state": "APPROVED",
        "commit_id": HEAD_SHA,
        "submitted_at": "2026-11-08T01:02:03Z",
    }
    pages = {
        "rest": {
            f"repos/o/r/pulls/{PR_NUM}/comments": {1: [review_comment]},
            f"repos/o/r/issues/{PR_NUM}/comments": {1: [issue_comment]},
            f"repos/o/r/pulls/{PR_NUM}/reviews": {1: [review]},
            f"repos/o/r/pulls/{PR_NUM}/commits": {1: []},
        }
    }
    _install_fake_gh(monkeypatch, calls, pull, BASE_TIP, pages)
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM, root="/nonexistent")
    assert data.pull_author_login == "pr-author"
    assert data.head_sha == HEAD_SHA
    assert data.comments == (
        pr_gates_dod.PRComment("review_comment", 100, review_comment["body"], "inline-reviewer"),
        pr_gates_dod.PRComment("issue_comment", 200, issue_comment["body"], "trusted-reviewer"),
    )
    assert data.reviews == (
        pr_gates_dod.PRReview(300, "trusted-reviewer", "APPROVED", HEAD_SHA, "2026-11-08T01:02:03Z"),
    )
    assert any(
        args[0] == "api" and args[1].startswith(f"repos/o/r/pulls/{PR_NUM}/reviews?")
        for args in calls
    )


def test_collect_threads_follow_graphql_pagination(monkeypatch):
    calls: list[list] = []
    nodes1 = [{"isResolved": True} for _ in range(99)] + [{"isResolved": False}]
    nodes2 = [{"isResolved": True} for _ in range(6)] + [{"isResolved": False}]
    page1 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "CUR1"},
                        "nodes": nodes1,
                    }
                }
            }
        }
    }
    page2 = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": nodes2,
                    }
                }
            }
        }
    }
    pull = {
        "title": "t",
        "body": "Fixes #1",
        "base": {"ref": "main", "sha": BASE_TIP},
        "head": {"sha": HEAD_SHA},
    }
    pages = {
        "rest": {f"repos/o/r/pulls/{PR_NUM}/commits": {1: []}},
        "graphql": {None: page1, "CUR1": page2},
    }
    _install_fake_gh(monkeypatch, calls, pull, BASE_TIP, pages)
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM, root="/nonexistent")
    assert data.unresolved_threads == 2  # one unresolved thread lives on page 2
    graphql_calls = [a for a in calls if a[:2] == ["api", "graphql"]]
    assert len(graphql_calls) == 2, "reviewThreads second page never requested"
    assert any(
        "cursor=CUR1" in flag for flag in graphql_calls[1]
    ), "endCursor not passed"


def test_collect_base_current_uses_compare_merge_base(monkeypatch):
    calls: list[list] = []
    compare = {
        "merge_base_commit": {"sha": MERGE_BASE_STALE},
        "behind_by": 5,
        "ahead_by": 2,
    }
    pull = {
        "title": "t",
        "body": "Fixes #1",
        "base": {"ref": "main", "sha": MERGE_BASE_STALE},
        "head": {"sha": HEAD_SHA},
    }
    pages = {"rest": {f"repos/o/r/pulls/{PR_NUM}/commits": {1: []}}, "compare": compare}
    _install_fake_gh(monkeypatch, calls, pull, BASE_TIP, pages)
    data = pr_gates_dod.collect_pr_data("o/r", PR_NUM, root="/nonexistent")
    assert data.merge_base_sha == MERGE_BASE_STALE
    assert data.commits_behind_base == 5
    assert data.default_branch_head == BASE_TIP
    assert any(
        a[0] == "api" and a[1].startswith(f"repos/o/r/compare/main...{HEAD_SHA}")
        for a in calls
    ), "PR head never compared against the target branch"


def test_base_current_flags_pr_head_behind_main(tmp_path: Path) -> None:
    """The exact false-green: PR cut from an old point, main has moved on.

    Old contract passed this (base-branch facts agreed); new contract must
    fail base_current because the PR head's merge-base is behind main's tip.
    """
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED))
    data = pr_gates_dod.PRData(
        title="fix: t",
        body="Fixes #1\n",
        commits=(),
        changed_files=[],
        unresolved_threads=0,
        review_comment_bodies=(),
        default_branch="main",
        default_branch_head=BASE_TIP,
        merge_base_sha=MERGE_BASE_STALE,
        commits_behind_base=5,
    )
    report = pr_gates_dod.evaluate(cfg, data)
    assert "base_current" in report.failed_names()
    assert "behind" in report.detail_for("base_current")
    fresh = pr_gates_dod.PRData(
        title="fix: t",
        body="Fixes #1\n",
        commits=(),
        changed_files=[],
        unresolved_threads=0,
        review_comment_bodies=(),
        default_branch="main",
        default_branch_head=BASE_TIP,
        merge_base_sha=BASE_TIP,
        commits_behind_base=0,
    )
    ok = pr_gates_dod.evaluate(cfg, fresh)
    assert "base_current" not in ok.failed_names()


# --- pagination liveness (red-first regression tests) ------------------------------
#
# Bismuth's bounded probes against PR #740 at 0c1ee3a4 reproduced two hang
# paths: a REST list endpoint that returns the same full page for every page
# parameter loops _gh_paginate forever, and reviewThreads pagination that
# advertises hasNextPage=true with a null or repeated endCursor loops the
# GraphQL collector forever. A gate script must hang never and silently
# truncate never, so both pathological shapes must fail closed with a loud
# error within a bounded number of API calls. The fakes below enforce the
# bound themselves (raising AssertionError past it) so a red run fails fast
# instead of hanging.


def test_gh_paginate_fails_closed_on_identical_consecutive_pages(monkeypatch):
    calls: list[list] = []

    def fake_gh(*args):
        calls.append(list(args))
        if len(calls) > 4:
            raise AssertionError("unbounded loop: more than 4 pages requested")
        return [_commit(f"{i:03d}" + "r" * 37) for i in range(100)]

    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)
    with pytest.raises(RuntimeError, match="identical"):
        pr_gates_dod._gh_paginate("repos/o/r/pulls/401/commits")
    assert len(calls) <= 4


def test_gh_paginate_fails_closed_on_page_cap(monkeypatch):
    calls: list[list] = []

    def fake_gh(*args):
        calls.append(list(args))
        if len(calls) > pr_gates_dod.PAGINATION_MAX_PAGES + 1:
            raise AssertionError("unbounded loop: more than the documented page cap")
        return [_commit(f"p{calls[-1][1]}-{i:03d}") for i in range(100)]

    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)
    with pytest.raises(RuntimeError, match="failing closed"):
        pr_gates_dod._gh_paginate("repos/o/r/pulls/401/commits")
    assert len(calls) <= pr_gates_dod.PAGINATION_MAX_PAGES + 1


def _thread_page(has_next, end_cursor):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                        "nodes": [],
                    }
                }
            }
        }
    }


def test_thread_pagination_fails_closed_on_null_end_cursor(monkeypatch):
    calls: list[list] = []

    def fake_gh(*args):
        calls.append(list(args))
        graphql_calls = [c for c in calls if c[:2] == ["api", "graphql"]]
        if len(graphql_calls) > 4:
            raise AssertionError("unbounded loop: more than 4 graphql calls")
        if args[:2] == ("api", "graphql"):
            return _thread_page(True, None)  # hasNextPage but no cursor
        if args[1] == f"repos/o/r/pulls/{PR_NUM}":
            return {
                "title": "t",
                "body": "Fixes #1",
                "base": {"ref": "main", "sha": BASE_TIP},
                "head": {"sha": HEAD_SHA},
            }
        if args[1].startswith("repos/o/r/branches/"):
            return {"commit": {"sha": BASE_TIP}}
        if args[1].startswith("repos/o/r/compare/"):
            return {
                "merge_base_commit": {"sha": BASE_TIP},
                "behind_by": 0,
                "ahead_by": 0,
            }
        return []  # REST list endpoints: one empty page

    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)
    with pytest.raises(RuntimeError, match="endCursor"):
        pr_gates_dod.collect_pr_data("o/r", PR_NUM, root="/nonexistent")
    graphql_made = [c for c in calls if c[:2] == ["api", "graphql"]]
    assert len(graphql_made) <= 4


def test_thread_pagination_fails_closed_on_repeated_end_cursor(monkeypatch):
    calls: list[list] = []

    def fake_gh(*args):
        calls.append(list(args))
        graphql_calls = [c for c in calls if c[:2] == ["api", "graphql"]]
        if len(graphql_calls) > 5:
            raise AssertionError("unbounded loop: more than 5 graphql calls")
        if args[:2] == ("api", "graphql"):
            return _thread_page(True, "CUR1")  # same cursor on every page
        if args[1] == f"repos/o/r/pulls/{PR_NUM}":
            return {
                "title": "t",
                "body": "Fixes #1",
                "base": {"ref": "main", "sha": BASE_TIP},
                "head": {"sha": HEAD_SHA},
            }
        if args[1].startswith("repos/o/r/branches/"):
            return {"commit": {"sha": BASE_TIP}}
        if args[1].startswith("repos/o/r/compare/"):
            return {
                "merge_base_commit": {"sha": BASE_TIP},
                "behind_by": 0,
                "ahead_by": 0,
            }
        return []  # REST list endpoints: one empty page

    monkeypatch.setattr(pr_gates_dod, "_gh", fake_gh)
    with pytest.raises(RuntimeError, match="endCursor"):
        pr_gates_dod.collect_pr_data("o/r", PR_NUM, root="/nonexistent")
    graphql_made = [c for c in calls if c[:2] == ["api", "graphql"]]
    assert len(graphql_made) <= 5


# --- second review round (issue #724) --------------------------------------


def test_main_disabled_never_calls_github(tmp_path, monkeypatch, capsys) -> None:
    """Off means off: no API calls, so a gh failure cannot turn the job red."""
    cfg = make_config(tmp_path, "[pr_gates.dod]\nenabled = false\n")

    def must_not_run(*_args, **_kwargs):
        raise RuntimeError("disabled gate called GitHub")

    monkeypatch.setattr(pr_gates_dod, "collect_pr_data", must_not_run)
    assert pr_gates_dod.main(["--config", str(cfg), "--repo", "o/r", "--pr", "1"]) == 0
    assert "disabled" in capsys.readouterr().out


@pytest.mark.parametrize(
    "test_content,covered",
    [
        ("def test_f1_keeps_order(): ...", True),
        ("# Regression for F1.", True),
        ("assert conf1g == 2", False),
        ("def test_f12_other(): ...", False),
        ("value = 0xabcf1", False),
    ],
)
def test_regression_test_matches_whole_finding_id(tmp_path, test_content, covered) -> None:
    cfg = pr_gates_dod.load_config(
        make_config(tmp_path, '[pr_gates.dod]\nenabled = true\nchecks = ["regression_test"]\n')
    )
    data = make_data(
        review_comment_bodies=("F1: the base check is wrong",),
        changed_file_contents={"tests/test_x.py": test_content},
    )
    report = pr_gates_dod.evaluate(cfg, data)
    assert report.failed_names() == ([] if covered else ["regression_test"])


def test_default_finding_pattern_ignores_words_that_contain_an_id(tmp_path) -> None:
    cfg = pr_gates_dod.load_config(
        make_config(tmp_path, '[pr_gates.dod]\nenabled = true\nchecks = ["regression_test"]\n')
    )
    data = make_data(
        review_comment_bodies=("Files are read as UTF8.",),
        changed_file_contents={},
    )
    assert pr_gates_dod.evaluate(cfg, data).failed == []


def test_regression_suppressions_require_exact_trusted_annotation(tmp_path):
    cfg = regression_cfg(tmp_path)
    source = "F11 had one failure and three errors. F11 rerun alone passed."
    markers = tuple(
        ignore_reference_comment(
            500 + occurrence,
            source_kind="issue_comment",
            source_id=100,
            source_body=source,
            finding_id="F11",
            occurrence=occurrence,
        )
        for occurrence in (1, 2)
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), *markers),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    report = pr_gates_dod.evaluate(cfg, data)
    assert report.detail_for("regression_test") == ""
    assert len(report.audit_notes) == 2
    assert "F11 occurrence 1 in issue_comment 100" in report.summary()
    assert all(
        note.source_body_sha256 == hashlib.sha256(source.encode()).hexdigest()
        for note in report.audit_notes
    )


def test_regression_code_default_trusts_no_reviewer(tmp_path):
    cfg = pr_gates_dod.load_config(make_config(tmp_path, ENABLED + '\nchecks = ["regression_test"]\n'))
    source = "F11 test suite failed."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="F11",
        occurrence=1,
        author="kollaborai",
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=(approved_review(author="kollaborai"),),
        changed_file_contents={},
    )
    assert cfg.regression.trusted_reviewers == []
    assert "F11" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


@pytest.mark.parametrize(
    "changes",
    [
        {"body_hash": "0" * 64},
        {"author": "untrusted"},
        {"author": "pr-author"},
        {"source_id": 999},
        {"source_kind": "review_comment"},
        {"occurrence": 3},
    ],
)
def test_regression_rejects_invalid_or_untrusted_suppressions(tmp_path, changes):
    cfg = regression_cfg(tmp_path, trusted_reviewers=("trusted-reviewer", "pr-author"))
    source = "F14: test failed."
    marker_args = {
        "source_kind": "issue_comment",
        "source_id": 100,
        "source_body": source,
        "finding_id": "F14",
        "occurrence": 1,
    }
    marker_args.update(changes)
    marker = ignore_reference_comment(501, **marker_args)
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    assert "F14" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


@pytest.mark.parametrize(
    "review_changes",
    [
        {"state": "DISMISSED"},
        {"state": "CHANGES_REQUESTED"},
        {"stale_head": True},
    ],
)
def test_regression_requires_latest_trusted_approval_on_current_head(tmp_path, review_changes):
    cfg = regression_cfg(tmp_path)
    source = "F11 test suite had one failure."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="F11",
        occurrence=1,
    )
    if review_changes.get("stale_head"):
        reviews = (approved_review(),)
        head_sha = "a" * 40
    else:
        reviews = (approved_review(), approved_review(10, **review_changes))
        head_sha = "c" * 40
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=reviews,
        head_sha=head_sha,
        changed_file_contents={},
    )
    assert "F11" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


def test_regression_keeps_pr553_f1_f6_and_requires_all_f11_occurrences(tmp_path):
    cfg = regression_cfg(tmp_path)
    findings = """\
[P1] Unsupported model effort can reach the API (F1)
[P2] Effort catalogs regress across profiles (F3 / F2 regression)
[P2] A no-effort backend retains stale state (F4)
[P2] Polling omits the current effort (F4)
[P2] F5 remains open: activation returns a stale snapshot
[P2] F6 is only partly fixed: default selection is skipped
"""
    full_unit = (
        "- Full unit suite: 8,990 passed, 23 failed, 3 errors; all 26 failures "
        "also failed on origin/main. F11 rerun alone on each revision: "
        "1 failed, 8 passed, 3 errors."
    )
    rerun_file = (
        "F11 and the\nprovider-registry file were also run separately on both "
        "revisions: F11 had one failure and three errors on each."
    )
    markers = (
        ignore_reference_comment(
            601,
            source_kind="issue_comment",
            source_id=201,
            source_body=full_unit,
            finding_id="F11",
            occurrence=1,
        ),
        ignore_reference_comment(
            602,
            source_kind="issue_comment",
            source_id=202,
            source_body=rerun_file,
            finding_id="F11",
            occurrence=1,
        ),
        ignore_reference_comment(
            603,
            source_kind="issue_comment",
            source_id=202,
            source_body=rerun_file,
            finding_id="F11",
            occurrence=2,
        ),
    )
    sources = (
        source_comment("issue_comment", 200, findings),
        source_comment("issue_comment", 201, full_unit),
        source_comment("issue_comment", 202, rerun_file),
    )
    valid = make_data(
        comments=(*sources, *markers), reviews=(approved_review(),), changed_file_contents={}
    )
    detail = pr_gates_dod.evaluate(cfg, valid).detail_for("regression_test")
    assert detail == "no regression test covers finding id(s): F1, F2, F3, F4, F5, F6 under tests/"
    assert "F11" not in detail

    unannotated = make_data(comments=sources, changed_file_contents={})
    assert "F11" in pr_gates_dod.evaluate(cfg, unannotated).detail_for("regression_test")


def test_regression_one_annotation_does_not_suppress_repeated_id(tmp_path):
    cfg = regression_cfg(tmp_path)
    source = "F11 failed here; F11 failed again."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="F11",
        occurrence=1,
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    assert "F11" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


def test_regression_duplicate_annotations_are_ambiguous_and_do_not_suppress(tmp_path):
    cfg = regression_cfg(tmp_path)
    source = "F11 test suite failed."
    markers = tuple(
        ignore_reference_comment(
            marker_id,
            source_kind="issue_comment",
            source_id=100,
            source_body=source,
            finding_id="F11",
            occurrence=1,
        )
        for marker_id in (501, 502)
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), *markers),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    assert "F11" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


def test_regression_missing_pr_author_metadata_fails_closed(tmp_path):
    cfg = regression_cfg(tmp_path)
    source = "F11 test suite failed."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="F11",
        occurrence=1,
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=(approved_review(),),
        pull_author_login="",
        changed_file_contents={},
    )
    assert "F11" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


def test_regression_pending_review_does_not_override_approved_review(tmp_path):
    cfg = regression_cfg(tmp_path)
    source = "F11 test suite failed."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="F11",
        occurrence=1,
    )
    pending = pr_gates_dod.PRReview(
        review_id=10,
        author_login="trusted-reviewer",
        state="PENDING",
        commit_id="c" * 40,
        submitted_at="",
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=(approved_review(), pending),
        changed_file_contents={},
    )
    assert pr_gates_dod.evaluate(cfg, data).detail_for("regression_test") == ""


def test_main_audits_accepted_exact_reference_suppression(tmp_path, monkeypatch, capsys):
    cfg = make_config(
        tmp_path,
        ENABLED
        + '\nchecks = ["regression_test"]\n'
        + '\n[pr_gates.dod.regression_profile]\n'
        + 'trusted_reviewers = ["trusted-reviewer"]\n',
    )
    source = "F11 test suite failed."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="F11",
        occurrence=1,
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    monkeypatch.setattr(pr_gates_dod, "collect_pr_data", lambda *_args, **_kwargs: data)
    assert pr_gates_dod.main(
        ["--config", str(cfg), "--repo", "o/r", "--pr", "1"]
    ) == 0
    output = capsys.readouterr().out
    assert "Audited finding-reference suppressions" in output
    assert "pr-gates action=finding_reference_suppressed" in output
    assert "source_body_sha256=" in output
    assert "approval_review_id=9" in output


def test_regression_malformed_or_non_marker_only_comment_cannot_suppress(tmp_path):
    cfg = regression_cfg(tmp_path)
    source = "F11 test suite failed."
    malformed = source_comment(
        "issue_comment",
        501,
        '<!-- pr-gates-ignore-reference:v1\n{"finding_id":"F11",\n-->\nF11 is harmless',
        author="trusted-reviewer",
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), malformed),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    assert "F11" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


def test_regression_metadata_mismatch_disables_all_suppressions(tmp_path):
    cfg = regression_cfg(tmp_path)
    source = "F11 test suite failed."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="F11",
        occurrence=1,
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        review_comment_bodies=(source,),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    assert "F11" in pr_gates_dod.evaluate(cfg, data).detail_for("regression_test")


def test_regression_custom_pattern_and_legacy_comments_stay_fail_closed(tmp_path):
    config = make_config(
        tmp_path,
        ENABLED
        + '\nchecks = ["regression_test"]\n'
        + '\n[pr_gates.dod.regression_profile]\n'
        + 'finding_id_pattern = "BUG-[0-9]+"\n'
        + 'trusted_reviewers = ["trusted-reviewer"]\n',
    )
    cfg = pr_gates_dod.load_config(config)
    source = "BUG-42 remains exploitable."
    marker = ignore_reference_comment(
        501,
        source_kind="issue_comment",
        source_id=100,
        source_body=source,
        finding_id="BUG-42",
        occurrence=1,
    )
    data = make_data(
        comments=(source_comment("issue_comment", 100, source), marker),
        reviews=(approved_review(),),
        changed_file_contents={},
    )
    assert pr_gates_dod.evaluate(cfg, data).detail_for("regression_test") == ""

    legacy = make_data(review_comment_bodies=("BUG-42 remains exploitable",), changed_file_contents={})
    assert "BUG-42" in pr_gates_dod.evaluate(cfg, legacy).detail_for("regression_test")


@pytest.mark.parametrize(
    "body,linked",
    [
        ("Refs #722", True),
        ("Fixed #1", True),
        ("closed #2", True),
        ("Resolves #3", True),
        ("References #4", True),
        ("See #5", False),
        ("prefixes #6", False),
    ],
)
def test_linked_issue_accepts_github_keywords_and_refs(tmp_path, body, linked) -> None:
    cfg = pr_gates_dod.load_config(
        make_config(tmp_path, '[pr_gates.dod]\nenabled = true\nchecks = ["linked_issue"]\n')
    )
    report = pr_gates_dod.evaluate(cfg, make_data(body=body))
    assert report.failed_names() == ([] if linked else ["linked_issue"])
