# Software Development Lifecycle

This document is the standard for how work moves from idea to merged code in
this repository. The short version: trunk-based development in one shared
`main` checkout with no worktrees, Conventional Commits, draft PRs by default,
and releases tagged only when Marco decides.

## Standard

We follow Trunk-Based Development: `main` is the single source of truth, and
every agent commits on it in the one shared checkout. A change reaches GitHub
through a short-lived PR branch pushed from that checkout; nobody works in a
worktree or a local feature branch. We follow Conventional Commits 1.0.0 for
commit messages and SemVer 2.0.0 for versions. Release mechanics live in
`docs/release-process.md`; the release manager playbook lives in
`bundles/skills/release-manager/SKILL.md`.

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

## Work on main

Everyone works in the one shared checkout on `main`. Never create a git
worktree (unless Marco asks for one), and never stash, reset, clean, restore,
or switch branches in the checkout.

- Several agents edit the tree at once. Run `git status --short` before you
  touch a file, leave other agents' uncommitted edits alone, and check with
  whoever is editing a file before you change it too.
- Commit only your own files: `git commit -m "<message>" -- <paths>`. Never
  `git add -A`, `git add .`, or a tree-wide `ruff --fix` or `black`.
- `main` is protected, so a change lands through a draft PR pushed straight
  from the checkout, with no local branch:

  ```bash
  git log --oneline origin/main..HEAD          # every commit listed ships
  git push origin HEAD:refs/heads/issue-<N>-<slug>
  gh pr create --draft --head issue-<N>-<slug> --title "<type(scope): summary>" --body "Fixes #<N>"
  ```

- PR branch name: `issue-<N>-<slug>` where N is the issue number. Docs tasks
  with no issue use a descriptive slug without N (for example
  `docs/software-development-lifecycle`).
- Review fixes go out the same way: commit on `main`, push `HEAD` to the same
  PR branch.
- PRs merge with a merge commit, never a squash or rebase, so local `main`
  stays a parent of the merge. Once yours merges,
  `git pull --ff-only origin main`.
- If that pull refuses, `main` has diverged from `origin/main`: stop and tell
  Marco. Never reset, rebase, or force-push to fix it.
- Never build a release from uncommitted files or a scratch clone. Releases
  run in this same checkout per `docs/release-process.md`.

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
- Merging needs the PR branch up to date with `main`. When `main` moves,
  `gh pr update-branch <n>` merges it in on GitHub (never a rebase). If your
  next push to that branch is refused, run
  `git pull --no-rebase origin issue-<N>-<slug>` first, never `--force`.
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
order and timing. Agents merge only when a task explicitly authorizes it, and
never their own unreviewed work.

## Release process

Full mechanics: `docs/release-process.md`. Summary:

- Releases are tagged whenever Marco decides, and agents may create a release
  tag ONLY when Marco explicitly instructs them to.
- Desktop Mac/Linux builds run from the release tag. Full four-platform binary
  builds and all desktop builds remain tag-only; the only PR-time build is the
  `ci/binary-smoke` Linux smoke build (not a required check, no artifacts).
- Before tagging, check all machines for unpushed work.
- Releases are built from the shared `main` checkout, never from worktrees or
  scratch clones.

## Hotfixes

Same flow, faster: issue, minimal fix on `main` with a regression test, DRAFT
PR, explicit merge authorization. Hotfix releases follow the normal release
process with the fix merged (or cherry-picked) first.

## Definition of done

- The PR body contains the complete product-ready checklist from `docs/product-ready-checklist.md`, with evidence for completed items and a specific reason for every N/A.
- Tracked task complete, tests green on the required CI checks.
- PR approved by Marco and merged (or explicitly closed).
- Local `main` pulled level after merge: `git pull --ff-only origin main`.
- Docs updated when behavior or process changed.
