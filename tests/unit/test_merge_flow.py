"""Merge flow dry run: precheck, fix order and pair-merge tree on a real git repo (#1472)."""

import subprocess
from pathlib import Path

import pytest

from scripts.merge_flow import Head, changed_files, jaccard, plan, ready_prs


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    (tmp_path / "b.txt").write_text("alpha\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _pr(repo: Path, number: int, path: str, text: str) -> Head:
    _git(repo, "checkout", "-q", "-b", f"pr{number}", "main")
    (repo / path).write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"pr {number}")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    base = _git(repo, "rev-parse", "main")
    return Head(number, sha, changed_files(repo, base, sha))


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer]).returncode == 0


def test_disjoint_prs_land_in_one_window_commit(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    c = _pr(repo, 3, "b.txt", "ALPHA\n")

    result = plan(repo, base, [a, c])

    assert result["clashes"] == []
    assert result["needs_fix"] == []
    assert result["landed"] == [1, 3]
    window = result["window_commit"]
    assert _is_ancestor(repo, base, window)
    assert _is_ancestor(repo, a.sha, window) and _is_ancestor(repo, c.sha, window)
    assert _git(repo, "show", f"{window}:a.txt").startswith("ONE")
    assert _git(repo, "show", f"{window}:b.txt") == "ALPHA"


def test_a_clash_marks_the_later_pr_for_a_fix_and_the_rest_still_land(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    b = _pr(repo, 2, "a.txt", "uno\ntwo\nthree\nfour\nfive\n")
    c = _pr(repo, 3, "b.txt", "ALPHA\n")

    result = plan(repo, base, [a, b, c])

    assert [x["prs"] for x in result["clashes"]] == [[1, 2]]
    assert result["clashes"][0]["files"] == ["a.txt"]
    assert result["needs_fix"] == [{"pr": 2, "depth": 1}]
    assert result["landed"] == [1, 3]
    assert not _is_ancestor(repo, b.sha, result["window_commit"])


def test_fixes_chain_when_a_pr_clashes_with_one_that_needs_a_fix(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    b = _pr(repo, 2, "a.txt", "uno\nTWO\nthree\nfour\nfive\n")
    c = _pr(repo, 3, "a.txt", "one\ndos\nthree\nfour\nfive\n")

    result = plan(repo, base, [a, b, c])

    assert result["needs_fix"] == [{"pr": 2, "depth": 1}, {"pr": 3, "depth": 2}]
    assert result["fix_rounds"] == 2


def test_a_pr_that_clashes_with_main_needs_a_fix(repo: Path) -> None:
    a = _pr(repo, 1, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    (repo / "a.txt").write_text("won\ntwo\nthree\nfour\nfive\n")
    _git(repo, "commit", "-q", "-am", "main moves")
    base = _git(repo, "rev-parse", "main")

    result = plan(repo, base, [a])

    assert result["clashes"] == [{"prs": [1], "with": "main", "files": ["a.txt"]}]
    assert result["needs_fix"] == [{"pr": 1, "depth": 1}]
    assert result["landed"] == []
    assert result["window_commit"] is None


def test_prs_that_share_files_pair_first(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    d = _pr(repo, 4, "a.txt", "ONE\ntwo\nthree\nfour\nfive\n")
    f = _pr(repo, 6, "b.txt", "ALPHA\n")
    e = _pr(repo, 5, "a.txt", "one\ntwo\nthree\nfour\nFIVE\n")

    result = plan(repo, base, [d, f, e])

    assert result["levels"][0] == [[4, 5]]
    assert result["landed"] == [4, 5, 6]
    assert _git(repo, "show", f"{result['window_commit']}:a.txt") == "ONE\ntwo\nthree\nfour\nFIVE"


def test_changed_files_ignore_changelog_fragments(repo: Path) -> None:
    base = _git(repo, "rev-parse", "main")
    _git(repo, "checkout", "-q", "-b", "frag", "main")
    (repo / "changes").mkdir()
    (repo / "changes" / "1.added.md").write_text("x\n")
    (repo / "b.txt").write_text("beta\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "frag")

    assert changed_files(repo, base, _git(repo, "rev-parse", "HEAD")) == frozenset({"b.txt"})


def test_jaccard() -> None:
    assert jaccard(frozenset(), frozenset()) == 0.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)


def _check(name: str, state: str) -> dict[str, str]:
    return {"__typename": "StatusContext", "context": name, "state": state}


def test_ready_prs_need_approval_green_required_checks_and_no_draft() -> None:
    green = [_check(name, "SUCCESS") for name in ("ci/tests", "ci/standards-check", "ci/security-scan")]
    items = [
        {"number": 10, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": green},
        {"number": 11, "isDraft": True, "reviewDecision": "APPROVED", "statusCheckRollup": green},
        {"number": 12, "isDraft": False, "reviewDecision": "REVIEW_REQUIRED", "statusCheckRollup": green},
        {"number": 13, "isDraft": False, "reviewDecision": "APPROVED", "statusCheckRollup": green[:2]},
        {
            "number": 9,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": c["context"], "conclusion": "SUCCESS"} for c in green
            ],
        },
        {
            "number": 14,
            "isDraft": False,
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [*green[:2], _check("ci/security-scan", "FAILURE")],
        },
    ]

    assert ready_prs(items) == [9, 10]
