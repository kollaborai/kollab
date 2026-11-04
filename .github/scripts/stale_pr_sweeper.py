#!/usr/bin/env python3
"""Stale-PR sweeper: flag-only digest plus the `stale` label.

Implements kollaborai/kollab#718. Every open PR with no new commit AND no
review activity for 5+ days is listed in a single digest (drafts first) and
labeled `stale`. The sweeper only flags: it never closes, never comments per
PR, never removes labels, and never touches draft state.

Activity means commits and reviews only. Label edits, comments, and metadata
churn do not reset the inactivity clock, so a PR cannot hide from the sweeper
by being re-labeled.

CI green on a stale head proves nothing once main moved: the digest always
pairs the head's recorded CI verdict with the head-versus-main state and
qualifies it as unproven when main is ahead.

Suggested actions (rebase/update, finish, consolidate, close) are heuristics
for humans and koordinator; nothing here acts on them.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STALE_LABEL = "stale"
DEFAULT_DAYS = 5
DRAFT_CLOSE_DAYS = 21
ANY_CLOSE_DAYS = 45
# Sort key: drafts first, then most-inactive first.
_DRAFT_RANK = {True: 0, False: 1}

IssueRef = int


def parse_linked_issues(body: str | None) -> list[IssueRef]:
    """Issue numbers referenced with a linking keyword in the PR body."""
    import re

    if not body:
        return []
    pattern = re.compile(
        r"\b(?:fix|fixes|fixed|close|closes|closed|resolve|resolves|"
        r"resolved|ref|refs|reference|references|part\s+of)\s+#(\d+)",
        re.IGNORECASE,
    )
    return sorted({int(m) for m in pattern.findall(body)})


def _parse_gh_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def last_activity(pr: dict) -> datetime | None:
    """Latest commit or review time; None when the PR has neither."""
    times: list[datetime] = []
    for commit in pr.get("commits") or []:
        parsed = _parse_gh_time(
            commit.get("committedDate") or commit.get("authoredDate")
        )
        if parsed:
            times.append(parsed)
    for review in pr.get("reviews") or []:
        parsed = _parse_gh_time(review.get("submittedAt"))
        if parsed:
            times.append(parsed)
    return max(times) if times else None


def inactivity_days(pr: dict, now: datetime) -> float:
    """Days since the last commit/review; falls back to creation time."""
    reference = last_activity(pr) or _parse_gh_time(pr.get("createdAt"))
    if reference is None:
        return float("inf")
    return max(0.0, (now - reference).total_seconds() / 86400)


def age_days(pr: dict, now: datetime) -> float:
    created = _parse_gh_time(pr.get("createdAt"))
    if created is None:
        return float("inf")
    return max(0.0, (now - created).total_seconds() / 86400)


def is_stale(pr: dict, now: datetime, days: int = DEFAULT_DAYS) -> bool:
    return inactivity_days(pr, now) >= days


def head_vs_main(pr: dict, compare: dict | None) -> str:
    """Human head-versus-main state; compare supplies the behind count."""
    state = (pr.get("mergeStateStatus") or "UNKNOWN").upper()
    mergeable = (pr.get("mergeable") or "UNKNOWN").upper()
    behind = (compare or {}).get("behind_by", 0)
    if state in ("CONFLICTING", "DIRTY") or mergeable == "CONFLICTING":
        return "CONFLICTED with main"
    if behind > 0:
        return f"BEHIND main by {behind} commit(s)"
    if state == "BLOCKED":
        return "even with main (blocked by gates)"
    if state in ("CLEAN", "HAS_HOOKS") and behind == 0:
        return "even with main"
    return f"{state or 'UNKNOWN'} (behind main by {behind})"


def ci_verdict(pr: dict, vs_main: str) -> str:
    """Recorded CI verdict on the head, qualified when main has moved."""
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        raw = "no checks recorded"
    else:
        conclusions = [
            str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
            for c in rollup
        ]
        completed = [
            c
            for c in conclusions
            if c and c != "PENDING" and c != "IN_PROGRESS" and c != "QUEUED"
        ]
        if len(completed) < len(conclusions):
            raw = "running"
        elif any(
            c in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED")
            for c in completed
        ):
            raw = "red"
        elif all(c in ("SUCCESS", "NEUTRAL", "SKIPPED") for c in completed):
            raw = "green"
        else:
            raw = "mixed/unknown"

    moved = "CONFLICTED" in vs_main or "BEHIND" in vs_main
    if moved and raw.startswith("green"):
        return f"{raw} at head, but {vs_main.lower()}: unproven vs current main"
    if moved:
        return f"{raw}; head {vs_main.lower()}"
    return raw


def suggested_action(
    pr: dict,
    linked: list[IssueRef],
    vs_main: str,
    inactivity: float,
    shared_issues: dict[IssueRef, list[int]],
) -> str:
    """One of: rebase/update, finish, consolidate, close (plus modifiers)."""
    primary: str
    notes: list[str] = []

    if "CONFLICTED" in vs_main:
        primary = "rebase/update (conflict repair)"
    elif "BEHIND" in vs_main:
        primary = "rebase/update (update-branch from main)"
    elif inactivity >= ANY_CLOSE_DAYS:
        primary = f"close ({int(inactivity)}d inactive)"
        if pr.get("isDraft"):
            notes.append("draft")
    elif pr.get("isDraft") and inactivity >= DRAFT_CLOSE_DAYS:
        primary = f"close candidate (draft, {int(inactivity)}d inactive)"
    else:
        primary = "finish"

    siblings = sorted(
        {
            f"#{n}"
            for issue in linked
            for n in shared_issues.get(issue, [])
            if n != pr.get("number")
        }
    )
    if siblings:
        notes.append(f"consolidate with {'/'.join(siblings)} (shares issue)")
    if not linked:
        notes.append("link its issue")

    if notes:
        return f"{primary}; also {', '.join(notes)}"
    return primary


def _fmt_days(value: float) -> str:
    if value == float("inf"):
        return "?"
    return f"{int(value)}d"


def build_digest(
    rows: list[dict],
    now: datetime,
    days: int,
    dry_run: bool,
) -> str:
    """Single markdown digest; drafts first, then most-inactive first."""
    ordered = sorted(
        rows,
        key=lambda r: (_DRAFT_RANK[bool(r["draft"])], -r["inactivity_days"]),
    )
    lines = [
        "# Stale PR sweep digest",
        "",
        f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')} - threshold "
        f"{days}d with no commit and no review activity - flag-only, "
        f"{'DRY RUN (no labels applied)' if dry_run else 'labels applied'}.",
        "",
        f"**{len(ordered)} stale PR(s)**. Drafts listed first. Old green CI "
        "on a moved head is not proof of correctness; see the head-vs-main "
        "column.",
        "",
        "| PR | Draft | Title | Linked | Age | Last activity | Head vs main | CI on head | Suggested |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in ordered:
        linked = ", ".join(f"#{n}" for n in r["linked_issues"]) or "none"
        draft = "yes" if r["draft"] else "no"
        title = str(r["title"]).replace("|", "\\|")
        activity = r["last_activity"] or "never (no commits/reviews)"
        lines.append(
            f"| #{r['number']} | {draft} | {title} | {linked} "
            f"| {_fmt_days(r['age_days'])} | {activity} "
            f"| {r['head_vs_main']} | {r['ci_verdict']} "
            f"| {r['suggested_action']} |"
        )

    conflicted = [r for r in ordered if "CONFLICTED" in r["head_vs_main"]]
    if conflicted:
        lines += ["", "## Conflicted backlog disposition", ""]
        for r in conflicted:
            lines.append(
                f"- #{r['number']}: disposition {r['suggested_action']} "
                f"(inactive {_fmt_days(r['inactivity_days'])}, "
                f"{'draft' if r['draft'] else 'ready'}"
                f"{', linked #' + str(r['linked_issues'][0]) if r['linked_issues'] else ', no linked issue'})."
            )
    lines += [
        "",
        "Method: inactivity = days since the latest commit or review "
        "(labels/comments/metadata do not count). The sweeper never closes, "
        "comments per PR, or removes labels; humans and koordinator decide.",
        "",
    ]
    return "\n".join(lines)


def collect_rows(
    prs: list[dict], compares: dict[int, dict], now: datetime, days: int
) -> list[dict]:
    stale = [p for p in prs if is_stale(p, now, days)]
    linked_by_number: dict[int, list[IssueRef]] = {}
    issue_groups: dict[IssueRef, list[int]] = {}
    for pr in stale:
        linked = parse_linked_issues(pr.get("body"))
        linked_by_number[pr["number"]] = linked
        for issue in linked:
            issue_groups.setdefault(issue, []).append(pr["number"])
    shared_issues = {k: v for k, v in issue_groups.items() if len(v) > 1}

    rows: list[dict] = []
    for pr in stale:
        number = pr["number"]
        vs_main = head_vs_main(pr, compares.get(number))
        inactivity = inactivity_days(pr, now)
        last = last_activity(pr)
        rows.append(
            {
                "number": number,
                "title": pr.get("title") or "",
                "draft": bool(pr.get("isDraft")),
                "linked_issues": linked_by_number[number],
                "age_days": age_days(pr, now),
                "inactivity_days": inactivity,
                "last_activity": last.strftime("%Y-%m-%d") if last else None,
                "head_vs_main": vs_main,
                "ci_verdict": ci_verdict(pr, vs_main),
                "suggested_action": suggested_action(
                    pr, linked_by_number[number], vs_main, inactivity, shared_issues
                ),
            }
        )
    return rows


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args[:3])} failed: {result.stderr.strip()[:300]}"
        )
    return result.stdout


def fetch_open_prs(repo: str) -> list[dict]:
    """One shallow list call, then per-PR view for nested fields.

    gh pr list cannot carry commits/reviews/rollup at repo scale: the
    GraphQL traversal exceeds its node limit. Per-PR view calls keep each
    query small; the open-PR count is bounded (200), so this stays well
    inside the workflow timeout.
    """
    payload = gh(
        "pr",
        "list",
        "-R",
        repo,
        "--state",
        "open",
        "--limit",
        "200",
        "--json",
        "number",
    )
    numbers = [item["number"] for item in json.loads(payload)]
    fields = (
        "number,title,isDraft,createdAt,body,labels,headRefOid,"
        "mergeable,mergeStateStatus,statusCheckRollup,commits,reviews"
    )
    return [
        json.loads(gh("pr", "view", str(n), "-R", repo, "--json", fields))
        for n in numbers
    ]


def fetch_compare(repo: str, head_sha: str) -> dict:
    payload = gh(
        "api",
        f"repos/{repo}/compare/main...{head_sha}",
        "--jq",
        "{behind_by: .behind_by, ahead_by: .ahead_by}",
    )
    return json.loads(payload)


def add_stale_label(pr_number: int, repo: str, dry_run: bool) -> str:
    """Add-only. There is deliberately no remove/close/comment code path."""
    if dry_run:
        return "DRYRUN"
    result = subprocess.run(
        ["gh", "pr", "edit", str(pr_number), "-R", repo, "--add-label", STALE_LABEL],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        return "LABELED"
    message = (result.stderr or "").lower()
    if "already" in message:
        return "ALREADY-LABELED"
    return f"FAILED: {(result.stderr or '').strip()[:160]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"inactivity threshold (default {DEFAULT_DAYS})",
    )
    parser.add_argument("--repo", default=None, help="OWNER/REPO (default $GH_REPO)")
    parser.add_argument(
        "--summary",
        default=None,
        help="also write the digest to this file (e.g. $GITHUB_STEP_SUMMARY)",
    )
    parser.add_argument(
        "--digest", default=None, help="write the digest to this file (artifact)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report only; never apply the stale label",
    )
    args = parser.parse_args(argv)

    repo = args.repo or os.environ.get("GH_REPO", "")
    if not repo:
        print("error: --repo or $GH_REPO is required", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    prs = fetch_open_prs(repo)
    stale_candidates = [p for p in prs if is_stale(p, now, args.days)]
    compares: dict[int, dict] = {}
    for pr in stale_candidates:
        head = pr.get("headRefOid")
        if head:
            try:
                compares[pr["number"]] = fetch_compare(repo, head)
            except RuntimeError as error:
                print(f"warn: compare for #{pr['number']}: {error}", file=sys.stderr)

    rows = collect_rows(prs, compares, now, args.days)
    digest = build_digest(rows, now, args.days, args.dry_run)
    print(digest)

    if args.digest:
        Path(args.digest).write_text(digest, encoding="utf-8")
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8") as handle:
            handle.write("\n" + digest)

    if args.dry_run:
        results = {r["number"]: "DRYRUN" for r in rows}
    else:
        results = {}
        for r in rows:
            results[r["number"]] = add_stale_label(r["number"], repo, args.dry_run)
    labeled = sum(
        1 for v in results.values() if v in ("LABELED", "ALREADY-LABELED", "DRYRUN")
    )
    print(
        f"\nflag summary: {len(rows)} stale, {labeled} labeled-ok, "
        f"{sum(1 for v in results.values() if str(v).startswith('FAILED'))} failed"
    )
    return 0 if not any(str(v).startswith("FAILED") for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
