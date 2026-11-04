#!/usr/bin/env python3
"""PR throughput KPIs: closed PRs per hour against a target, rolling windows.

Implements kollaborai/kollab#729. Off by default: nothing runs unless the
config file sets `kpi.enabled: true`. The script is repo-agnostic. The repo,
target, windows and lane labels all come from the `kpi` config section.

Primary metric: closed PRs (merged or closed unmerged) whose `closed_at` falls
in the trailing window (now - hours, now], divided by hours, compared with
`kpi.target_per_hour`. Windows come from `kpi.windows` (default 1, 8, 24).

Supporting metrics: merged vs closed-unmerged, open PR count and age, stale
open PRs, conflicted/BEHIND open PRs, time to first review, review rounds per
PR (CHANGES_REQUESTED reviews), CI pass rate on the first pushed commit, and
per-author and per-lane throughput.

Missing data is shown as missing, never as zero. A metric whose inputs failed
or whose window was not fully fetched is `None`, and the markdown renders it
as "missing". A real zero (for example, no change requests) is still zero.

Data comes only from the GitHub REST API through `gh api`. Cost per merged PR
(#723) needs a cost source outside GitHub and is not computed here. Hub data
is not wired in; the report says so.

Outputs: a JSON artifact (`--json-out`), a markdown dashboard
(`--markdown-out`), and an appended GitHub step summary when
`GITHUB_STEP_SUMMARY` is set. The script is a pure reader: it never labels,
comments on, closes, or merges anything.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_TARGET_PER_HOUR = 3.0
DEFAULT_WINDOWS = (1, 8, 24)
DEFAULT_STALE_DAYS = 5
DEFAULT_MAX_PAGES = 10
PER_PAGE = 100
UNASSIGNED = "unassigned"

Runner = Callable[[list[str]], str]


class ConfigError(ValueError):
    """The `kpi` config section is invalid."""


class GhError(RuntimeError):
    """A `gh api` call failed."""


@dataclass(frozen=True)
class KpiConfig:
    enabled: bool = False
    repo: str | None = None
    target_per_hour: float = DEFAULT_TARGET_PER_HOUR
    windows: tuple[int, ...] = DEFAULT_WINDOWS
    stale_days: int = DEFAULT_STALE_DAYS
    max_pages: int = DEFAULT_MAX_PAGES
    lane_labels: tuple[str, ...] = ()


def parse_config(data: dict[str, Any]) -> KpiConfig:
    """Build KpiConfig from a parsed config document (reads its `kpi` key)."""
    section = data.get("kpi") or {}
    if not isinstance(section, dict):
        raise ConfigError("kpi must be an object")
    enabled = bool(section.get("enabled", False))
    repo = section.get("repo")
    if enabled and (not isinstance(repo, str) or repo.count("/") != 1):
        raise ConfigError("kpi.repo must be 'owner/name' when kpi.enabled is true")
    target = section.get("target_per_hour", DEFAULT_TARGET_PER_HOUR)
    if not isinstance(target, (int, float)) or isinstance(target, bool) or target <= 0:
        raise ConfigError("kpi.target_per_hour must be a positive number")
    windows = section.get("windows", list(DEFAULT_WINDOWS))
    if (
        not isinstance(windows, list)
        or not windows
        or any(not isinstance(h, int) or isinstance(h, bool) or h <= 0 for h in windows)
    ):
        raise ConfigError("kpi.windows must be a non-empty list of positive hours")
    stale_days = section.get("stale_days", DEFAULT_STALE_DAYS)
    if not isinstance(stale_days, int) or stale_days <= 0:
        raise ConfigError("kpi.stale_days must be a positive integer")
    max_pages = section.get("max_pages", DEFAULT_MAX_PAGES)
    if not isinstance(max_pages, int) or max_pages <= 0:
        raise ConfigError("kpi.max_pages must be a positive integer")
    lanes = section.get("lane_labels", [])
    if not isinstance(lanes, list) or any(not isinstance(x, str) for x in lanes):
        raise ConfigError("kpi.lane_labels must be a list of label prefixes")
    return KpiConfig(
        enabled=enabled,
        repo=repo,
        target_per_hour=float(target),
        windows=tuple(sorted(set(windows))),
        stale_days=stale_days,
        max_pages=max_pages,
        lane_labels=tuple(lanes),
    )


def load_config(path: Path) -> KpiConfig:
    return parse_config(json.loads(path.read_text(encoding="utf-8")))


# ----------------------------------------------------------- time helpers


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


# ------------------------------------------------------- primary metric


def window_stats(
    closed_prs: list[dict],
    now: datetime,
    hours: int,
    target: float,
    covered_since: datetime | None,
) -> dict[str, Any]:
    """Closure rate for the trailing window (now - hours, now].

    `covered_since` is the oldest timestamp the fetch guarantees to include.
    If the window starts before it, counts are missing (None), not zero.
    """
    start = now - timedelta(hours=hours)
    base: dict[str, Any] = {
        "window_hours": hours,
        "target_per_hour": target,
    }
    if covered_since is not None and start < covered_since:
        return {
            **base,
            "closed": None,
            "merged": None,
            "closed_unmerged": None,
            "rate_per_hour": None,
            "pct_of_target": None,
            "status": "missing",
        }
    in_window = [
        pr
        for pr in closed_prs
        if (closed := parse_time(pr.get("closed_at"))) is not None
        and start < closed <= now
    ]
    merged = sum(1 for pr in in_window if pr.get("merged_at"))
    closed_n = len(in_window)
    rate = closed_n / hours
    return {
        **base,
        "closed": closed_n,
        "merged": merged,
        "closed_unmerged": closed_n - merged,
        "rate_per_hour": rate,
        "pct_of_target": rate / target * 100.0,
        "status": "meets" if rate >= target else "below",
    }


# ---------------------------------------------------- supporting metrics


def open_stats(open_prs: list[dict], now: datetime, stale_days: int) -> dict[str, Any]:
    """Open count, age, stale count and merge-state counts.

    Stale means no update for `stale_days` or more. It uses `updated_at`, the
    REST proxy for inactivity. Conflicted/BEHIND counts are None unless every
    open PR had its merge state fetched; an unknown state is counted apart.
    """
    ages = [_hours_between(now, parse_time(p["created_at"])) / 24.0 for p in open_prs]
    stale = sum(
        1
        for p in open_prs
        if _hours_between(now, parse_time(p["updated_at"])) / 24.0 >= stale_days
    )
    states = [p.get("mergeable_state") for p in open_prs]
    have_state = all(s is not None for s in states)
    known = [s for s in states if s not in (None, "unknown")]
    return {
        "open": len(open_prs),
        "oldest_age_days": max(ages) if ages else 0.0,
        "median_age_days": statistics.median(ages) if ages else None,
        "stale_days_threshold": stale_days,
        "stale": stale,
        "conflicted": sum(1 for s in known if s == "dirty") if have_state else None,
        "behind": sum(1 for s in known if s == "behind") if have_state else None,
        "merge_state_unknown": sum(1 for s in states if s in (None, "unknown")),
    }


def time_to_first_review_hours(pr: dict, reviews: list[dict]) -> float | None:
    created = parse_time(pr.get("created_at"))
    times = [
        parse_time(r.get("submitted_at"))
        for r in reviews
        if r.get("state") != "PENDING" and r.get("submitted_at")
    ]
    times = [t for t in times if t is not None]
    if created is None or not times:
        return None
    hours = _hours_between(min(times), created)
    # A review before the PR existed is a data anomaly: missing, not zero.
    return hours if hours >= 0 else None


def review_rounds(reviews: list[dict]) -> int:
    return sum(1 for r in reviews if r.get("state") == "CHANGES_REQUESTED")


def first_push_ci(check_runs: list[dict]) -> str | None:
    """'pass', 'fail', or None (missing) for one commit's check runs."""
    if not check_runs:
        return None
    if any(r.get("status") != "completed" for r in check_runs):
        return None
    conclusions = {r.get("conclusion") for r in check_runs}
    if conclusions & {
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
        "startup_failure",
    }:
        return "fail"
    if conclusions <= {"success", "neutral", "skipped"}:
        return "pass"
    return None


