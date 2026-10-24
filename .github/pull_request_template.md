## What changed and why

<!-- Briefly explain what this PR changes and why. -->

## How to try it

<!-- Give reproducible steps or commands. State if behavior is mocked/simulated. -->

## Type of change

- [ ] Bug fix
- [ ] Feature
- [ ] Documentation
- [ ] Refactor
- [ ] Tests or tooling
- [ ] CI or release workflow
- [ ] Package or release metadata

## Validation evidence

<!-- List exact commands, results, required CI checks, and screenshots. For
     public-facing changes, note secret/personal-data review. -->

```text

```

## Product-ready checklist

Complete this for every PR, including docs, CI, refactors, fixes, and tests.
Check an item only when evidence supports it. For a non-applicable item, replace
`[ ]` with `[x] N/A - <specific reason>`. Do not leave items blank, use a blanket
N/A, or claim unverified work. The canonical checklist and evidence guidance are
in `docs/product-ready-checklist.md`.

### Works

- [ ] The requested behavior is complete and was checked manually end to end,
  not only through tests.
- [ ] Tests cover the new behavior and relevant failure paths, and all required
  CI checks for this PR are green. If no behavior changed, give a specific N/A
  reason for the tests; do not claim CI passes until every required check is
  green.
- [ ] Failures are visible and actionable: the message explains what happened
  and what the user can do next; no silent failure paths were added.
- [ ] Existing startup, hub connection, agent launch, and profile-switch flows
  were checked for regressions.

### Intuitive

- [ ] A newcomer can tell what to do next without needing internal knowledge.
- [ ] Labels, buttons, and messages use plain language rather than internal IDs.
- [ ] Sensible defaults work without mandatory hand-editing of `config.json`.
- [ ] Loading, empty, and error states are designed and give a clear next step.
- [ ] Destructive or security-sensitive actions explain the risk and require
  confirmation where appropriate.

### UI and polish

- [ ] Phone, tablet, and desktop screenshots are attached directly to the PR
  (local file paths are not attachments).
- [ ] Changed UI follows existing spacing, fonts, colors, and gem styling.
- [ ] At phone, tablet, and desktop sizes there are no jumps, overflow, cut-off
  content, or browser-console errors.
- [ ] Keyboard access and readable contrast are checked for changed UI.
- [ ] Slow work shows progress and a clear completion or failure state.

### Docs and Learn

- [ ] README, canonical docs, and in-app help are updated for user-visible or
  configuration behavior; otherwise give a specific N/A reason.
- [ ] A Learn entry is added or updated for a user-facing change; otherwise
  give a specific N/A reason.
- [ ] A plain-language entry is added under the `Unreleased` section of
  `CHANGELOG.md`.
- [ ] New or changed settings, flags, and commands are documented with examples;
  otherwise give a specific N/A reason.
- [ ] External setup such as router, VPN, or key configuration is documented
  step by step when needed; otherwise give a specific N/A reason.

### PR hygiene

- [ ] The PR title and commit messages follow Conventional Commits.
- [ ] The PR body says what changed, why, and how to try it, and includes
  `Fixes #<issue-number>` when an issue tracks the work.
- [ ] This complete checklist is present in the PR body, and every item is
  filled with evidence or a specific N/A reason; no result is guessed or left
  blank.
- [ ] Work was done in a task-specific sibling worktree (for example,
  `../kollab-wt/issue-<N>-<slug>`) based on the current release branch (`origin/release/<ver>`), leaving the shared
  checkout untouched.
- [ ] The PR remains a draft until Marco marks it ready; agents do not merge or
  create release tags without explicit authorization.

## Risk and rollout notes

<!-- Mention compatibility, migrations, known gaps, and follow-up work. -->

## Public surface

<!-- Mention changed commands, config, docs, package metadata, APIs, and
     security posture. For public-facing changes, note secret/personal-data
     review. -->

## Linked issues

<!-- Use Fixes #123 when this PR resolves an issue. -->
