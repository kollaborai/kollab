# Software Development Lifecycle

This document is the standard for how work moves from idea to merged code in
this repository. The short version: trunk-based development, one isolated
worktree and branch per active writer, one named integrator, draft PRs by
default, and releases tagged only when Marco explicitly decides.

## Standard

We follow Trunk-Based Development: `main` is the single source of truth for
merged work. Each active writing lane uses its own worktree and branch based on
the latest `origin/main`; source changes reach `main` through a reviewed PR.
Conventional Commits 1.0.0 apply to commit messages and SemVer 2.0.0 to
versions. Release mechanics live in `docs/release-process.md`; the release
manager playbook lives in `bundles/skills/release-manager/SKILL.md`. The rules
below are canonical for Kollab; shorter repo instructions and templates point
here for detail.

## Roles

- Marco (operator): decides what ships, approves and marks PRs ready (the koordinator
  may too), decides
  releases and tags, and is the only merge authority for product changes.
- Agents: investigate, implement, test, open DRAFT PRs, keep branches updated,
  and respond to review. Agents never mark their own PRs ready and never merge
  product changes without explicit authorization.
- Koordinator: routes work, tracks the task ledger, and verifies gates before
  reporting to the operator.

## Intake: issue to tracked task

Work enters through a GitHub issue and becomes a tracked task (via `/goal` or
the work queue) before any code changes. No tracked task, no edits. Small
fixes still get an issue so the PR branch and commit can reference it.

## Writer worktrees and main integration

- Each active writing lane has one named owner, one unique worktree, and one
  branch. Record the owner, worktree path, branch, and file scope before edits.
  Do not assign concurrent writers to the same paths; arrange a clear handoff
  before ownership changes.
- Read-only reviewers may inspect a shared checkout. They do not edit it.
- Preserve every existing worktree, branch, staged change, and dirty file unless
  its owner explicitly authorizes a specific change. Never automatically clean
  up or delete worktrees or branches.
- The local `main` worktree is the integration checkout. Keep its source
  files read-only: inspect them or fast-forward after changes have already
  merged to remote `main`. Fetching refs and registering a new worktree may be
  done from the repository context; neither changes source in local `main`.
  Never edit, stage, commit, or push source there, or switch, reset, stash,
  clean, or restore it.
- Before creating a writer lane, inspect status and all worktrees, fetch the
  intended base, and record the exact `origin/main` SHA. Confirm the requested
  worktree path, local branch, and remote branch are unused; do not reuse an
  existing path or branch.

From the integration checkout, a new lane can be created like this after the
path and branch absence checks:

```bash
git status --short --branch
git worktree list
git fetch origin main
git rev-parse origin/main
test ! -e "../kollab-wt/<task-slug>" && test ! -L "../kollab-wt/<task-slug>" || { echo "worktree path exists"; exit 1; }
! git show-ref --verify --quiet "refs/heads/issue-<N>-<slug>" || { echo "local branch exists"; exit 1; }
remote_branch=$(git ls-remote --heads origin "refs/heads/issue-<N>-<slug>") || { echo "remote branch check failed"; exit 1; }
test -z "$remote_branch" || { echo "remote branch exists"; exit 1; }
git worktree add -b "issue-<N>-<slug>" "../kollab-wt/<task-slug>" origin/main
```

Use `issue-<N>-<slug>` for issue work and a descriptive `docs/<slug>` branch
for documentation work without an issue. Check the new worktree's status
before editing. Do not copy uncommitted files from the integration checkout
into a lane without its owner's authorization.

From the owning worktree, commit only owned paths, set the PR branch upstream on
the first push, and create a DRAFT PR:

```bash
git status --short --branch
git commit -m "fix(cli): correct version display" -- kollabor/cli.py
git push -u origin HEAD:refs/heads/issue-8-version-display
gh pr create --draft --head issue-8-version-display --title "fix(cli): correct version display" --body "Fixes #8"
```

## Commits and PRs

- Make small, scoped commits from the owning worktree, naming only the paths
  owned by that lane. Use Conventional Commits and include the issue reference
  when one exists. Never broadly stage another writer's files.
- Push the lane branch normally and open a DRAFT PR against `main`. Agents do
  not mark their own PR ready or merge it. Note overlapping PRs and path
  ownership so the integrator can sequence them.
- Review must name the exact PR head SHA and current `main` base SHA. Required
  CI checks must be green on that head, the branch must include the current
  base, and all review conversations must be resolved before merge.
- When `main` advances, update the writer branch by fetching and merging
  `origin/main` into it, then push normally. If the remote PR branch advanced,
  fetch and merge that branch too. Resolve conflicts only within owned paths;
  stop and coordinate if a conflict crosses another owner's scope. Never rebase
  or force-push; preserve history with ordinary merges.
- One named integrator coordinates each merge queue and lands authorized PRs
  with a merge commit; never squash or rebase. Marco remains the merge authority
  for product changes, with approval/authorization requirements below.
