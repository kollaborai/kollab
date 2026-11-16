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
- Trust: CI runs the base branch's copy of the workflow and script
  (`pull_request_target`) and reads PR and linked-issue data only through the
  API, so a PR cannot change its own grade or reach the token. A change to the
  script or the config takes effect once it is on the base branch.
- Freshness: it grades again when a PR is opened, pushed, edited, reopened or
  marked ready. A grade is a snapshot: a later review comment or a new commit on
  `main` does not rerun it, and the `main` ruleset still enforces both at merge.
  Re-run the job from the PR's Checks tab for a fresh grade.
- Local: agents run the same script before pushing or requesting review:

  ```bash
  python scripts/pr_gates_dod.py --repo kollaborai/kollab --pr 123
  ```

  It prints the report and exits 1 when an enabled check fails. Add
  `--head-sha <sha> --post` to also post the check run (requires
  `GITHUB_TOKEN`).

## Configuration

This repository runs it with `enabled = true`, names the two owner accounts as
trusted annotation reviewers, and omits `signed_commits`, which would fail
every agent PR until agent commits are signed (#755). Other repositories must
configure their own trusted list; the code default is empty.

Everything lives in `.github/pr-gates.toml`. The feature is disabled by
default; `enabled = false` fully disables it (nothing posts, every exit
code is 0).

| Key | Default | Meaning |
| --- | --- | --- |
| `pr_gates.dod.enabled` | `false` | Master toggle. Off means no checks run and nothing is posted. |
| `pr_gates.dod.check_name` | `dod/ready-for-review` | Name of the posted check run. |
| `pr_gates.dod.checks` | all configured checks | Which checks run. Remove an entry to toggle that check off. |
| `pr_gates.dod.docs_paths` | `{}` | Maps a regex over changed paths to the doc files that must be updated in the same PR. |
| `pr_gates.dod.spec_or_fix_brief_mode` | `block` | Missing feature specs or fix briefs warn without failing in `warn` mode; `block` fails the check. |
| `pr_gates.dod.regression_profile.finding_id_pattern` | `\bF\d+\b` | Generic finding-id shape used to identify review finding IDs. |
| `pr_gates.dod.regression_profile.test_paths` | `["tests/"]` | Where regression tests live; a changed file here must mention the finding id. |
| `pr_gates.dod.regression_profile.trusted_reviewers` | `[]` | Exact GitHub logins permitted to annotate incidental references; the PR author is never accepted. |

Example:

```toml
[pr_gates.dod]
enabled = true
check_name = "dod/ready-for-review"
checks = ["signed_commits", "conventional_title", "linked_issue", "spec_or_fix_brief"]
spec_or_fix_brief_mode = "warn"

[pr_gates.dod.docs_paths]
"^packages/" = ["CHANGELOG.md"]

[pr_gates.dod.regression_profile]
finding_id_pattern = "\\bF\\d+\\b"
test_paths = ["tests/"]
trusted_reviewers = ["owner-login"]
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
- `spec_or_fix_brief`: `feat` PRs need a changed `docs/specs/*.md` file or a
  linked issue that links one. `fix` PRs need a `Fix brief` section in the PR
  body or linked issue that names `Root cause` and `Regression test`. Other
  title types are exempt. In `warn` mode, missing documentation appears in the
  check summary and the check succeeds; set
  `pr_gates.dod.spec_or_fix_brief_mode = "block"` to make it fail. This repo
  starts in `warn` mode so existing open PRs are reported without being held.
- `regression_test`: every finding id (per
  `regression_profile.finding_id_pattern`) mentioned in review comments must
  appear in a changed test file under `test_paths` (#721). Every occurrence is
  gate-relevant by default, including free text and test summaries. There is no
  prose heuristic. A trusted reviewer may suppress one exact incidental
  occurrence by posting a marker-only PR issue comment while holding the latest
  `APPROVED` review on the current head. The reviewer must be listed in
  `trusted_reviewers` and must not be the PR author. The approval review stays
  plain; the marker is a separate issue comment.

  The collector reads changed test files as immutable GitHub blob data from
  the PR head. The workflow executes only the trusted base-branch script and
  configuration; it never checks out or executes PR code. A changed head or
  unreadable test blob fails the collection rather than grading stale data.

  The marker is an HTML comment containing one JSON object:

  ```html
  <!-- pr-gates-ignore-reference:v1
  {"source_kind":"issue_comment","source_id":123456,
   "source_body_sha256":"<sha256 of exact source body>",
   "finding_id":"F11","occurrence":2,
   "reason":"This occurrence names the F11 test suite in validation output."}
  -->
  ```

  `source_kind` is `issue_comment` or `review_comment`; `source_id` is that
  comment's GitHub ID. `occurrence` is 1-based among matches of that ID in that
  exact comment. The body hash makes edits invalidate the annotation. A valid
  marker masks only that one occurrence; duplicates, other comments, and other
  occurrences of the same ID remain gate-relevant. Missing metadata, malformed
  markers, an edited source, an untrusted or PR-author marker author, or a
  missing, stale, dismissed, ambiguous, or non-approved current-head review
  means no suppression. The configured regex defines the ID syntax and remains
  generic. Accepted suppressions, including the source hash, reviewer, review
  ID, occurrence, and reason, appear in the check summary and structured audit
  output. The tool never creates the marker or changes a GitHub review. The
  current workflow does not trigger on issue comments, so after posting a
  marker-only comment, rerun the workflow or use an existing PR event trigger
  before relying on the updated check result.

## Rollout and rollback

This repository has finished this rollout. To turn the check on in another one:

1. Start with `enabled = false`.
2. Set the config values in `.github/pr-gates.toml`.
3. Do a supervised dry run on a test PR or lane.
4. Get owner sign-off.
5. Flip `pr_gates.dod.enabled = true`.

Rollback is setting `enabled = false`; the feature then has no effect
anywhere. Out of scope by design: changing required check names,
bypassing rulesets, and anything running with admin rights. The workflow
holds only `contents: read`, `issues: read`, `pull-requests: read`, and
`checks: write`.

## Auditing and safety

- Every posted check run writes a structured audit line (action, repo,
  check name, outcome) to stdout and, when `PR_GATES_DOD_AUDIT` names a
  file, appends there.
- Every accepted incidental-reference annotation also writes its source
  comment ID and body hash, finding ID and occurrence, trusted reviewer and
  approval review ID, and reason to the check summary and audit log. An invalid
  annotation never hides a finding mention.
- Tokens are redacted before any log or error line; the script never
  prints `GITHUB_TOKEN` material.
- One check run per evaluation, never comments: the check run for a head
  SHA is the only persistent artifact.
