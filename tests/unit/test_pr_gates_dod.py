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