- After a PR has already merged, the integration checkout may run
  `git pull --ff-only origin main`. Verify its HEAD equals `origin/main`.
  If the pull refuses or the SHAs differ, stop and report the exact local,
  remote, and PR state. Do not reset, rebase, or force-push to hide divergence.

## Main protection and merge authority

`main` is protected. Agents investigate, implement, test, open DRAFT PRs, keep
their own branches current, and respond to review. Marco decides what ships,
approves and marks PRs ready (the koordinator may too), decides releases and
tags, and is the merge authority for product changes. Agents never mark their
own PRs ready and never merge product changes without explicit authorization.
The designated integrator is the single owner of merge sequencing and post-merge
`main` synchronization; designation does not grant release-tag authority.

## Pull request gates

Agents ALWAYS open PRs as drafts. Only Marco or the koordinator marks a PR
ready or approves it. Once authorized and ready, the repository's configured
merge workflow may merge it with a merge commit when required checks pass.

A PR must be current with `main`, have all required checks green, and have all
conversations resolved. Required checks are `ci/tests`,
`ci/standards-check`, and `ci/security-scan`. Record the exact reviewed head
SHA and the tested head SHA; a new push requires review and CI evidence for the
new head. Desktop Mac/Linux builds (the `binaries` and `desktop` workflows)
run only on release tags and are not merge requirements. `ci/binary-smoke` is
an early Linux build, not a required merge check.

## Implement and test

- Investigate first: read the code, its history, and the docs before changing
  anything.
- Respect package boundaries and follow existing patterns.
- Add or update tests for new behavior, including failure modes.
- Run the targeted suite while iterating, then the full unit suite before
  opening the PR.
- Python 3.14 is the language floor and the launch runtime.

## Commits

Conventional Commits 1.0.0: `type(scope): summary`.

```bash
git commit -m "feat(webui): copy a profile and pin its reasoning effort"
```

- Types: feat, fix, docs, chore, refactor, test, ci, build, perf.
- Reference issues in the body: `Fixes #N`.
- Small, atomic commits. Imperative mood. Explain why when it is not obvious.

## Pull requests

- Agents ALWAYS open PRs as drafts: `gh pr create --draft`. Draft is the
  default for every agent-opened PR, no exceptions.
- Only Marco or the koordinator marks a PR ready or approves it. Once ready, auto-merge
  (`.github/workflows/auto-merge.yml`) merges it when checks pass.
- Merging needs the PR branch up to date with `main`. The writer updates its
  owned branch by fetching and merging `origin/main`, then pushing normally;
  preserve history with merge commits and never rebase or force-push.
- Note overlapping PRs and file ownership in the body so the integrator can
  sequence them.

## CI gates

Required checks: `ci/tests`, `ci/standards-check`, `ci/security-scan`. Merge
also requires the branch to be up to date with `main` and all PR
conversations resolved.

Desktop Mac/Linux builds (the `binaries` and `desktop` workflows) run only on
release tags. They are never PR checks and never merge requirements. The one
exception: `ci/binary-smoke` runs one quick Linux binary smoke build on every
PR and main push (about 49 seconds, no artifacts). It proves the build works
early; it is not a required merge check.

## Review and merge

An approving review from Marco is required before merge. Marco decides merge
order and timing. Agents merge only when a task explicitly authorizes it, and
never their own unreviewed work.

## Release process

Full mechanics: `docs/release-process.md`. Summary:

- Author release source and version-preparation changes in a dedicated
  worktree/branch and merge them through the same reviewed PR gate.
- Build from the exact, clean, synchronized merged `main` checkout, never from
  an unmerged writer branch or scratch clone.
- Releases are tagged whenever Marco decides; agents may create a release tag
  ONLY when Marco explicitly instructs them to.
- Before tagging, check all machines for unpushed work and prove the integration
  checkout HEAD equals `origin/main` at the intended release merge commit.

## Hotfixes

Same gates, shorter scope: issue, minimal fix and regression test in an isolated
writer worktree, DRAFT PR, exact-head CI/review, explicit merge authorization,
and merge commit by the designated integrator. Hotfix releases follow the
normal release process after the fix is merged and `main` is synchronized.

## Definition of done

- The PR body contains the complete product-ready checklist from `docs/product-ready-checklist.md`, with evidence for completed items and a specific reason for every N/A.
- Tracked task complete, tests green on the required CI checks.
- PR approved by Marco and merged (or explicitly closed).
- The designated integrator fast-forwarded local `main` after merge:
  `git pull --ff-only origin main`.
- Docs updated when behavior or process changed.
- The handoff records exact worktree path, branch, base SHA, local HEAD, remote
  branch SHA, PR number/state, reviewed head SHA, required CI state, and the
  integration checkout's local/remote main SHAs. Any mismatch or dirty path is
  named; never claim synchronization from labels alone.
