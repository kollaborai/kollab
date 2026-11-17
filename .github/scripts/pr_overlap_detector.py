"""Flag open pull requests that appear to duplicate or overlap one another."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Iterable

_ISSUE_REF = re.compile(
    r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?|refs?)\s+#(\d+)\b", re.IGNORECASE
)
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_GH_TIMEOUT_SECONDS = 30


class OverlapMatch:
    """One open PR that overlaps the event PR, with human-readable reasons."""

    __slots__ = ("number", "reasons")

    def __init__(self, number: int, reasons: list[str]):
        self.number = number
        self.reasons = reasons


def linked_issues(pull: dict[str, Any]) -> set[int]:
    text = "\n".join(str(pull.get(key) or "") for key in ("title", "body"))
    return {int(match) for match in _ISSUE_REF.findall(text)}


def _hunk_ranges(patch: str | None) -> list[tuple[int, int]]:
    ranges = []
    for match in _HUNK.finditer(patch or ""):
        start = int(match.group(1))
        count = int(match.group(2) or 1)
        ranges.append((start, start + count))
    return ranges


def _ranges_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    left_start, left_end = left
    right_start, right_end = right
    if left_start == left_end and right_start == right_end:
        return left_start == right_start
    if left_start == left_end:
        return right_start <= left_start < right_end
    if right_start == right_end:
        return left_start <= right_start < left_end
    return max(left_start, right_start) < min(left_end, right_end)


def _files(pull: dict[str, Any]) -> dict[str, str | None]:
    return {
        str(item.get("filename")): item.get("patch")
        for item in pull.get("files", [])
        if item.get("filename")
    }


def _same_branch(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not left.get("ref") or left.get("ref") != right.get("ref"):
        return False
    left_repo = (left.get("repo") or {}).get("full_name")
    right_repo = (right.get("repo") or {}).get("full_name")
    if left_repo or right_repo:
        if left_repo and right_repo:
            return left_repo == right_repo
        left_label, right_label = left.get("label"), right.get("label")
        return bool(left_label and right_label and left_label == right_label)
    left_label, right_label = left.get("label"), right.get("label")
    return left_label == right_label if left_label and right_label else True


def _branch_reason(current: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    current_head = current.get("head", {})
    current_base = current.get("base", {})
    candidate_head = candidate.get("head", {})
    candidate_base = candidate.get("base", {})
    if _same_branch(current_head, candidate_head):
        head_ref = current_head["ref"]
        return f"both PRs use head branch `{head_ref}`"
    if _same_branch(current_head, candidate_base):
        return f"PR #{candidate.get('number')} is stacked on branch `{current_head['ref']}`"
    if _same_branch(candidate_head, current_base):
        return f"PR #{current.get('number')} is stacked on branch `{candidate_head['ref']}`"
    return None


def _file_overlap_reason(
    current: dict[str, Any], candidate: dict[str, Any]
) -> str | None:
    current_files = _files(current)
    candidate_files = _files(candidate)
    shared = sorted(current_files.keys() & candidate_files.keys())
    if len(shared) >= 2:
        return f"both PRs change {len(shared)} of the same files"
    if shared:
        left_ranges = _hunk_ranges(current_files[shared[0]])
        right_ranges = _hunk_ranges(candidate_files[shared[0]])
        if any(
            _ranges_overlap(left, right)
            for left in left_ranges
            for right in right_ranges
        ):
            return f"both PRs change overlapping hunks in `{shared[0]}`"
    return None


def find_overlaps(
    current: dict[str, Any], candidates: Iterable[dict[str, Any]]
) -> list[OverlapMatch]:
    current_number = int(current["number"])
    current_issues = linked_issues(current)
    matches: list[OverlapMatch] = []
    for candidate in candidates:
        if int(candidate.get("number", -1)) == current_number:
            continue
        if candidate.get("state", "open").lower() != "open":
            continue
        reasons = []
        for issue in sorted(current_issues & linked_issues(candidate)):
            reasons.append(f"both PRs link issue #{issue}")
        branch_reason = _branch_reason(current, candidate)
        if branch_reason:
            reasons.append(branch_reason)
        file_reason = _file_overlap_reason(current, candidate)
        if file_reason:
            reasons.append(file_reason)
        if reasons:
            matches.append(OverlapMatch(int(candidate["number"]), reasons))
    return matches


def overlap_marker(first: int, second: int) -> str:
    low, high = sorted((int(first), int(second)))
    return f"<!-- kollab-pr-overlap:{low}:{high} -->"


def overlap_comment(first: int, second: int, reasons: Iterable[str] = ()) -> str:
    first, second = sorted((int(first), int(second)))
    marker = overlap_marker(first, second)
    detail = "\n".join(f"- {reason}" for reason in reasons)
    if not detail:
        detail = "- The pull requests may represent duplicate or overlapping work."
    return (
        f"{marker}\n"
        f"Koordinator: these open pull requests may overlap: #{first} and #{second}.\n\n"
        f"{detail}\n\n"
        "Please compare the intent and changed lines, then consolidate the work or close one PR with a reason."
    )


def _gh_api(path: str, *, method: str = "GET", body: str | None = None) -> Any:
    argv = ["gh", "api", path]
    if method != "GET":
        argv.extend(["--method", method])
    if body is not None:
        argv.extend(["--input", "-"])
    try:
        proc = subprocess.run(
            argv,
            input=body,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"GitHub API request failed: {exc}") from exc
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"GitHub API exited {proc.returncode}")
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub API returned invalid JSON") from exc


def _gh_pages(path: str) -> list[dict[str, Any]]:
    argv = ["gh", "api", "--paginate", "--slurp", path]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_GH_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"GitHub API request failed: {exc}") from exc
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"GitHub API exited {proc.returncode}")
    try:
        pages = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub API returned invalid paginated JSON") from exc
    return [item for page in pages for item in page]


def _ensure_comment(repo: str, number: int, body: str, marker: str) -> None:
    comments = _gh_pages(f"repos/{repo}/issues/{number}/comments?per_page=100")
    existing = next(
        (comment for comment in comments if marker in str(comment.get("body") or "")),
        None,
    )
    if existing:
        if existing.get("body") == body:
            return
        _gh_api(
            f"repos/{repo}/issues/comments/{int(existing['id'])}",
            method="PATCH",
            body=json.dumps({"body": body}),
        )
        return
    _gh_api(
        f"repos/{repo}/issues/{number}/comments",
        method="POST",
        body=json.dumps({"body": body}),
    )


def check_pull_request(repo: str, number: int) -> list[OverlapMatch]:
    current = _gh_api(f"repos/{repo}/pulls/{number}")
    if not isinstance(current, dict) or current.get("state") != "open":
        return []
    current_files = _gh_pages(f"repos/{repo}/pulls/{number}/files?per_page=100")
    current["files"] = current_files
    candidates = _gh_pages(f"repos/{repo}/pulls?state=open&per_page=100")
    matches: list[OverlapMatch] = []
    for candidate in candidates:
        if int(candidate.get("number", -1)) == number:
            continue
        candidate["files"] = _gh_pages(
            f"repos/{repo}/pulls/{int(candidate['number'])}/files?per_page=100"
        )
        combined = find_overlaps(current, [candidate])
        if combined:
            matches.extend(combined)
    for match in matches:
        body = overlap_comment(number, match.number, match.reasons)
        marker = overlap_marker(number, match.number)
        _ensure_comment(repo, number, body, marker)
        _ensure_comment(repo, match.number, body, marker)
    return matches


def main() -> int:
    repo = os.environ.get("GH_REPO", "")
    number_text = os.environ.get("PR_NUMBER", "")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repo) or not number_text.isdigit():
        print("GH_REPO and numeric PR_NUMBER are required", file=sys.stderr)
        return 2
    try:
        matches = check_pull_request(repo, int(number_text))
    except RuntimeError as exc:
        print(f"PR overlap check failed: {exc}", file=sys.stderr)
        return 1
    if matches:
        print(f"Found {len(matches)} open PR overlap(s) for #{number_text}.")
        for match in matches:
            print(f"#{number_text} overlaps #{match.number}: {'; '.join(match.reasons)}")
    else:
        print(f"No open PR overlap found for #{number_text}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