def author_key(pr: dict) -> str:
    return (pr.get("user") or {}).get("login") or UNASSIGNED


def lane_key(prefixes: list[str] | tuple[str, ...]) -> Callable[[dict], str]:
    def key(pr: dict) -> str:
        for label in pr.get("labels") or []:
            name = label.get("name") or ""
            if any(name.startswith(p) for p in prefixes):
                return name
        return UNASSIGNED

    return key


def throughput_by(
    closed_prs: list[dict],
    now: datetime,
    hours: int,
    key: Callable[[dict], str],
) -> dict[str, int]:
    start = now - timedelta(hours=hours)
    counts: dict[str, int] = {}
    for pr in closed_prs:
        closed = parse_time(pr.get("closed_at"))
        if closed is None or not (start < closed <= now):
            continue
        name = key(pr)
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ------------------------------------------------------------ GitHub access


def gh_runner(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise GhError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout


class GhApi:
    """Thin REST reader. Every call is `gh api <path>`; no GraphQL."""

    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner or gh_runner

    def get(self, path: str) -> Any:
        return json.loads(self._runner(["api", path]) or "null")


def fetch_pulls(
    api: GhApi,
    repo: str,
    state: str,
    *,
    max_pages: int,
    stop_before: datetime | None,
) -> tuple[list[dict], datetime | None]:
    """Newest-updated-first PR list, paged until the cutoff or max_pages.

    Returns (prs, covered_since). covered_since is None when the cutoff was
    reached (or the list ended), so every PR updated after the cutoff was seen.
    Otherwise it is the oldest updated_at fetched, and windows that start
    before it cannot be counted.
    """
    prs: list[dict] = []
    for page in range(1, max_pages + 1):
        path = (
            f"repos/{repo}/pulls?state={state}&sort=updated&direction=desc"
            f"&per_page={PER_PAGE}&page={page}"
        )
        batch = api.get(path) or []
        if not batch:
            return prs, None
        prs.extend(batch)
        if stop_before is not None:
            oldest = parse_time(batch[-1].get("updated_at"))
            if oldest is not None and oldest < stop_before:
                return prs, None
        if len(batch) < PER_PAGE:
            return prs, None
    oldest = parse_time(prs[-1].get("updated_at")) if prs else None
    return prs, oldest


def _enrich_closed(
    api: GhApi, repo: str, prs: list[dict], errors: list[str]
) -> dict[str, Any]:
    """Review and first-push CI data for closed PRs. Failures become errors."""
    ttfr: list[float] = []
    rounds: list[int] = []
    ci: list[str] = []
    for pr in prs:
        number = pr["number"]
        try:
            reviews = (
                api.get(f"repos/{repo}/pulls/{number}/reviews?per_page={PER_PAGE}")
                or []
            )
            hours = time_to_first_review_hours(pr, reviews)
            if hours is not None:
                ttfr.append(hours)
            rounds.append(review_rounds(reviews))
        except GhError as exc:
            errors.append(f"PR #{number} reviews: {exc}")
        try:
            commits = (
                api.get(f"repos/{repo}/pulls/{number}/commits?per_page={PER_PAGE}")
                or []
            )
            if commits:
                sha = commits[0]["sha"]
                payload = (
                    api.get(
                        f"repos/{repo}/commits/{sha}/check-runs?per_page={PER_PAGE}"
                    )
                    or {}
                )
                verdict = first_push_ci(payload.get("check_runs") or [])
                if verdict is not None:
                    ci.append(verdict)
        except GhError as exc:
            errors.append(f"PR #{number} first-push CI: {exc}")
    passed = sum(1 for v in ci if v == "pass")
    return {
        "time_to_first_review_hours_median": statistics.median(ttfr) if ttfr else None,
        "review_rounds_per_pr": (sum(rounds) / len(rounds)) if rounds else None,
        "first_push_pass_rate": (passed / len(ci)) if ci else None,
        "first_push_checked": len(ci),
    }


def _enrich_open(
    api: GhApi, repo: str, prs: list[dict], errors: list[str]
) -> list[dict]:
    """Attach mergeable_state to each open PR. A failed lookup stays unknown."""
    detailed: list[dict] = []
    for pr in prs:
        number = pr["number"]
        try:
            detail = api.get(f"repos/{repo}/pulls/{number}") or {}
            detailed.append({**pr, "mergeable_state": detail.get("mergeable_state")})
        except GhError as exc:
            errors.append(f"PR #{number} detail: {exc}")
            detailed.append({**pr, "mergeable_state": None})
    return detailed


def collect(
    api: GhApi,
    config: KpiConfig,
    now: datetime,
) -> dict[str, Any]:
    """Fetch and compute the full report. Core list failures give 'unavailable'."""
    assert config.repo, "collect requires a repo"
    errors: list[str] = []
    longest = max(config.windows)
    stop_before = now - timedelta(hours=longest)
    report: dict[str, Any] = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": config.repo,
        "target_per_hour": config.target_per_hour,
        "windows_hours": list(config.windows),
        "status": "ok",
        "errors": errors,
        "scope_notes": [
            "Cost per merged PR (#723) is not computed: no cost source in the GitHub API.",
            "Hub data is not wired in; agent throughput is grouped by PR author.",
        ],
    }
    try:
        closed, covered = fetch_pulls(
            api,
            config.repo,
            "closed",
            max_pages=config.max_pages,
            stop_before=stop_before,
        )
        open_raw, _ = fetch_pulls(
            api, config.repo, "open", max_pages=config.max_pages, stop_before=None
        )
    except GhError as exc:
        errors.append(str(exc))
        report["status"] = "unavailable"
        report["windows"] = [
            window_stats([], now, h, config.target_per_hour, covered_since=now)
            for h in config.windows
        ]
        report["open"] = {
            "open": None,
            "oldest_age_days": None,
            "median_age_days": None,
            "stale_days_threshold": config.stale_days,
            "stale": None,
            "conflicted": None,
            "behind": None,
            "merge_state_unknown": None,
        }
        report["review"] = {}
        report["ci"] = {}
        report["throughput"] = {}
        return report

    closed_in_range = [
        pr
        for pr in closed
        if (c := parse_time(pr.get("closed_at"))) is not None and c > stop_before
    ]
    report["windows"] = [
        window_stats(closed, now, h, config.target_per_hour, covered_since=covered)
        for h in config.windows
    ]
    enriched = _enrich_closed(api, config.repo, closed_in_range, errors)
    open_detailed = _enrich_open(api, config.repo, open_raw, errors)
    open_block = open_stats(open_detailed, now, config.stale_days)
    report["open"] = open_block
    report["review"] = {
        "time_to_first_review_hours_median": enriched[
            "time_to_first_review_hours_median"
        ],
        "review_rounds_per_pr": enriched["review_rounds_per_pr"],
        "sample_closed_prs": len(closed_in_range),
    }
    report["ci"] = {
        "first_push_pass_rate": enriched["first_push_pass_rate"],
        "first_push_checked": enriched["first_push_checked"],
    }
    key_lane = lane_key(config.lane_labels) if config.lane_labels else None
    report["throughput"] = {
        "by_author": throughput_by(closed, now, longest, author_key)
    }
    if key_lane is not None:
        report["throughput"]["by_lane"] = throughput_by(closed, now, longest, key_lane)
    if errors:
        report["status"] = "partial"
    return report


