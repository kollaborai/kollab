"""Regression coverage for automatic duplicate-intent PR detection."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "scripts"
    / "pr_overlap_detector.py"
)
SPEC = importlib.util.spec_from_file_location("pr_overlap_detector", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
detector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(detector)


def _pr(number: int, *, title: str = "", body: str = "", head: str = "work/a", base: str = "main", files=None):
    return {
        "number": number,
        "title": title,
        "body": body,
        "head": {"ref": head},
        "base": {"ref": base},
        "files": files or [],
    }


def test_same_linked_issue_is_reported_from_title_or_body():
    current = _pr(2, body="Refs #616")
    candidate = _pr(1, title="Fixes #616")

    matches = detector.find_overlaps(current, [candidate])

    assert len(matches) == 1
    assert "issue #616" in matches[0].reasons[0].lower()


def test_same_head_branch_and_stacked_branches_are_reported():
    current = _pr(2, head="feature")
    same_head = _pr(1, head="feature")
    stacked = _pr(3, head="other", base="feature")

    matches = detector.find_overlaps(current, [same_head, stacked])

    assert {match.number for match in matches} == {1, 3}
    assert all("branch" in " ".join(match.reasons).lower() for match in matches)


def test_overlapping_hunks_and_multiple_shared_files_are_reported():
    current = _pr(
        2,
        files=[
            {"filename": "src/a.py", "patch": "@@ -10,3 +10,4 @@\n changed"},
            {"filename": "src/b.py", "patch": "@@ -20,2 +20,2 @@\n changed"},
        ],
    )
    overlapping_hunk = _pr(
        1,
        files=[{"filename": "src/a.py", "patch": "@@ -11,2 +11,2 @@\n other"}],
    )
    multiple_files = _pr(
        3,
        files=[
            {"filename": "src/a.py", "patch": "@@ -100,2 +100,2 @@\n other"},
            {"filename": "src/b.py", "patch": "@@ -200,2 +200,2 @@\n other"},
        ],
    )

    matches = detector.find_overlaps(current, [overlapping_hunk, multiple_files])

    assert {match.number for match in matches} == {1, 3}


def test_common_base_or_one_nonoverlapping_file_is_not_enough():
    current = _pr(
        2,
        files=[{"filename": "src/a.py", "patch": "@@ -10,2 +10,2 @@\n changed"}],
    )
    unrelated = _pr(
        1,
        files=[{"filename": "src/a.py", "patch": "@@ -200,2 +200,2 @@\n changed"}],
    )

    assert detector.find_overlaps(current, [unrelated]) == []


def test_comment_marker_is_stable_for_both_pr_orders():
    assert detector.overlap_marker(12, 4) == detector.overlap_marker(4, 12)
    assert detector.overlap_marker(12, 4) in detector.overlap_comment(12, 4)
