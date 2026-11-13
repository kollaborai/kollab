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
- [ ] A plain-language changelog entry is added as
  `changes/<issue>.<section>.md` (see `changes/README.md`). `CHANGELOG.md`
  itself changes only in release PRs.
- [ ] New or changed settings, flags, and commands are documented with examples;
  otherwise give a specific N/A reason.
- [ ] External setup such as router, VPN, or key configuration is documented
  step by step when needed; otherwise give a specific N/A reason.

### PR hygiene

- [ ] The PR title and commit messages follow Conventional Commits.
- [ ] The PR body says what changed, why, and how to try it, and includes
  `Fixes #<issue-number>` when an issue tracks the work.
- [ ] Feature PRs add or link a feature spec; fix PRs include a Fix brief in
  this body or the linked issue; other types are N/A.
- [ ] This complete checklist is present in the PR body, and every item is
  filled with evidence or a specific N/A reason; no result is guessed or left
  blank.
- [ ] Work was done in a uniquely owned worktree and branch based on the
  current PR base (`origin/main` in the maintainer checkout), or a standalone
  contributor branch when no other writer shares the checkout. File ownership
  was clear; existing dirty work, branches, and worktrees were preserved; the
  maintainer integration checkout had no source edits.
- [ ] The PR remains a draft until its designated integrator marks it ready
  within the maintainer-authorized integration plan, batch, or class order, after a different AI
  agent reviews the exact final head, records approval as whichever account
  (`kollaborai` or `maarco`) is not the PR author, and required CI passes.
  Record reviewer identity and exact reviewed SHA in private maintainer audit
  evidence. Keep the GitHub approval plain, with no AI attribution or footer.
  GitHub prohibits author self-approval.
  Once the code-owner gate is enabled, an external PR needs approval from
  `@maarco` or `@kollaborai`; a PR authored by either account needs approval
  from the other. GitHub enforces the owner-account approval, while separate
  AI reviewer identity and exact-head evidence stay in the private audit.
  Every merge uses `kollaborai` with a merge commit; never use `--admin` or a
  ruleset bypass. Work outside the plan needs new authorization, and release
  tags need separate explicit maintainer instruction.
- [ ] An independent clean-room review verdict is posted as a PR comment
  recording the pinned head SHA, base SHA, exact commands run, and PASS or
  FAIL; the reviewer worked in a scratch environment and ran the tests itself.
  A head moved only by merging `main` may carry the reviewer's addendum instead,
  as `SOFTWARE_DEVELOPMENT_LIFECYCLE.md` describes.
- [ ] Independent review started only while the PR head was current with
  `main` (not BEHIND or DIRTY), and the verdict names the base SHA it covered.
- [ ] Every review finding has a failing regression test landed before its
  fix (as its own commit, or marked strict `xfail`); the fix flipped it green
  without editing the test, and removing that marker was the only test change.
  A finding no test can reproduce, such as wording in a doc, says so in its
  fix commit instead.

## Productization checklist

Applies when this PR adds an enhancement that ships as a product surface
(skill, extension, agent, or plugin). Check an item only when evidence supports
it. For a non-applicable item, replace `[ ]` with `[x] N/A - <specific reason>`.
Do not leave items blank, use a blanket N/A, or claim unverified work. The
canonical policy, checklist, and #711-#730 surface mapping are in
`docs/productization.md`.

- [ ] Names its product surface: skill, extension, agent, or plugin.
- [ ] Has an opt-in config toggle that defaults to off (name the key in the surface's docs).
- [ ] Reads repo, owner, check names, identities, schedules, and paths from config or provider discovery; no literal `kollaborai/kollab` or personal host, device, or user names.
- [ ] Documents the toggles and the enable steps.
- [ ] Flags anything inherently Kollab-internal.
- [ ] Hub job registration, ruleset, or required-status changes have recorded owner sign-off (these are not covered by a plain PR).
- [ ] A failing regression test was written and seen to fail before the fix (#721).


## Risk and rollout notes

<!-- Mention compatibility, migrations, known gaps, and follow-up work. -->

## Public surface

<!-- Mention changed commands, config, docs, package metadata, APIs, and
     security posture. For public-facing changes, note secret/personal-data
     review. -->

## Linked issues

<!-- Use Fixes #123 when this PR resolves an issue. -->
