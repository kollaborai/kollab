# PR Gates: Definition of Done

A per-pull-request hygiene gate (#724). PRs reach review missing basics
(unsigned commits, no linked issue, a stale base), and reviewers burn
rounds on hygiene instead of correctness. The definition-of-done (DoD)
check evaluates a configurable checklist and reports every failed item
in exactly one GitHub check run. It never posts comments, so repeated
pushes never spam the thread.

DoD is hygiene, not a correctness verdict: a green DoD check says the
basics are present, not that the change is right. Independent review
still gates merge.

## What runs where

- CI: `.github/workflows/pr-gates-dod.yml` runs on every PR targeting
  `main`, `release/**`, or `integration/**`, executes
  `scripts/pr_gates_dod.py`, and posts one check run named by
  `pr_gates.dod.check_name` (default `dod/ready-for-review`). The posted
  check carries the verdict; the workflow job itself stays green so the
  outcome is reported in one place only.
- Local: agents run the same script before pushing or requesting review:

  ```bash
  python scripts/pr_gates_dod.py --repo kollaborai/kollab --pr 123
  ```

  It prints the report and exits 1 when an enabled check fails. Add
  `--head-sha <sha> --post` to also post the check run (requires
  `GITHUB_TOKEN`).

## Configuration

Everything lives in `.github/pr-gates.toml`. The feature is disabled by
default; `enabled = false` fully disables it (nothing posts, every exit
code is 0).

| Key | Default | Meaning |
| --- | --- | --- |
| `pr_gates.dod.enabled` | `false` | Master toggle. Off means no checks run and nothing is posted. |
| `pr_gates.dod.check_name` | `dod/ready-for-review` | Name of the posted check run. |
| `pr_gates.dod.checks` | all seven checks | Which checks run. Remove an entry to toggle that check off. |
| `pr_gates.dod.docs_paths` | `{}` | Maps a regex over changed paths to the doc files that must be updated in the same PR. |
| `pr_gates.dod.regression_profile.finding_id_pattern` | `F\d+` | Generic finding-id shape searched for in review comments. |
| `pr_gates.dod.regression_profile.test_paths` | `["tests/"]` | Where regression tests live; a changed file here must mention the finding id. |

Example:

```toml
[pr_gates.dod]
enabled = true
check_name = "dod/ready-for-review"
checks = ["signed_commits", "conventional_title", "linked_issue"]

[pr_gates.dod.docs_paths]
"^packages/" = ["CHANGELOG.md"]

[pr_gates.dod.regression_profile]
finding_id_pattern = "F\\d+"
test_paths = ["tests/"]
```

## The checks

- `signed_commits`: every commit on the PR is signature-verified.
- `docs_paths`: when a changed path matches a `docs_paths` regex, the
  mapped docs (for example `CHANGELOG.md`) must also be changed.
- `threads_resolved`: no unresolved review threads.
- `base_current`: the PR base equals the default branch head; update the
  branch from main before review (#716).
- `conventional_title`: the title matches Conventional Commits
  (`fix(scope): ...`).
- `linked_issue`: the body contains `Fixes #N` / `Closes #N` /
  `References #N`.
- `regression_test`: every finding id (per
  `regression_profile.finding_id_pattern`) found in review comments must
  appear in a changed test file under `test_paths` (#721). The profile is
  generic; it is not tied to any one review convention.

## Rollout and rollback

1. Merge with the toggle off (the shipped state).
2. Set the config values in `.github/pr-gates.toml`.
3. Do a supervised dry run on a test PR or lane.
4. Get owner sign-off.
5. Flip `pr_gates.dod.enabled = true`.

Rollback is setting `enabled = false`; the feature then has no effect
anywhere. Out of scope by design: changing required check names,
bypassing rulesets, and anything running with admin rights. The workflow
holds only `contents: read`, `pull-requests: read`, and `checks: write`.

## Auditing and safety

- Every posted check run writes a structured audit line (action, repo,
  check name, outcome) to stdout and, when `PR_GATES_DOD_AUDIT` names a
  file, appends there.
- Tokens are redacted before any log or error line; the script never
  prints `GITHUB_TOKEN` material.
- One check run per evaluation, never comments: the check run for a head
  SHA is the only persistent artifact.
