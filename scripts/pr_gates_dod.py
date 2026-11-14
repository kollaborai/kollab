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
             "regression_test", "spec_or_fix_brief"] # each toggleable
   spec_or_fix_brief_mode = "warn"                    # warn or block

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
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import time
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
    "spec_or_fix_brief",
)

DEFAULT_CHECK_NAME = "dod/ready-for-review"
# Generic finding-id shape (F14, F2, ...) -- not tied to any one review
# convention. Repos configure their own via regression_profile.
DEFAULT_FINDING_PATTERN = r"\bF\d+\b"
DEFAULT_TEST_PATHS: list[str] = ["tests/"]
# Suppressions stay unavailable unless the protected base-branch config names
# trusted accounts explicitly.
DEFAULT_TRUSTED_REVIEWERS: tuple[str, ...] = ()
SUPPRESSION_MARKER_PREFIX = "<!-- pr-gates-ignore-reference:v1"
SUPPRESSION_MARKER_SUFFIX = "-->"
SUPPRESSION_FIELDS = {
    "source_kind",
    "source_id",
    "source_body_sha256",
    "finding_id",
    "occurrence",
    "reason",
}

CONVENTIONAL_TITLE_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([^)]+\))?!?:\s+.+"
)
LINKED_ISSUE_RE = re.compile(
    r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?|references?|refs?)\s+#(?P<number>\d+)",
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
    trusted_reviewers: list[str] = field(
        default_factory=lambda: list(DEFAULT_TRUSTED_REVIEWERS)
    )


