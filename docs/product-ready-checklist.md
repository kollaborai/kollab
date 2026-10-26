# Product-ready checklist

This checklist is required for every pull request: features, fixes, refactors,
CI, tests, and documentation changes. Copy all five sections into the PR body
and complete every item before requesting review. An unchecked box is an open
gap, not an implicit pass.

For each item, check it only when it is supported by evidence. If it does not
apply, replace its checkbox with `[x] N/A - <specific reason>`. Do not leave
items blank, use a blanket N/A, or claim work that was not performed. Link the
commands, CI checks, screenshots, or observed behavior where applicable. Label
mocked or simulated checks as such; do not present them as live verification.
Keep the PR draft while any required evidence or work is outstanding.

## Works

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

## Intuitive

- [ ] A newcomer can tell what to do next without needing internal knowledge.
- [ ] Labels, buttons, and messages use plain language rather than internal IDs.
- [ ] Sensible defaults work without mandatory hand-editing of `config.json`.
- [ ] Loading, empty, and error states are designed and give a clear next step.
- [ ] Destructive or security-sensitive actions explain the risk and require
  confirmation where appropriate.

## UI and polish

- [ ] Phone, tablet, and desktop screenshots are attached directly to the PR
  (local file paths are not attachments).
- [ ] Changed UI follows existing spacing, fonts, colors, and gem styling.
- [ ] At phone, tablet, and desktop sizes there are no jumps, overflow, cut-off
  content, or browser-console errors.
- [ ] Keyboard access and readable contrast are checked for changed UI.
- [ ] Slow work shows progress and a clear completion or failure state.

## Docs and Learn

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

## PR hygiene

- [ ] The PR title and commit messages follow Conventional Commits.
- [ ] The PR body says what changed, why, and how to try it, and includes
  `Fixes #<issue-number>` when an issue tracks the work.
- [ ] This complete checklist is present in the PR body, and every item is
  filled with evidence or a specific N/A reason; no result is guessed or left blank.
- [ ] Work was done in the shared checkout on `main` (no worktree, no branch
  switch) and committed by explicit paths, leaving other agents' uncommitted
  edits untouched.
- [ ] The PR remains a draft until Marco or the koordinator marks it ready; agents do not merge or
  create release tags without explicit authorization.

## Evidence notes

Attach screenshots to the PR itself. For tests, list the exact command and
result. For browser tests, state the browser, viewport sizes, and whether the
API was mocked. For external services, state whether a real service or a safe
simulation was used. A screenshot or a green test is evidence only for the
behavior it actually covers. For public-facing changes, also record whether
secrets and personal data were reviewed.
