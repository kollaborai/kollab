"""Red-first tests for scripts/pr_gates_dod.py (issue #724).

Contract: a definition-of-done check that runs per PR, evaluates the
enabled checks from .github/pr-gates.toml, names every failed item in
one check run, and never posts comments.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

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
        default_branch="main",
        default_branch_head="d" * 40,
        base_sha="d" * 40,
    )
    base.update(overrides)
    return pr_gates_dod.PRData(**base)


ENABLED = """
[pr_gates.dod]
enabled = true
"""


# --- config loading --------------------------------------------------------


def test_config_defaults_disabled(tmp_path: Path) -> None:
    cfg = pr_gates_dod.load_config(tmp_path / "missing.toml")
    assert cfg.enabled is False
    assert cfg.check_name == "dod/ready-for-review"
    assert cfg.checks == ALL_CHECKS
    assert cfg.docs_paths == {}
    assert cfg.regression.finding_id_pattern == pr_gates_dod.DEFAULT_FINDING_PATTERN
    assert cfg.regression.test_paths == pr_gates_dod.DEFAULT_TEST_PATHS


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
""",
    )
    cfg = pr_gates_dod.load_config(path)
    assert cfg.enabled is True
    assert cfg.check_name == "custom/dod"
    assert cfg.checks == ["signed_commits", "docs_paths"]
    assert cfg.docs_paths == {"^packages/": ["CHANGELOG.md"]}
    assert cfg.regression.finding_id_pattern == r"BUG-\d+"
    assert cfg.regression.test_paths == ["tests/regressions/"]


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
        cfg, make_data(default_branch_head="d" * 40, base_sha="d" * 40)
    )
    assert "base_current" not in ok.failed_names()
    bad = pr_gates_dod.evaluate(
        cfg, make_data(default_branch_head="e" * 40, base_sha="d" * 40)
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


def test_repo_config_ships_disabled() -> None:
    text = CONFIG_FILE.read_text(encoding="utf-8")
    assert "enabled" in text
    cfg = pr_gates_dod.load_config(CONFIG_FILE)
    assert cfg.enabled is False


def test_workflow_file_exists_least_privilege() -> None:
    text = WORKFLOW_FILE.read_text(encoding="utf-8")
    assert "pull_request" in text
    # Only what a check run needs; no comment posting, no admin surface.
    assert "checks: write" in text
    assert "pull-requests: write" not in text
    assert "contents: read" in text


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
        if endpoint.startswith("repos/o/r/compare/"):
            return pages.get("compare")  # default None: head contains base tip
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
    graphql_calls = [a for a in calls if a[0] == "graphql"]
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
