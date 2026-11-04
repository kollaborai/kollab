# pr-gates merge bot

`.github/workflows/auto-merge.yml` (#722) merges a PR by itself once every gate
holds on the PR's current head. It ships off and merges nothing until a
repository owner turns it on.

## Why nothing runs today

Three separate switches are all off:

1. The workflow is disabled on GitHub (`gh workflow enable` turns it on).
2. The repository variable `PR_GATES_AUTO_MERGE` is unset. `dry-run` evaluates
   and logs only; `on` also merges.
3. The `pr-gates` environment holds no `PR_GATES_MERGE_TOKEN`.

Turning it on changes what can merge to the default branch, so only a repository
owner does it.

## What it checks

Each check runs against the PR's current head. Any failure means no merge, and
the run summary names the reason.

- The PR is open, not a draft, targets the default branch, and is not from a fork.
- It has no hold label (`PR_GATES_HOLD_LABELS`). When `PR_GATES_OPT_IN_LABEL` is
  set, it has that label.
- It touches no manual-merge path (`PR_GATES_MANUAL_PATHS`, default `.github/`).
- Every required check in the default branch's rules is green. Finding no
  required checks means no merge.
- The commit status `review/independent` (`PR_GATES_REVIEW_CONTEXT`) is `success`,
  posted by a login in `PR_GATES_REVIEWER_LOGINS` and not by the PR author. An
  unset `PR_GATES_REVIEWER_LOGINS` means no merge.
- A non-author approved this exact head (`PR_GATES_APPROVER_LOGINS` narrows who),
  and no reviewer's latest review requests changes.
- Every review thread is resolved.
- GitHub reports `mergeable_state` as `clean`.

It then merges with `--match-head-commit`, using `PR_GATES_MERGE_METHOD` (default
`merge`). It never uses `--admin`, and it never checks out or runs PR code.

## Turn it on (repository owner)

1. Create the environment `pr-gates`, limit its deployment branches to the default
   branch, and add the secret `PR_GATES_MERGE_TOKEN`: a token for the merge account.
2. Give the independent reviewer a way to post the commit status
   `review/independent`, such as a GitHub App, and set
   `PR_GATES_REVIEWER_LOGINS` to its login.
3. Run `gh workflow enable auto-merge.yml`.
4. Run `gh workflow run auto-merge.yml -f pr=<number> -f dry_run=true`, then read
   the run summary.
5. Run `gh variable set PR_GATES_AUTO_MERGE --body dry-run`, watch a few PRs, then
   run `gh variable set PR_GATES_AUTO_MERGE --body on`.

This workflow does not change the ruleset. To also require `review/independent`
for manual merges, add it with the App's integration id to
`.github/scripts/setup-branch-protection.sh` (#717).

## Turn it off

Run `gh variable delete PR_GATES_AUTO_MERGE`, or `gh workflow disable auto-merge.yml`.