@dataclass(frozen=True)
class DodConfig:
    enabled: bool = False
    checks: list[str] = field(default_factory=lambda: list(ALL_CHECKS))
    docs_paths: dict[str, list[str]] = field(default_factory=dict)
    check_name: str = DEFAULT_CHECK_NAME
    regression: RegressionProfile = field(default_factory=RegressionProfile)
    spec_or_fix_brief_mode: str = "block"


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

    spec_or_fix_brief_mode = section.get("spec_or_fix_brief_mode", "block")
    if not isinstance(spec_or_fix_brief_mode, str) or spec_or_fix_brief_mode not in {
        "warn",
        "block",
    }:
        raise ConfigError(
            "pr_gates.dod.spec_or_fix_brief_mode must be 'warn' or 'block'"
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
    trusted_reviewers = list(
        _as_str_tuple(
            raw_profile.get("trusted_reviewers", list(DEFAULT_TRUSTED_REVIEWERS)),
            "pr_gates.dod.regression_profile.trusted_reviewers",
        )
    )
    normalized_reviewers = [reviewer.casefold() for reviewer in trusted_reviewers]
    if any(not reviewer for reviewer in normalized_reviewers):
        raise ConfigError(
            "pr_gates.dod.regression_profile.trusted_reviewers cannot contain empty logins"
        )
    if len(set(normalized_reviewers)) != len(normalized_reviewers):
        raise ConfigError(
            "pr_gates.dod.regression_profile.trusted_reviewers cannot contain duplicates"
        )
    regression = RegressionProfile(
        finding_id_pattern=pattern,
        test_paths=test_paths,
        trusted_reviewers=trusted_reviewers,
    )

    return DodConfig(
        enabled=enabled,
        checks=list(checks),
        docs_paths=docs_paths,
        check_name=check_name,
        regression=regression,
        spec_or_fix_brief_mode=spec_or_fix_brief_mode,
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
    comments: tuple["PRComment", ...] = ()
    reviews: tuple["PRReview", ...] = ()
    pull_author_login: str = ""
    head_sha: str = ""
    linked_issue_bodies: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PRComment:
    kind: str  # "issue_comment" or "review_comment"
    comment_id: int
    body: str
    author_login: str


@dataclass(frozen=True)
class PRReview:
    review_id: int
    author_login: str
    state: str
    commit_id: str
    submitted_at: str


@dataclass(frozen=True)
class FindingSuppression:
    source_kind: str
    source_id: int
    source_body_sha256: str
    finding_id: str
    occurrence: int
    reason: str
    reviewer_login: str
    approval_review_id: int


@dataclass(frozen=True)
class DoDReport:
    ran: bool
    evaluated: list[str]
    failed: list[tuple[str, str]]  # (check name, detail)
    audit_notes: list[FindingSuppression] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)

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
            lines = [f"pr-gates DoD: all enabled checks passed ({names})."]
        else:
            lines = [f"pr-gates DoD: {len(self.failed)} failed item(s):"]
            for name, detail in self.failed:
                lines.append(f"- {name}: {detail}")
        if self.warnings:
            lines.append(f"Warnings: {len(self.warnings)} item(s) need attention:")
            lines.extend(f"- WARNING {name}: {detail}" for name, detail in self.warnings)
        if self.audit_notes:
            lines.append("Audited finding-reference suppressions:")
            lines.extend(
                f"- {note.finding_id} occurrence {note.occurrence} in "
                f"{note.source_kind} {note.source_id}; reviewer={note.reviewer_login} "
                f"review={note.approval_review_id}; reason={note.reason}"
                for note in self.audit_notes
            )
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


def _has_feature_spec(data: PRData) -> bool:
    if any(re.fullmatch(r"docs/specs/[^/]+\.md", path) for path in data.changed_files):
        return True
    return any(
        re.search(r"docs/specs/[^\s)>\]]+\.md", body, re.IGNORECASE)
        for body in data.linked_issue_bodies.values()
    )


def _has_fix_brief(text: str) -> bool:
    heading = re.search(r"(?im)^\s*(?P<level>#{1,6})\s+fix brief\s*:?\s*$", text)
    if not heading:
        return False
    level = len(heading.group("level"))
    next_section = re.search(rf"(?m)^\s*#{{1,{level}}}\s+\S.*$", text[heading.end() :])
    end = heading.end() + next_section.start() if next_section else len(text)
    section = text[heading.end() : end]
    return bool(
        re.search(r"\broot cause\b", section, re.IGNORECASE)
        and re.search(r"\bregression test\b", section, re.IGNORECASE)
    )


def _check_spec_or_fix_brief(data: PRData) -> str:
    title_type = re.match(r"^([a-z]+)(?:\([^)]*\))?!?:", data.title.strip())
    if not title_type:
        return ""
    if title_type.group(1) == "feat" and not _has_feature_spec(data):
        return "feature PR needs a changed docs/specs/*.md file or a linked issue that links one"
    if title_type.group(1) == "fix":
        sources = (data.body or "", *data.linked_issue_bodies.values())
        if not any(_has_fix_brief(source) for source in sources):
            return "fix PR needs a Fix brief section with Root cause and Regression test"
    return ""


def _mentions_finding(content: str, finding: str) -> bool:
    # Underscore separates, so test_f14_bypass names F14; conf1g and f12 do not
    # name F1.
    pattern = rf"(?<![a-z0-9]){re.escape(finding)}(?![a-z0-9])"
    return re.search(pattern, content, re.IGNORECASE) is not None


def _json_object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate annotation field {key!r}")
        result[key] = value
    return result


def _parse_suppression_marker(comment: PRComment) -> dict[str, Any] | None:
    """Parse a marker-only issue comment; malformed markers have no authority."""
    if comment.kind != "issue_comment":
        return None
    body = (comment.body or "").strip()
    if not body.startswith(SUPPRESSION_MARKER_PREFIX):
        return None
    if not body.endswith(SUPPRESSION_MARKER_SUFFIX):
        return None
    payload_text = body[len(SUPPRESSION_MARKER_PREFIX) : -len(SUPPRESSION_MARKER_SUFFIX)].strip()
    if "-->" in payload_text or "<!--" in payload_text:
        return None
    try:
        payload = json.loads(payload_text, object_pairs_hook=_json_object_without_duplicate_keys)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != SUPPRESSION_FIELDS:
        return None
    source_kind = payload.get("source_kind")
    if not isinstance(source_kind, str) or source_kind not in {
        "issue_comment",
        "review_comment",
    }:
        return None
    if type(payload.get("source_id")) is not int or payload["source_id"] <= 0:
        return None
    if type(payload.get("occurrence")) is not int or payload["occurrence"] <= 0:
        return None
    finding_id = payload.get("finding_id")
    if not isinstance(finding_id, str) or not finding_id:
        return None
    body_hash = payload.get("source_body_sha256")
    if not isinstance(body_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", body_hash):
        return None
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        return None
    if "\n" in reason or "\r" in reason:
        return None
    return payload


def _latest_current_head_review(
    data: PRData, reviewer_login: str
) -> PRReview | None:
    """Return only an unambiguous latest submitted review by this actor/head."""
    if not data.head_sha or not reviewer_login:
        return None
    actor = reviewer_login.casefold()
    relevant = [
        review
        for review in data.reviews
        if review.author_login.casefold() == actor and review.commit_id == data.head_sha
    ]
    if not relevant:
        return None
    dated: list[tuple[datetime, PRReview]] = []
    for review in relevant:
        # GitHub's PENDING reviews are drafts, not submitted review events, and
        # have no submitted_at. They do not supersede the latest submitted vote.
        if review.state.upper() == "PENDING":
            continue
        if not review.submitted_at:
            return None
        try:
            submitted = datetime.fromisoformat(review.submitted_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if submitted.tzinfo is None:
            return None
        dated.append((submitted, review))
    if not dated:
        return None
    latest_time = max(item[0] for item in dated)
    latest = [review for submitted, review in dated if submitted == latest_time]
    if len(latest) != 1 or latest[0].state.upper() != "APPROVED":
        return None
    if latest[0].review_id <= 0:
        return None
    return latest[0]


def _finding_suppressions(
    cfg: DodConfig, data: PRData, pattern: re.Pattern[str]
) -> tuple[set[tuple[str, int, str, int]], set[int], list[FindingSuppression]]:
    """Validate exact-occurrence annotations from trusted current-head reviewers."""
    comments_by_key: dict[tuple[str, int], list[PRComment]] = {}
    for comment in data.comments:
        comments_by_key.setdefault((comment.kind, comment.comment_id), []).append(comment)

    candidates: dict[tuple[str, int, str, int], list[tuple[PRComment, PRReview, str]]] = {}
    for marker_comment in data.comments:
        annotation = _parse_suppression_marker(marker_comment)
        if annotation is None:
            continue
        reviewer_login = marker_comment.author_login
        if not reviewer_login or marker_comment.comment_id <= 0 or not data.pull_author_login:
            continue
        normalized = reviewer_login.casefold()
        configured = {login.casefold() for login in cfg.regression.trusted_reviewers}
        if normalized not in configured or normalized == data.pull_author_login.casefold():
            continue
        approval = _latest_current_head_review(data, reviewer_login)
        if approval is None:
            continue
        source_kind = annotation["source_kind"]
        source_id = annotation["source_id"]
        if source_id == marker_comment.comment_id and source_kind == marker_comment.kind:
            continue
        sources = comments_by_key.get((source_kind, source_id), [])
        if len(sources) != 1:
            continue
        source = sources[0]
        expected_hash = hashlib.sha256(source.body.encode("utf-8")).hexdigest()
        if annotation["source_body_sha256"] != expected_hash:
            continue
        finding_id = annotation["finding_id"]
        if pattern.fullmatch(finding_id) is None:
            continue
        occurrence = annotation["occurrence"]
        matches = [
            match.group(0)
            for match in pattern.finditer(source.body)
            if match.group(0) == finding_id
        ]
        if occurrence > len(matches):
            continue
        key = (source_kind, source_id, finding_id, occurrence)
        reason = _redact(annotation["reason"].strip())
        candidates.setdefault(key, []).append((marker_comment, approval, reason))

    accepted: set[tuple[str, int, str, int]] = set()
    marker_comment_ids: set[int] = set()
    audit_notes: list[FindingSuppression] = []
    for key, entries in candidates.items():
        # Multiple annotations for one occurrence are ambiguous, so none wins.
        if len(entries) != 1:
            continue
        marker_comment, approval, reason = entries[0]
        accepted.add(key)
        marker_comment_ids.add(marker_comment.comment_id)
        source_kind, source_id, finding_id, occurrence = key
        source = comments_by_key[(source_kind, source_id)][0]
        audit_notes.append(
            FindingSuppression(
                source_kind=source_kind,
                source_id=source_id,
                source_body_sha256=hashlib.sha256(source.body.encode("utf-8")).hexdigest(),
                finding_id=finding_id,
                occurrence=occurrence,
                reason=reason,
                reviewer_login=marker_comment.author_login,
                approval_review_id=approval.review_id,
            )
        )
    return accepted, marker_comment_ids, audit_notes


def _check_regression_test(
    cfg: DodConfig, data: PRData
) -> tuple[str, list[FindingSuppression]]:
    pattern = re.compile(cfg.regression.finding_id_pattern)
    findings: set[str] = set()
    if data.comments:
        metadata_matches = tuple(comment.body for comment in data.comments) == tuple(
            data.review_comment_bodies
        )
        if metadata_matches:
            suppressions, marker_comment_ids, audit_notes = _finding_suppressions(
                cfg, data, pattern
            )
            for comment in data.comments:
                if comment.comment_id in marker_comment_ids and comment.kind == "issue_comment":
                    continue
                occurrence_counts: dict[str, int] = {}
                for match in pattern.finditer(comment.body or ""):
                    finding_id = match.group(0)
                    occurrence_counts[finding_id] = occurrence_counts.get(finding_id, 0) + 1
                    suppression_key = (
                        comment.kind,
                        comment.comment_id,
                        finding_id,
                        occurrence_counts[finding_id],
                    )
                    if suppression_key not in suppressions:
                        findings.add(finding_id)
        else:
            # Incomplete or mismatched metadata cannot authorize any exception.
            audit_notes = []
            for body in (*data.review_comment_bodies, *(c.body for c in data.comments)):
                findings.update(match.group(0) for match in pattern.finditer(body or ""))
    else:
        # Legacy/local test callers have no comment metadata, so no annotation
        # can be trusted and every ID remains gate-relevant.
        audit_notes = []
        for body in data.review_comment_bodies or ():
            findings.update(match.group(0) for match in pattern.finditer(body or ""))
    if not findings:
        return "", audit_notes
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
        ), audit_notes
    return "", audit_notes


# --- evaluation -----------------------------------------------------------------


def evaluate(cfg: DodConfig, data: PRData) -> DoDReport:
    """Run the enabled checks; disabled config runs nothing and passes."""
    if not cfg.enabled:
        return DoDReport(ran=False, evaluated=[], failed=[])
    failed: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    audit_notes: list[FindingSuppression] = []
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
            detail, suppression_notes = _check_regression_test(cfg, data)
            audit_notes.extend(suppression_notes)
        elif name == "spec_or_fix_brief":
            detail = _check_spec_or_fix_brief(data)
            if detail and cfg.spec_or_fix_brief_mode == "warn":
                warnings.append((name, detail))
                detail = ""
        else:  # load_config rejects unknowns; defensive here
            detail = f"unknown check {name!r}"
        if detail:
            failed.append((name, detail))
    return DoDReport(
        ran=True,
        evaluated=list(cfg.checks),
        failed=failed,
        audit_notes=audit_notes,
        warnings=warnings,
    )


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
                "Definition of done: passed with warnings"
                if report.warnings and not report.failed
                else "Definition of done: passed"
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


def _gh_blob(endpoint: str, max_output: int) -> Any:
    """Read bounded blob data with a deadline, without starting a worker."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        auth = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
        if auth.returncode != 0 or not auth.stdout.strip():
            raise RuntimeError("GitHub authentication unavailable; failing closed")
        token = auth.stdout.strip()
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = urllib.request.Request(f"{base}/{endpoint}", headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    deadline = time.monotonic() + 30
    output = bytearray()
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            while True:
                if time.monotonic() >= deadline:
                    raise RuntimeError("blob request timed out; failing closed")
                chunk = response.read1(min(65536, max_output + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > max_output:
                    raise RuntimeError("blob response exceeds limit; failing closed")
        return json.loads(output)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("blob request failed or timed out; failing closed") from exc


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


def collect_pr_data(
    repo: str,
    pr: int,
    *,
    root: str = ".",
    test_paths: tuple[str, ...] = ("tests/",),
    need_spec_or_fix_brief: bool = False,
) -> PRData:
    """Collect PR facts without checking out or executing PR-controlled code."""
    pull = _gh("api", f"repos/{repo}/pulls/{pr}")
    body = pull.get("body") or ""
    linked_issue_bodies: dict[int, str] = {}
    title = pull.get("title") or ""
    title_type = re.match(r"^([a-z]+)(?:\([^)]*\))?!?:", title.strip())
    if need_spec_or_fix_brief and title_type and title_type.group(1) in {"feat", "fix"}:
        issue_numbers = {
            int(match.group("number")) for match in LINKED_ISSUE_RE.finditer(body)
        }
        for issue_number in sorted(issue_numbers):
            issue = _gh("api", f"repos/{repo}/issues/{issue_number}")
            issue_body = issue.get("body") if isinstance(issue, dict) else None
            if isinstance(issue_body, str):
                linked_issue_bodies[issue_number] = issue_body
    commits_raw = _gh_paginate(f"repos/{repo}/pulls/{pr}/commits")
    files_raw = _gh_paginate(f"repos/{repo}/pulls/{pr}/files")
    review_comments = _gh_paginate(f"repos/{repo}/pulls/{pr}/comments")
    issue_comments = _gh_paginate(f"repos/{repo}/issues/{pr}/comments")
    reviews_raw = _gh_paginate(f"repos/{repo}/pulls/{pr}/reviews")

    def login(entry: dict[str, Any]) -> str:
        user = entry.get("user") or {}
        return user.get("login", "") if isinstance(user, dict) else ""

    def comment_record(entry: dict[str, Any], kind: str) -> PRComment:
        body = entry.get("body")
        if not isinstance(body, str):
            raise RuntimeError(
                f"{kind} {entry.get('id', 'unknown')} has no textual body; failing closed"
            )
        comment_id = entry.get("id")
        return PRComment(
            kind=kind,
            comment_id=comment_id if type(comment_id) is int and comment_id > 0 else 0,
            body=body,
            author_login=login(entry),
        )

    comment_records = tuple(
        [comment_record(entry, "review_comment") for entry in review_comments]
        + [comment_record(entry, "issue_comment") for entry in issue_comments]
    )
    reviews = tuple(
        PRReview(
            review_id=(entry.get("id") if type(entry.get("id")) is int else 0),
            author_login=login(entry),
            state=str(entry.get("state") or ""),
            commit_id=str(entry.get("commit_id") or ""),
            submitted_at=str(entry.get("submitted_at") or ""),
        )
        for entry in reviews_raw
    )

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

    # Use immutable trees to identify changed tests and their blob IDs. The
    # PR files endpoint is mutable during pagination and is not a snapshot.
    def test_tree(commit_sha: str) -> dict[str, dict[str, Any]]:
        tree = _gh("api", f"repos/{repo}/git/trees/{commit_sha}?recursive=1")
        if not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
            raise RuntimeError("incomplete commit tree; failing closed")
        result: dict[str, dict[str, Any]] = {}
        for item in tree["tree"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise RuntimeError("invalid commit tree entry; failing closed")
            path = item["path"]
            if item.get("type") == "tree" or not any(path.startswith(prefix) for prefix in test_paths):
                continue
            if path in result or item.get("type") != "blob" or item.get("mode") not in {"100644", "100755"}:
                raise RuntimeError(f"unsupported test tree entry {path}; failing closed")
            result[path] = item
        return result

    base_tree = test_tree(compare["merge_base_commit"]["sha"])
    head_tree = test_tree(head_sha)
    changed_tests = [(path, entry) for path, entry in head_tree.items()
                     if path not in base_tree or (entry.get("sha"), entry.get("mode")) !=
                     (base_tree[path].get("sha"), base_tree[path].get("mode"))]
    if len(changed_tests) > 256:
        raise RuntimeError("too many changed test files; failing closed")
    total_bytes = 0
    contents: dict[str, str] = {}
    for path, entry in changed_tests:
        sha = entry.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError(f"missing blob identity for {path}; failing closed")
        size = entry.get("size")
        if type(size) is not int or size < 0 or size > 1_000_000:
            raise RuntimeError(f"invalid test blob size for {path}; failing closed")
        total_bytes += size
        if total_bytes > 16_000_000:
            raise RuntimeError("changed test files exceed size limit; failing closed")
        blob = _gh_blob(f"repos/{repo}/git/blobs/{sha}", 2 * size + 8192)
        if (not isinstance(blob, dict) or blob.get("encoding") != "base64" or blob.get("sha") != sha
                or blob.get("size") != size or not isinstance(blob.get("content"), str)
                or len(blob["content"]) > 2 * size + 1024):
            raise RuntimeError(f"invalid blob response for {path}; failing closed")
        try:
            raw = base64.b64decode("".join(blob["content"].split()), validate=True)
            if len(raw) != size:
                raise ValueError("test blob size mismatch")
            contents[path] = raw.decode("utf-8")
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise RuntimeError(f"invalid test blob for {path}; failing closed") from exc

    # The files listing and the original head must describe the same PR state.
    current_pull = _gh("api", f"repos/{repo}/pulls/{pr}")
    if current_pull.get("head", {}).get("sha") != head_sha:
        raise RuntimeError("PR head changed while collecting facts; failing closed")

    return PRData(
        title=title,
        body=body,
        commits=commits,
        changed_files=changed_files,
        unresolved_threads=unresolved,
        review_comment_bodies=comment_bodies,
        default_branch=default_branch,
        default_branch_head=branch_info["commit"]["sha"],
        merge_base_sha=compare["merge_base_commit"]["sha"],
        commits_behind_base=compare["behind_by"],
        changed_file_contents=contents,
        comments=comment_records,
        reviews=reviews,
        pull_author_login=login(pull),
        head_sha=head_sha,
        linked_issue_bodies=linked_issue_bodies,
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
        data = collect_pr_data(
            args.repo,
            args.pr,
            test_paths=tuple(cfg.regression.test_paths),
            need_spec_or_fix_brief="spec_or_fix_brief" in cfg.checks,
        )
        if args.post and data.head_sha != args.head_sha:
            raise RuntimeError("event head differs from collected PR head; failing closed")
    except (ConfigError, RuntimeError, KeyError, ValueError) as exc:
        print(f"pr-gates DoD: error: {_redact(str(exc))}", file=sys.stderr)
        return 2

    report = evaluate(cfg, data)
    print(report.summary())
    for suppression in report.audit_notes:
        audit(
            "finding_reference_suppressed",
            finding_id=suppression.finding_id,
            occurrence=suppression.occurrence,
            source_kind=suppression.source_kind,
            source_id=suppression.source_id,
            source_body_sha256=suppression.source_body_sha256,
            reviewer=suppression.reviewer_login,
            approval_review_id=suppression.approval_review_id,
            reason=suppression.reason,
        )

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
