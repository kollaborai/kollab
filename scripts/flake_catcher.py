#!/usr/bin/env python3
"""Record tests that failed in an earlier CI attempt and passed on rerun."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any

Gh = Callable[[Sequence[str]], str | dict[str, Any] | list[Any]]


def _run_gh(args: Sequence[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # job logs are not guaranteed to be valid UTF-8
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "gh api failed")
    return result.stdout


def _decode_payload(payload: str | dict[str, Any] | list[Any]) -> Any:
    if isinstance(payload, (dict, list)):
        return payload
    text = payload.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _api(
    gh: Gh,
    path: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    paginate: bool = False,
) -> Any:
    args = ["api", path]
    if paginate:
        args.extend(["--paginate", "--slurp"])
    if method != "GET":
        args.extend(["--method", method])
    for key, value in (fields or {}).items():
        args.extend(["-f", f"{key}={value}"])
    return _decode_payload(gh(args))


def _rows(payload: Any, key: str | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if key is not None and key in payload:
            return _rows(payload[key])
        return [payload]
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        for item in payload:
            rows.extend(_rows(item, key))
        return rows
    return []


def _api_text(gh: Gh, path: str) -> str:
    # gh refuses to print text with terminal escape codes (CI logs have colors) unless told to.
    try:
        payload = gh(["api", path, "--allow-escape-sequences"])
    except RuntimeError as exc:
        if "unknown flag" not in str(exc):
            raise
        payload = gh(["api", path])  # older gh has no such flag and no such refusal
    if not isinstance(payload, str):
        raise RuntimeError(f"{path} did not return plain-text logs")
    return payload


def parse_failed_lines(logs: str) -> list[str]:
    """Extract distinct pytest node IDs from FAILED or ERROR summary lines."""
    nodeids: list[str] = []
    seen: set[str] = set()
    timestamp_prefix = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z[ \t]+")
    summary_header = re.compile(r"=+\s*short test summary info\s*=+")
    result_line = re.compile(r"^(FAILED|ERROR|SKIPPED|XFAIL|XPASS|PASSED)\s+(.+)$")
    in_summary = False
    for line in logs.removeprefix("\ufeff").splitlines():
        line = timestamp_prefix.sub("", line, count=1)
        if summary_header.search(line):
            in_summary = True
            continue
        if not in_summary:
            continue
        result = result_line.match(line)
        if result is None:
            in_summary = False
            continue
        if result.group(1) not in ("FAILED", "ERROR"):
            continue
        summary = result.group(2).strip()
        # A parameter id may itself hold " - ", so the id ends at its closing bracket when it has one.
        match = re.match(r"\S+?\.py::[^\s\[]+(?:\[[^\]]*\])?", summary)
        nodeid = match.group(0) if match else summary.partition(" - ")[0].strip()
        if not re.match(r"(tests|packages/[\w-]+/tests)/", nodeid) or ".py::" not in nodeid:
            continue
        if nodeid not in seen:
            seen.add(nodeid)
            nodeids.append(nodeid)
    return nodeids


def build_issue_title(nodeid: str) -> str:
    if not nodeid:
        raise ValueError("test node id must not be empty")
    return f"flaky test: {nodeid}"


def build_issue_body(nodeid: str, run_url: str, attempt: int) -> str:
    if not nodeid or not run_url or attempt < 1:
        raise ValueError("test node id, run URL, and positive attempt are required")
    longest_backtick_run = max((len(run) for run in re.findall(r"`+", nodeid)), default=0)
    delimiter = "`" * (longest_backtick_run + 1)
    padding = " " if nodeid.startswith("`") or nodeid.endswith("`") else ""
    fenced_nodeid = f"{delimiter}{padding}{nodeid}{padding}{delimiter}"
    return (
        f"Test: {fenced_nodeid}\n\n"
        f"- [Workflow run attempt {attempt}]({run_url}/attempts/{attempt}) "
        "failed; a later attempt passed."
    )


def decide_issue_action(issues: list[dict[str, Any]], title: str, body: str) -> tuple[str, dict[str, Any] | None, str]:
    """Choose create, record, or no-op without changing an existing issue body."""
    matches = [issue for issue in issues if "pull_request" not in issue and issue.get("title") == title]
    if not matches:
        return "create", None, body
    issue = min(matches, key=lambda row: int(row.get("number", 0)))
    existing_body = str(issue.get("body") or "")
    _, separator, observation = body.partition("\n\n")
    if not separator or not observation:
        raise ValueError("issue body must include a run observation")
    if observation in existing_body:
        return "noop", issue, existing_body
    return "record", issue, body


def _has_observation(gh: Gh, repo: str, issue: dict[str, Any], observation: str) -> bool:
    """Check the issue body and every comment for a previously recorded observation."""
    body = str(issue.get("body") or "")
    comments = _rows(
        _api(
            gh,
            f"repos/{repo}/issues/{issue['number']}/comments?per_page=100",
            paginate=True,
        )
    )
    return observation in body or any(observation in str(comment.get("body") or "") for comment in comments)


def _record_observation(gh: Gh, repo: str, issue: dict[str, Any], body: str) -> dict[str, Any]:
    """Append one observation comment and reopen a closed issue without editing its body."""
    _, separator, observation = body.partition("\n\n")
    if not separator or not observation:
        raise ValueError("issue body must include a run observation")
    if not _has_observation(gh, repo, issue, observation):
        _api(
            gh,
            f"repos/{repo}/issues/{issue['number']}/comments",
            method="POST",
            fields={"body": observation},
        )
    if issue.get("state") == "closed":
        reopened = _api(gh, f"repos/{repo}/issues/{issue['number']}", method="PATCH", fields={"state": "open"})
        if isinstance(reopened, dict):
            return reopened
    return issue


def _fold_duplicate(gh: Gh, repo: str, title: str, created: dict[str, Any], body: str) -> dict[str, Any]:
    """Another catcher run may have opened the same issue between our read and our create.

    The issue with the lowest number wins: our observation moves onto it and our copy closes.
    The later creator always sees both, so concurrent runs converge on one open issue.
    """
    rows = _rows(_api(gh, f"repos/{repo}/issues?state=all&per_page=100", paginate=True), "issues")
    others = [
        r
        for r in rows
        if "pull_request" not in r and r.get("title") == title and r.get("number") != created.get("number")
    ]
    oldest = min(others, key=lambda r: int(r.get("number", 0)), default=None)
    if oldest is None or int(oldest["number"]) > int(created.get("number", 0)):
        return created
    action, _, _ = decide_issue_action([oldest], title, body)
    if action != "noop":
        oldest = _record_observation(gh, repo, oldest, body)
    _api(
        gh,
        f"repos/{repo}/issues/{created['number']}/comments",
        method="POST",
        fields={"body": f"Duplicate of #{oldest['number']}."},
    )
    _api(
        gh,
        f"repos/{repo}/issues/{created['number']}",
        method="PATCH",
        fields={"state": "closed"},
    )
    return oldest


def open_or_update_flaky_tests(
    repo: str,
    run_id: str,
    run_attempt: int,
    *,
    gh: Gh = _run_gh,
) -> list[dict[str, Any]]:
    """Read prior failed attempts, then create or update one issue per test."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo, flags=re.ASCII):
        raise ValueError("repo must be in owner/repo form")
    if not re.fullmatch(r"[1-9][0-9]*", str(run_id)):
        raise ValueError("run id must be a positive integer")
    if isinstance(run_attempt, bool) or run_attempt < 2:
        raise ValueError("run attempt must be at least 2")

    run = _api(gh, f"repos/{repo}/actions/runs/{run_id}")
    if run.get("conclusion") != "success":
        raise ValueError("only a successful final CI attempt can record flakes")
    if int(run.get("run_attempt", run_attempt)) != run_attempt:
        raise ValueError("run attempt did not match the GitHub workflow run")
    run_url = run.get("html_url")
    if not isinstance(run_url, str) or not run_url:
        raise ValueError("workflow run did not include a URL")

    failed_tests: dict[str, set[int]] = {}
    for attempt in range(1, run_attempt):
        jobs = _api(
            gh,
            f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100",
            paginate=True,
        )
        for job in _rows(jobs, "jobs"):
            if job.get("conclusion") not in {"failure", "timed_out"} or not job.get("id"):
                continue
            logs = _api_text(gh, f"repos/{repo}/actions/jobs/{job['id']}/logs")
            for nodeid in parse_failed_lines(logs):
                failed_tests.setdefault(nodeid, set()).add(attempt)

    if not failed_tests:
        return []

    issues = _rows(
        _api(gh, f"repos/{repo}/issues?state=all&per_page=100", paginate=True),
        "issues",
    )
    changed: list[dict[str, Any]] = []
    for nodeid in sorted(failed_tests):
        title = build_issue_title(nodeid)
        for attempt in sorted(failed_tests[nodeid]):
            body = build_issue_body(nodeid, run_url, attempt)
            action, existing, next_body = decide_issue_action(issues, title, body)
            if action == "noop":
                continue
            if action == "create":
                issue = _api(
                    gh,
                    f"repos/{repo}/issues",
                    method="POST",
                    fields={"title": title, "body": next_body},
                )
                if isinstance(issue, dict):
                    issue = _fold_duplicate(gh, repo, title, issue, body)
                    issues.append(issue)
                    changed.append(issue)
                continue

            assert existing is not None
            issue = _record_observation(gh, repo, existing, next_body)
            issues = [issue if row.get("number") == existing.get("number") else row for row in issues]
            changed.append(issue)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        changes = open_or_update_flaky_tests(args.repo, args.run_id, args.attempt)
    except (RuntimeError, ValueError) as exc:
        print(f"flake catcher: {exc}", file=sys.stderr)
        return 1
    print(f"flake catcher: updated {len(changes)} flaky test issue(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
