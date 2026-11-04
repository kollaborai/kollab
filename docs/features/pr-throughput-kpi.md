# PR throughput KPIs

Reports how many pull requests close per hour against a target of 3, on
rolling 1-hour, 8-hour and 24-hour windows. Implements #729.

The report is off by default. It runs only when `kpi.enabled` is `true`.
The script is repo-agnostic: the repo, target, windows and lane label
prefixes all come from config.

Script: `.github/scripts/pr_throughput_kpi.py` (stdlib only, reads GitHub
through `gh api` REST calls, no GraphQL).

## Config

A JSON file with a top-level `kpi` object:

```json
{
  "kpi": {
    "enabled": true,
    "repo": "owner/name",
    "target_per_hour": 3,
    "windows": [1, 8, 24],
    "stale_days": 5,
    "max_pages": 10,
    "lane_labels": ["lane/"]
  }
}
```

`enabled` defaults to `false`, `repo` is required when enabled, and the other
keys default as shown above.

## Run

```bash
python .github/scripts/pr_throughput_kpi.py --config kpi.json \
  --json-out out/kpi.json --markdown-out out/kpi.md
```

With `kpi.enabled` false the script exits 0 and makes no GitHub calls.
`GITHUB_STEP_SUMMARY` is appended to when set. `--now` pins the time, for
reproducible output. Exit 1 means the report is `unavailable`: the core PR
list could not be read.

## Metrics

- **Closure rate (primary).** Closed PRs (merged or closed unmerged) whose
  `closed_at` falls in `(now - hours, now]`, divided by `hours`. Status is
  `meets` at or above target, otherwise `below`.
- **Open PRs.** Count, oldest age, median age, and stale count. Stale means no
  update for `stale_days` or more, using `updated_at` as the REST proxy for
  inactivity.
- **Conflicted and BEHIND.** Open PRs with `mergeable_state` `dirty` or
  `behind`. An open PR whose state could not be fetched makes these counts
  missing.
- **Time to first review.** Earliest non-pending review minus PR creation, as
  a median over closed PRs. A review submitted before the PR existed is missing.
- **Review rounds per PR.** Mean count of `CHANGES_REQUESTED` reviews.
- **CI pass rate on first push.** Check runs on the PR's first commit: `pass`
  when all completed runs succeeded, `fail` when any failed, missing otherwise.
- **Throughput by author and lane.** Closed PRs in the longest window, grouped
  by PR author and by the first label matching a `lane_labels` prefix.

## Missing data

Missing is shown as "missing", never as zero. A metric is missing when its
input failed to fetch, or when a window starts before the oldest PR the fetch
covered (`max_pages` reached). A real zero, such as no change requests, is
still zero.

## Not in this report

- Cost per merged PR (#723): no cost source in the GitHub API.
- Hub data: not wired in.
- Scheduling: no workflow runs this on a timer. Scheduling and hub job
  registration need owner sign-off.
