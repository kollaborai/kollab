"""Open one priority issue for a failed CI run on the main branch."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(decoded, str):
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
    return decoded


def _api(
    gh: Gh,
    path: str,
    *,
    method: str = "GET",
    fields: dict[str, str | list[str]] | None = None,
    paginate: bool = False,
) -> Any:
    args = ["api", path]
    if paginate:
        args.extend(["--paginate", "--slurp"])
    if method != "GET":
        args.extend(["--method", method])
    for key, value in (fields or {}).items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            field_name = f"{key}[]" if isinstance(value, list) else key
            args.extend(["-f", f"{field_name}={item}"])
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


def _issue_body(
    sha: str,
    run_url: str,
    failed_jobs: list[str],
    pull: dict[str, Any] | None,
) -> str:
    job_lines = [f"- {name}" for name in failed_jobs]
    if not job_lines:
        job_lines = ["- No failed job names were reported."]
    lines = [
        f"<!-- main-red:{sha} -->",
        "## Main branch CI failure",
        "",
        f"Workflow run: {run_url}",
        "",
        "Failed jobs:",
        *job_lines,
    ]
    if pull:
        lines.extend(
            [
                "",
                f"Pull request: [#{pull['number']}]({pull['html_url']})",
            ]
        )
    lines.extend(
        [
            "",
            "Try reverting the merge with:",
            "",
            f"`git revert -m 1 {sha}`",
        ]
    )
    return "\n".join(lines)


def open_main_red_issue(repo: str, run_id: str, *, gh: Gh = _run_gh) -> dict[str, Any]:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo, flags=re.ASCII):
        raise ValueError("repo must be in owner/repo form")
    if not re.fullmatch(r"[1-9][0-9]*", str(run_id)):
        raise ValueError("run id must be a positive integer")

    run = _api(gh, f"repos/{repo}/actions/runs/{run_id}")
    sha = run.get("head_sha")
    run_url = run.get("html_url")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise ValueError("workflow run did not include a valid head SHA")
    if not isinstance(run_url, str) or not run_url:
        raise ValueError("workflow run did not include a URL")

    jobs_payload = _api(
        gh,
        f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
        paginate=True,
    )
    failed_jobs = [
        str(job["name"])
        for job in _rows(jobs_payload, "jobs")
        if job.get("conclusion") == "failure" and job.get("name")
    ]

    pulls_payload = _api(
        gh,
        f"repos/{repo}/commits/{sha}/pulls?per_page=100",
        paginate=True,
    )
    pulls = _rows(pulls_payload, "pulls")
    pull = pulls[0] if pulls else None

    marker = f"<!-- main-red:{sha} -->"
    issues_payload = _api(
        gh,
        f"repos/{repo}/issues?state=all&per_page=100",
        paginate=True,
    )
    existing = [
        issue
        for issue in _rows(issues_payload, "issues")
        if "pull_request" not in issue and marker in str(issue.get("body", ""))
    ]
    if existing:
        keep = min(existing, key=lambda row: row["number"])
        has_open_issue = any(row.get("state") != "closed" for row in existing)
        if keep.get("state") == "closed" and has_open_issue:
            _api(gh, f"repos/{repo}/issues/{keep['number']}", method="PATCH", fields={"state": "open"})
        for duplicate in existing:
            if duplicate is not keep and duplicate.get("state") != "closed":
                _api(
                    gh,
                    f"repos/{repo}/issues/{duplicate['number']}",
                    method="PATCH",
                    fields={"state": "closed"},
                )
        print(f"main red issue already exists: {keep.get('html_url', keep.get('number'))}")
        _ensure_pr_comment(gh, repo, pull, keep)
        return keep

    title = f"main CI red at {sha[:7]}"
    if pull:
        title += f" after #{pull['number']}"
    body = _issue_body(sha, run_url, failed_jobs, pull)
    issue_payload = _api(
        gh,
        f"repos/{repo}/issues",
        method="POST",
        fields={
            "title": title,
            "body": body,
            "labels": ["bug", "priority/p0", "area/ci"],
        },
    )
    issue = issue_payload if isinstance(issue_payload, dict) else {}
    # A run that overlapped this one may have created an issue between the lookup and the
    # create. The lowest-numbered issue for the commit stays open; later ones close themselves.
    after = _api(gh, f"repos/{repo}/issues?state=all&per_page=100", paginate=True)
    earlier = [
        row
        for row in _rows(after, "issues")
        if "pull_request" not in row
        and marker in str(row.get("body", ""))
        and isinstance(row.get("number"), int)
        and isinstance(issue.get("number"), int)
        and row["number"] < issue["number"]
    ]
    if earlier:
        keep = min(earlier, key=lambda row: row["number"])
        _api(gh, f"repos/{repo}/issues/{issue['number']}", method="PATCH", fields={"state": "closed"})
        _ensure_pr_comment(gh, repo, pull, keep)
        print(f"closed duplicate main red issue: {keep.get('html_url', keep.get('number'))}")
        return keep
    _ensure_pr_comment(gh, repo, pull, issue)
    print(f"created main red issue: {issue.get('html_url', issue.get('number'))}")
    return issue


def _ensure_pr_comment(gh: Gh, repo: str, pull: dict[str, Any] | None, issue: dict[str, Any]) -> None:
    """Post the PR comment that links the issue, unless it is already there."""
    url = issue.get("html_url")
    if not pull or not isinstance(url, str):
        return
    comments = _api(gh, f"repos/{repo}/issues/{pull['number']}/comments?per_page=100", paginate=True)
    if any(url in str(row.get("body", "")) for row in _rows(comments)):
        return
    _api(
        gh,
        f"repos/{repo}/issues/{pull['number']}/comments",
        method="POST",
        fields={"body": f"Main CI failure tracked in {url}"},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    open_main_red_issue(args.repo, args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
