# Software Development Lifecycle

This document is the standard for how work moves from idea to merged code in
this repository. The short version: trunk-based development, one git worktree
and one lane branch per task (based on the current release branch), verified
pushes, Conventional Commits, draft PRs into the release branch, and releases
tagged only when Marco decides.

## Standard

We follow Trunk-Based Development: `main` is the single source of truth, work
runs in short-lived lane branches, and each task gets its own git worktree. A
release's lane branches are based on its release branch (`release/<ver>`) and
land there, and the release branch reaches `main` through one release PR (see
"One release PR"). We follow Conventional Commits 1.0.0 for commit messages and
SemVer 2.0.0 for versions. Release mechanics live in `docs/release-process.md`;
the release manager playbook lives in `bundles/skills/release-manager/SKILL.md`.

The agent rules below are Marco's official workflow standard (issue #302, set
2026-10-22). They apply to the koordinator and every coding agent, on every
machine, for all Kollab work, not only one release.

## Roles

- Marco (operator): decides what ships, approves and marks PRs ready (the koordinator
  may too), decides
  releases and tags, and is the only merge authority for product changes.
- Agents: investigate, implement, test, open DRAFT PRs into the release branch,
  keep branches updated, and respond to review. Agents never mark their own PRs
  ready and never merge product changes without explicit authorization.
- Koordinator: routes work, tracks the task ledger, and verifies gates before
  reporting to the operator. Agents report to the koordinator when something
  looks off.

## Intake: issue to tracked task

Work enters through a GitHub issue and becomes a tracked task (via `/goal` or
the work queue) before any code changes. No tracked task, no edits. Small
fixes still get an issue so the worktree, branch, and commit can reference it.

## Worktree convention

Every agent works in its own isolated worktree on its own lane branch, created
outside the repo tree and based on the current release branch:

```bash
git worktree add ../kollab-wt/issue-<N>-<slug> -b issue-<N>-<slug> origin/release/<ver>
```

- Branch name: `issue-<N>-<slug>` where N is the issue number. Docs tasks with
  no issue use a descriptive slug without N (for example
  `docs/software-development-lifecycle`).
- Always base the lane on the current release branch (`origin/release/<ver>`),
  never on `main` or on another lane's branch. If you cannot tell which release
  branch is current, stop and ask the koordinator.
- Never work in a shared checkout. The shared checkout is read-only for agents:
  a coordination surface, not an editing surface. Edit, test, and commit inside
  your own worktree.
- Never stash, reset, clean, restore, or switch branches in the shared
  checkout, and never build a release from uncommitted files or a scratch
  clone.
- Each lane cleans up its own worktree once its work is merged and verified on
  the remote: `git worktree remove ../kollab-wt/issue-<N>-<slug> && git branch -d
  issue-<N>-<slug>`.
- Release steps that touch the shared checkout belong to the operator; see
  "Release process".

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

## Before every push

Confirm all three, and stop if any one does not match:

- `pwd` is your own worktree.
- `git branch --show-current` is your lane's branch.
- `git log --oneline origin/release/<ver>..HEAD` and `git status` show only
  your lane's commits, with no stray files.

## Pushing and verifying

- Push only to your own lane branch: `git push -u origin <lane-branch>`. Never
  push to `main` or `release/*` directly.
- A lane branch targets the release branch. If a PR is needed, it is a draft PR
  into `release/<ver>`, never into `main`.
- Before reporting done, `git ls-remote origin refs/heads/<lane-branch>` must
  equal your local `git rev-parse HEAD`.
- Report done only with the branch name, the full SHA, and that `ls-remote`
  result. For a PR, also confirm its base with `gh pr view`.
- Correct beats fast. A late correct push beats an on-time wrong one; deadlines
  never justify skipping the checks before the push or the verification after
  it.

## Never

- Force-push (`--force`, `-f`, `--force-with-lease`) or rewrite pushed history.
- Merge anything without Marco's explicit approval.
- Delete or remove a worktree or branch that has unpushed commits or
  uncommitted changes.
- Run `git reset`, `git clean`, `git checkout --` or `git stash drop` in any
  shared checkout.

## When something looks off

Stop. Do not fix it destructively. Report to the koordinator with exact paths,
branches, and SHAs. This covers a wrong branch, unexpected commits, a file in
the wrong checkout, or a remote that does not match.

## The live checkout

- The live checkout is the worktree Marco designates for his running setup,
  tracking a `live/<ver>` branch. It runs the engine, web UI, and agents Marco
  uses day to day.
- It is off-limits to all agents: no edits, pulls, resets, or restarts from it.
- It is reset or redeployed to the release branch only when Marco explicitly
  approves.

## Pull requests

- Agents ALWAYS open PRs as drafts into the release branch, never `main`:
  `gh pr create --draft --base release/<ver>`. Draft is the
  default for every agent-opened PR, no exceptions.
- Only Marco or the koordinator marks a PR ready or approves it. Once ready, auto-merge
  (`.github/workflows/auto-merge.yml`) merges it when checks pass.
- Keep the branch updated from the release branch while the PR is open.
- Note any overlapping PRs in the body so the operator can decide disposition.

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
order and timing. Agents merge only with Marco's explicit approval, and never
their own unreviewed work. Auto-merge (`.github/workflows/auto-merge.yml`)
watches PRs into `main` only, so a lane PR into `release/<ver>` never merges
itself: it is merged by hand, with that approval.

## One release PR

- All work for a release folds into a single draft release PR, `release/<ver>`
  into `main`, so the full impact gets one review.
- The operator creates the release branch (`release/<ver>`) and opens that
  release PR, or an agent does on the operator's explicit instruction: the same
  gate as the tag.
- New work lands on the release branch through lane branches and shows up in
  that PR, rather than spawning new long-lived PRs.

## Release process

Full mechanics: `docs/release-process.md`. Summary:

- A release's work reaches `main` only through its one release PR; nobody
  pushes to `release/*` or `main` directly.
- Releases are tagged whenever Marco decides, and agents may create a release
  tag ONLY when Marco explicitly instructs them to.
- Desktop Mac/Linux builds run from the release tag. Full four-platform binary
  builds and all desktop builds remain tag-only; the only PR-time build is the
  `ci/binary-smoke` Linux smoke build (not a required check, no artifacts).
- Before tagging, check all machines for unpushed work.
- Releases are built from the shared `main` checkout, never from worktrees or
  scratch clones. That checkout is read-only for agents, so the steps that
  touch it (fast-forwarding `main`, tagging, the local install) are the
  operator's; an agent runs them only on Marco's explicit instruction, the same
  gate as the tag.

## Hotfixes

Same flow, faster: issue, worktree, minimal fix with a regression test, DRAFT
PR, explicit merge authorization. Hotfix releases follow the normal release
process with the fix merged (or cherry-picked) first.

## Standing rules

- Conventional Commits; a PR body says `Fixes #N` for each issue it closes.
- The product-ready checklist (`docs/product-ready-checklist.md`) applies to
  every change.
- No tags unless Marco says so.
- No engine or web UI restart without approval.
- Each lane cleans up its own worktree once its work is merged and verified on
  the remote.

## Definition of done

- The PR body contains the complete product-ready checklist from `docs/product-ready-checklist.md`, with evidence for completed items and a specific reason for every N/A.
- Tracked task complete, tests green on the required CI checks.
- The lane branch is pushed and verified (`git ls-remote` matches the local
  HEAD), and the PR's base is the release branch.
- PR approved by Marco and merged (or explicitly closed).
- Worktree and branch removed after the merge is verified on the remote.
- Docs updated when behavior or process changed.