# ------------------------------------------------------------- rendering


def _num(value: float | None, fmt: str) -> str:
    return "missing" if value is None else format(value, fmt)


def render_markdown(report: dict[str, Any]) -> str:
    target = report["target_per_hour"]
    lines = [
        f"## PR throughput: {report.get('repo', 'unknown repo')}",
        "",
        f"Generated {report.get('generated_at', 'unknown')} · status `{report.get('status')}` · "
        f"target {target:.1f}/h closed PRs",
        "",
        "### Rolling closure rate",
        "",
        "| Window | Closed | Merged | Closed unmerged | Rate/h | % of target | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for w in report.get("windows", []):
        lines.append(
            f"| {w['window_hours']}h | {_num(w['closed'], 'd')} | {_num(w['merged'], 'd')} "
            f"| {_num(w['closed_unmerged'], 'd')} | {_num(w['rate_per_hour'], '.2f')} "
            f"| {_num(w['pct_of_target'], '.0f')} | {w['status']} |"
        )
    open_block = report.get("open") or {}
    lines += [
        "",
        "### Open PRs",
        "",
        f"- Open: {_num(open_block.get('open'), 'd')}",
        f"- Oldest age: {_num_days(open_block.get('oldest_age_days'))}",
        f"- Stale (no update in {open_block.get('stale_days_threshold', DEFAULT_STALE_DAYS)}+ days): "
        f"{_num(open_block.get('stale'), 'd')}",
        f"- Conflicted (dirty): {_num(open_block.get('conflicted'), 'd')}",
        f"- BEHIND base: {_num(open_block.get('behind'), 'd')}",
    ]
    review = report.get("review") or {}
    ci = report.get("ci") or {}
    lines += [
        "",
        "### Supporting",
        "",
        f"- Time to first review (median, closed PRs): "
        f"{_num_hours(review.get('time_to_first_review_hours_median'))}",
        f"- Review rounds per PR: {_num(review.get('review_rounds_per_pr'), '.2f')}",
        f"- CI pass rate on first push: {_pct(ci.get('first_push_pass_rate'))}"
        f" ({_num(ci.get('first_push_checked'), 'd')} PRs checked)",
    ]
    throughput = report.get("throughput") or {}
    for label, group in (("Author", "by_author"), ("Lane", "by_lane")):
        if group in throughput:
            rows = (
                ", ".join(f"{k}: {v}" for k, v in throughput[group].items()) or "none"
            )
            lines.append(f"- Closed by {label.lower()} (longest window): {rows}")
    if report.get("errors"):
        lines += ["", "### Errors", ""] + [f"- {e}" for e in report["errors"]]
    lines += ["", "### Scope", ""] + [f"- {n}" for n in report.get("scope_notes", [])]
    return "\n".join(lines) + "\n"


def _num_days(value: float | None) -> str:
    return "missing" if value is None else f"{value:.1f} days"


def _num_hours(value: float | None) -> str:
    if value is None:
        return "missing"
    return f"{value * 60:.0f} min" if value < 1 else f"{value:.1f} h"


def _pct(value: float | None) -> str:
    return "missing" if value is None else f"{value * 100:.0f}%"


# ------------------------------------------------------------------ CLI


def main(
    argv: list[str] | None = None,
    runner: Runner | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--config", required=True, type=Path, help="JSON file with a kpi section"
    )
    parser.add_argument("--repo", help="override kpi.repo")
    parser.add_argument("--json-out", type=Path, help="write the JSON report here")
    parser.add_argument(
        "--markdown-out", type=Path, help="write the markdown dashboard here"
    )
    parser.add_argument("--now", help="ISO time to compute at (default: current UTC)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, json.JSONDecodeError, ConfigError) as exc:
        print(f"pr_throughput_kpi: config error: {exc}", file=sys.stderr)
        return 2
    if not config.enabled:
        print("pr_throughput_kpi: disabled (kpi.enabled is false); nothing to do")
        return 0
    if args.repo:
        config = KpiConfig(**{**config.__dict__, "repo": args.repo})
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("pr_throughput_kpi: --now must be ISO 8601", file=sys.stderr)
        return 2

    report = collect(GhApi(runner), config, now)
    markdown = render_markdown(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(markdown)
    sys.stdout.write(markdown)
    return 1 if report["status"] == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
