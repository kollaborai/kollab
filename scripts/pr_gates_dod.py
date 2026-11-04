#!/usr/bin/env python3
"""pr-gates: definition-of-done check for pull requests (#724).

A PR entering review must have its basics done so reviewers spend rounds on
correctness, not hygiene. This tool evaluates a configurable set of
definition-of-done checks and reports every failed item in exactly one
GitHub check run (configurable name, default ``dod/ready-for-review``).
It never posts comments, so repeated pushes never spam a thread.

Two modes:

- Local (agents run it pre-push / pre-review)::

      python scripts/pr_gates_dod.py --repo OWNER/REPO --pr 123

  Collects PR data with the ``gh`` CLI, evaluates, prints the report, and
  exits 1 when an enabled check fails.

- CI (``--post``)::

      python scripts/pr_gates_dod.py --repo ... --pr ... --head-sha ... --post

  Additionally posts one check run whose conclusion carries the verdict.
  The workflow job itself exits 0 after a successful post so exactly one
  check (the posted one) reports the outcome.

Configuration lives in ``.github/pr-gates.toml`` and is repo-agnostic:

.. code-block:: toml

   [pr_gates.dod]
   enabled = false                                  # default: off
   check_name = "dod/ready-for-review"              # posted check name
   checks = ["signed_commits", "docs_paths", "threads_resolved",
             "base_current", "conventional_title", "linked_issue",
             "regression_test"]                     # each toggleable

   [pr_gates.dod.docs_paths]
   "^packages/" = ["CHANGELOG.md"]                  # regex -> required docs

   [pr_gates.dod.regression_profile]
   finding_id_pattern = "\\bF\\d+\\b"               # generic finding ids
   test_paths = ["tests/"]                          # where tests live

Turning ``enabled`` off fully disables the feature: nothing is posted and
every exit code is 0. DoD is a hygiene gate, not a correctness verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- constants ---------------------------------------------------------------

ALL_CHECKS: tuple[str, ...] = (
    "signed_commits",
    "docs_paths",
    "threads_resolved",
    "base_current",
    "conventional_title",
    "linked_issue",
    "regression_test",
)

DEFAULT_CHECK_NAME = "dod/ready-for-review"
# Generic finding-id shape (F14, F2, ...) -- not tied to any one review
# convention. Repos configure their own via regression_profile.
DEFAULT_FINDING_PATTERN = r"\bF\d+\b"
DEFAULT_TEST_PATHS: list[str] = ["tests/"]

CONVENTIONAL_TITLE_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([^)]+\))?!?:\s+.+"
)
LINKED_ISSUE_RE = re.compile(
    r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?|references?|refs?)\s+#\d+",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"(ghp_|github_pat_)[A-Za-z0-9_]+")


class ConfigError(Exception):
    """Raised when pr-gates.toml contains values this tool cannot use."""


# --- configuration ------------------------------------------------------------


@dataclass(frozen=True)
class RegressionProfile:
    """Generic finding-id -> test mapping profile (repo-agnostic, #721)."""

    finding_id_pattern: str = DEFAULT_FINDING_PATTERN
    test_paths: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_PATHS))


@dataclass(frozen=True)
class DodConfig:
    enabled: bool = False
    checks: list[str] = field(default_factory=lambda: list(ALL_CHECKS))
    docs_paths: dict[str, list[str]] = field(default_factory=dict)
    check_name: str = DEFAULT_CHECK_NAME
    regression: RegressionProfile = field(default_factory=RegressionProfile)


