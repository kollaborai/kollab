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
    payload = gh(["api", path])
    if not isinstance(payload, str):
        raise RuntimeError(f"{path} did not return plain-text logs")
    return payload


def parse_failed_lines(logs: str) -> list[str]:
    """Extract distinct pytest node IDs from FAILED summary lines."""
    nodeids: list[str] = []
    seen: set[str] = set()
    for line in logs.splitlines():
        marker = line.find("FAILED ")
        if marker < 0:
            continue
        summary = line[marker + len("FAILED ") :].strip()
        nodeid = summary.partition(" - ")[0].strip()
        if not nodeid.startswith("tests/") or ".py::" not in nodeid:
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
    return (
        f"Test: `{nodeid}`\n\n"
        f"- [Workflow run attempt {attempt}]({run_url}/attempts/{attempt}) "
        "failed; a later attempt passed."
    )


def decide_issue_action(
    issues: list[dict[str, Any]], title: str, body: str
) -> tuple[str, dict[str, Any] | None, str]:
    """Choose create, update, or no-op while retaining prior run observations."""
    matches = [
        issue
        for issue in issues
        if "pull_request" not in issue and issue.get("title") == title
    ]
    if not matches:
        return "create", None, body
    issue = min(matches, key=lambda row: int(row.get("number", 0)))
    existing_body = str(issue.get("body") or "")
    _, separator, observation = body.partition("\n\n")
    if not separator or not observation:
        raise ValueError("issue body must include a run observation")
    if observation in existing_body:
        return "noop", issue, existing_body
    return "update", issue, f"{existing_body.rstrip()}\n\n{observation}"


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
            if job.get("conclusion") != "failure" or not job.get("id"):
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
                    issues.append(issue)
                    changed.append(issue)
                continue

            assert existing is not None
            fields = {"body": next_body}
            if existing.get("state") == "closed":
                fields["state"] = "open"
            issue = _api(
                gh,
                f"repos/{repo}/issues/{existing['number']}",
                method="PATCH",
                fields=fields,
            )
            if isinstance(issue, dict):
                issues = [
                    issue if row.get("number") == existing.get("number") else row
                    for row in issues
                ]
                changed.append(issue)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        changes = open_or_update_flaky_tests(
            args.repo, args.run_id, args.attempt
        )
    except (RuntimeError, ValueError) as exc:
        print(f"flake catcher: {exc}", file=sys.stderr)
        return 1
    print(f"flake catcher: updated {len(changes)} flaky test issue(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