def _as_str_tuple(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{what} must be a list of strings, got: {value!r}")
    return tuple(value)


def load_config(path: str | os.PathLike[str]) -> DodConfig:
    """Load pr-gates config; a missing file means fully-default (off)."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return DodConfig()
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    section = data.get("pr_gates", {}).get("dod", {})
    if not isinstance(section, dict):
        raise ConfigError("pr_gates.dod must be a table")

    enabled = bool(section.get("enabled", False))
    check_name = str(section.get("check_name", DEFAULT_CHECK_NAME))

    checks = _as_str_tuple(
        section.get("checks", list(ALL_CHECKS)), "pr_gates.dod.checks"
    )
    unknown = [c for c in checks if c not in ALL_CHECKS]
    if unknown:
        raise ConfigError(
            f"unknown checks in pr_gates.dod.checks: {', '.join(unknown)} "
            f"(known: {', '.join(ALL_CHECKS)})"
        )

    raw_docs = section.get("docs_paths", {})
    if not isinstance(raw_docs, dict):
        raise ConfigError("pr_gates.dod.docs_paths must be a table of regex -> [paths]")
    docs_paths: dict[str, list[str]] = {}
    for pattern, required in raw_docs.items():
        docs_paths[str(pattern)] = list(
            _as_str_tuple(required, f"pr_gates.dod.docs_paths[{pattern!r}]")
        )

    raw_profile = section.get("regression_profile", {})
    if not isinstance(raw_profile, dict):
        raise ConfigError("pr_gates.dod.regression_profile must be a table")
    pattern = str(raw_profile.get("finding_id_pattern", DEFAULT_FINDING_PATTERN))
    test_paths = list(
        _as_str_tuple(
            raw_profile.get("test_paths", list(DEFAULT_TEST_PATHS)),
            "pr_gates.dod.regression_profile.test_paths",
        )
    )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConfigError(
            f"pr_gates.dod.regression_profile.finding_id_pattern is not a "
            f"valid regex ({pattern!r}): {exc}"
        ) from exc
    regression = RegressionProfile(finding_id_pattern=pattern, test_paths=test_paths)

    return DodConfig(
        enabled=enabled,
        checks=list(checks),
        docs_paths=docs_paths,
        check_name=check_name,
        regression=regression,
    )


# --- PR data -------------------------------------------------------------------


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str
    verified: bool


@dataclass(frozen=True)
class PRData:
    """Everything the checks need; collectors fill it, tests build it."""

    title: str
    body: str
    commits: tuple[Commit, ...]
    changed_files: tuple[str, ...]
    unresolved_threads: int
    review_comment_bodies: tuple[str, ...]
    default_branch: str
    default_branch_head: str
    merge_base_sha: str
    commits_behind_base: int
    changed_file_contents: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DoDReport:
    ran: bool
    evaluated: list[str]
    failed: list[tuple[str, str]]  # (check name, detail)

    @property
    def outcome(self) -> str:
        return "failure" if self.failed else "success"

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def failed_names(self) -> list[str]:
        return [name for name, _ in self.failed]

    def detail_for(self, name: str) -> str:
        for check, detail in self.failed:
            if check == name:
                return detail
        return ""

    def summary(self) -> str:
        if not self.ran:
            return "pr-gates DoD: disabled; no checks ran."
        if not self.failed:
            names = ", ".join(self.evaluated) or "none"
            return f"pr-gates DoD: all enabled checks passed ({names})."
        lines = [f"pr-gates DoD: {len(self.failed)} failed item(s):"]
        for name, detail in self.failed:
            lines.append(f"- {name}: {detail}")
        return "\n".join(lines)


# --- individual checks ------------------------------------------------------------


def _check_signed_commits(data: PRData) -> str:
    unsigned = [c.sha[:10] for c in data.commits if not c.verified]
    if unsigned:
        return f"unsigned commits: {', '.join(unsigned)}"
    return ""


def _check_docs_paths(cfg: DodConfig, data: PRData) -> str:
    changed = set(data.changed_files)
    missing: list[str] = []
    for pattern, required in cfg.docs_paths.items():
        regex = re.compile(pattern)
        if not any(regex.search(path) for path in changed):
            continue
        absent = [doc for doc in required if doc not in changed]
        if absent:
            missing.extend(absent)
    if missing:
        uniq = sorted(set(missing))
        return f"docs required by changed paths not updated: {', '.join(uniq)}"
    return ""


def _check_threads_resolved(data: PRData) -> str:
    if data.unresolved_threads:
        return f"{data.unresolved_threads} unresolved review thread(s)"
    return ""


def _check_base_current(data: PRData) -> str:
    if data.merge_base_sha != data.default_branch_head:
        return (
            f"PR head is {data.commits_behind_base} commit(s) behind "
            f"{data.default_branch} head {data.default_branch_head[:10]} "
            f"(merge-base {data.merge_base_sha[:10]}); update the branch from main"
        )
    return ""


def _check_conventional_title(data: PRData) -> str:
    if not CONVENTIONAL_TITLE_RE.match(data.title.strip()):
        return f"title is not a Conventional Commit: {data.title!r}"
    return ""


def _check_linked_issue(data: PRData) -> str:
    if not LINKED_ISSUE_RE.search(data.body or ""):
        return "no 'Fixes #N' / 'Closes #N' / 'Refs #N' link in the body"
    return ""


def _mentions_finding(content: str, finding: str) -> bool:
    # Underscore separates, so test_f14_bypass names F14; conf1g and f12 do not
    # name F1.
    pattern = rf"(?<![a-z0-9]){re.escape(finding)}(?![a-z0-9])"
    return re.search(pattern, content, re.IGNORECASE) is not None


def _check_regression_test(cfg: DodConfig, data: PRData) -> str:
    pattern = re.compile(cfg.regression.finding_id_pattern)
    findings: set[str] = set()
    for body in data.review_comment_bodies or ():
        findings.update(pattern.findall(body or ""))
    if not findings:
        return ""
    test_files = [
        (path, content)
        for path, content in (data.changed_file_contents or {}).items()
        if any(path.startswith(prefix) for prefix in cfg.regression.test_paths)
    ]
    uncovered: list[str] = []
    for finding in sorted(findings):
        covered = any(_mentions_finding(content, finding) for _path, content in test_files)
        if not covered:
            uncovered.append(finding)
    if uncovered:
        return (
            "no regression test covers finding id(s): "
            f"{', '.join(uncovered)} under {', '.join(cfg.regression.test_paths)}"
        )
    return ""


# --- evaluation -----------------------------------------------------------------


def evaluate(cfg: DodConfig, data: PRData) -> DoDReport:
    """Run the enabled checks; disabled config runs nothing and passes."""
    if not cfg.enabled:
        return DoDReport(ran=False, evaluated=[], failed=[])
    failed: list[tuple[str, str]] = []
    for name in cfg.checks:
        if name == "signed_commits":
            detail = _check_signed_commits(data)
        elif name == "docs_paths":
            detail = _check_docs_paths(cfg, data)
        elif name == "threads_resolved":
            detail = _check_threads_resolved(data)
        elif name == "base_current":
            detail = _check_base_current(data)
        elif name == "conventional_title":
            detail = _check_conventional_title(data)
        elif name == "linked_issue":
            detail = _check_linked_issue(data)
        elif name == "regression_test":
            detail = _check_regression_test(cfg, data)
        else:  # load_config rejects unknowns; defensive here
            detail = f"unknown check {name!r}"
        if detail:
            failed.append((name, detail))
    return DoDReport(ran=True, evaluated=list(cfg.checks), failed=failed)


# --- audit -----------------------------------------------------------------------


def _redact(value: str) -> str:
    return TOKEN_RE.sub(r"\1<redacted>", value)


def audit(action: str, **fields: Any) -> None:
    """Record one structured audit line; tokens never appear in output."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = " ".join(f"{key}={_redact(str(value))}" for key, value in fields.items())
    line = f"{stamp} pr-gates action={action} {parts}".rstrip()
    print(line)
    target = os.environ.get("PR_GATES_DOD_AUDIT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# --- github api -------------------------------------------------------------------


def github_api(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST one JSON payload to {GITHUB_API_URL}{endpoint} with GITHUB_TOKEN."""
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    token = os.environ.get("GITHUB_TOKEN", "")
    request = urllib.request.Request(
        f"{base.rstrip('/')}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def post_check_run(
    cfg: DodConfig, report: DoDReport, *, repo: str, head_sha: str
) -> dict[str, Any]:
    """Post exactly ONE check run; never a comment (no comment spam)."""
    if not cfg.enabled or not report.ran:
        audit("post_check_run", repo=repo, skipped="disabled")
        return {}
    payload = {
        "name": cfg.check_name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "success" if not report.failed else "failure",
        "output": {
            "title": (
                "Definition of done: passed"
                if not report.failed
                else f"Definition of done: {len(report.failed)} item(s) missing"
            ),
            "summary": report.summary(),
        },
    }
    result = github_api(f"/repos/{repo}/check-runs", payload)
    audit("post_check_run", repo=repo, name=cfg.check_name, outcome=report.outcome)
    return result


def main_post_if_enabled(cfg: DodConfig, report: DoDReport, **post_kwargs: Any) -> None:
    """Gate posting on the toggle; disabled means nothing is posted."""
    if cfg.enabled and report.ran:
        post_check_run(cfg, report, **post_kwargs)


# --- collectors (thin gh wrappers; the pure logic above is unit tested) -----------


def _gh(*args: str) -> Any:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed ({proc.returncode}): "
            f"{_redact(proc.stderr.strip())}"
        )
    return json.loads(proc.stdout) if proc.stdout.strip() else None


PAGE_SIZE = 100
# Liveness bound: 50 full pages of 100 items (5000) is far above any real
# PR's commits, files, or comments. A gate script must hang never and
# silently truncate never: hitting the cap is a loud fail-closed error,
# never a silent stop and never an unbounded loop.
PAGINATION_MAX_PAGES = 50


def _gh_paginate(endpoint: str) -> list[dict[str, Any]]:
    """Collect every page of a REST list endpoint; fail closed, hang never.

    Normal termination is a short page. Two guards make the loop
    liveness-safe: identical consecutive pages (a server ignoring the page
    parameter) abort immediately, and the PAGINATION_MAX_PAGES bound stops
    any other runaway — both raise instead of looping forever.
    """
    items: list[dict[str, Any]] = []
    page = 1
    sep = "&" if "?" in endpoint else "?"
    previous_fingerprint: tuple[Any, ...] | None = None
    while True:
        if page > PAGINATION_MAX_PAGES:
            raise RuntimeError(
                f"{endpoint} exceeded {PAGINATION_MAX_PAGES} pages of "
                f"{PAGE_SIZE} items; failing closed instead of paginating forever"
            )
        batch = _gh("api", f"{endpoint}{sep}per_page={PAGE_SIZE}&page={page}")
        if not isinstance(batch, list):
            raise RuntimeError(
                f"expected a JSON list from {endpoint} page {page}; failing closed"
            )
        fingerprint = (
            len(batch),
            repr(batch[0]) if batch else None,
            repr(batch[-1]) if batch else None,
        )
        if fingerprint == previous_fingerprint:
            raise RuntimeError(
                f"{endpoint} returned an identical page at page {page}; "
                "the API is ignoring pagination — failing closed"
            )
        previous_fingerprint = fingerprint
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
        page += 1


def collect_pr_data(repo: str, pr: int, *, root: str = ".") -> PRData:
    """Collect PR facts with the gh CLI against the checked-out PR head."""
    pull = _gh("api", f"repos/{repo}/pulls/{pr}")
    commits_raw = _gh_paginate(f"repos/{repo}/pulls/{pr}/commits")
    files_raw = _gh_paginate(f"repos/{repo}/pulls/{pr}/files")
    review_comments = _gh_paginate(f"repos/{repo}/pulls/{pr}/comments")
    issue_comments = _gh_paginate(f"repos/{repo}/issues/{pr}/comments")

    default_branch = pull["base"]["ref"]
    branch_info = _gh("api", f"repos/{repo}/branches/{default_branch}")
    # base_current must judge the PR head, not two base-branch facts: a PR
    # cut from an old point passes or fails by its merge-base with the
    # target branch, so compare head against the branch tip.
    head_sha = pull["head"]["sha"]
    compare = _gh("api", f"repos/{repo}/compare/{default_branch}...{head_sha}")

    threads_query = (
        "query($owner: String!, $name: String!, $pr: Int!, $cursor: String) { "
        "repository(owner: $owner, name: $name) { "
        "pullRequest(number: $pr) { "
        "reviewThreads(first: 100, after: $cursor) { "
        "pageInfo { hasNextPage endCursor } nodes { isResolved } } } } }"
    )
    owner, name = repo.split("/", 1)
    thread_nodes: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    pages = 0
    while True:
        pages += 1
        if pages > PAGINATION_MAX_PAGES:
            raise RuntimeError(
                f"reviewThreads pagination exceeded {PAGINATION_MAX_PAGES} pages; "
                "failing closed instead of paginating forever"
            )
        graphql_args: list[str] = [
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            # -F (capital) coerces to a real Int; -f sends a string and the
            # query declares $pr as Int!.
            "-F",
            f"pr={pr}",
        ]
        if cursor:
            graphql_args += ["-f", f"cursor={cursor}"]
        graphql_args += ["-f", f"query={threads_query}"]
        graphql = _gh(*graphql_args)
        connection = graphql["data"]["repository"]["pullRequest"]["reviewThreads"]
        thread_nodes.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        next_cursor = connection["pageInfo"]["endCursor"]
        # Advancing requires a non-null, never-seen endCursor: a null or
        # repeated cursor with hasNextPage=true means the same page would
        # repeat forever — fail closed, never silently continue.
        if not next_cursor:
            raise RuntimeError(
                "reviewThreads pagination returned hasNextPage=true with a "
                "null endCursor; failing closed"
            )
        if next_cursor in seen_cursors:
            raise RuntimeError(
                "reviewThreads pagination returned a repeated endCursor "
                f"({next_cursor}); failing closed"
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    unresolved = sum(1 for node in thread_nodes if not node.get("isResolved"))

    commits = tuple(
        Commit(
            sha=entry["sha"],
            message=entry["commit"]["message"],
            verified=bool(
                (entry.get("commit", {}).get("verification", {}) or {}).get("verified")
            ),
        )
        for entry in commits_raw
    )
    changed_files = tuple(entry["filename"] for entry in files_raw)
    comment_bodies = tuple(
        [entry["body"] for entry in review_comments]
        + [entry["body"] for entry in issue_comments]
    )

    # The action checks out the PR head, so test-file contents come from the
    # local tree; a local run from the PR branch sees the same files.
    contents: dict[str, str] = {}
    for path in changed_files:
        try:
            with open(os.path.join(root, path), encoding="utf-8") as fh:
                contents[path] = fh.read()
        except (OSError, UnicodeDecodeError):
            contents[path] = ""

    return PRData(
        title=pull.get("title") or "",
        body=pull.get("body") or "",
        commits=commits,
        changed_files=changed_files,
        unresolved_threads=unresolved,
        review_comment_bodies=comment_bodies,
        default_branch=default_branch,
        default_branch_head=branch_info["commit"]["sha"],
        merge_base_sha=compare["merge_base_commit"]["sha"],
        commits_behind_base=compare["behind_by"],
        changed_file_contents=contents,
    )


# --- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="pr-gates definition-of-done check (#724)"
    )
    parser.add_argument("--config", default=".github/pr-gates.toml")
    parser.add_argument("--repo", help="OWNER/REPO, e.g. kollaborai/kollab")
    parser.add_argument("--pr", type=int, help="pull request number")
    parser.add_argument("--head-sha", help="PR head SHA (required with --post)")
    parser.add_argument(
        "--post", action="store_true", help="post one check run with the verdict"
    )
    args = parser.parse_args(argv)

    if not args.repo or args.pr is None:
        parser.error("--repo and --pr are required")

    try:
        cfg = load_config(args.config)
        if not cfg.enabled:
            print("pr-gates DoD is disabled (pr_gates.dod.enabled = false); exit 0.")
            return 0
        data = collect_pr_data(args.repo, args.pr)
    except (ConfigError, RuntimeError, KeyError, ValueError) as exc:
        print(f"pr-gates DoD: error: {_redact(str(exc))}", file=sys.stderr)
        return 2

    report = evaluate(cfg, data)
    print(report.summary())

    if args.post:
        if not args.head_sha:
            parser.error("--head-sha is required with --post")
        # The posted check run carries the verdict; the job exits 0 so the
        # workflow's own check does not duplicate the outcome.
        main_post_if_enabled(cfg, report, repo=args.repo, head_sha=args.head_sha)
        return 0

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
