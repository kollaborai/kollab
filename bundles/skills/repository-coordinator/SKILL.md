---
name: repository-coordinator
description: Coordinate Kollab repository work from issue intake through task ownership, project tracking, implementation, PR integration, documentation, and release readiness. Use for cross-repository or multi-stage coordination; route specialist work to the current repo skills and agents.
---

# Repository coordination

This is Koordinator's end-to-end routing layer for work in the Kollab repository: issue intake, task planning, implementation handoffs, boards, PRs, documentation, and release readiness. It coordinates the work; it does not replace specialist skills, grant permission, or extend the user's goal. “Everything” means the requested repository lifecycle, not unrelated personal/business work or unbounded new goals.

## Start with the real contract

- Read the current repository `AGENTS.md`, `SOFTWARE_DEVELOPMENT_LIFECYCLE.md`, relevant feature and release docs, available skills, and live GitHub rules. Repository instructions and actual host settings outrank stale summaries. If a specialist skill conflicts with a current repo rule, state the conflict and follow the higher-priority current rule; do not silently broaden authority.
- Define the requested scope, observable acceptance, allowed operations, owners, and remaining gates. A concrete authorized plan covers its listed steps without repeated permission requests. This skill grants no authority. Public messages require explicit authorization; release, deploy, install, publish, and tag actions require the specific separate authorization current repo policy demands.
- Honor the requested model and reasoning effort for yourself and delegated reviewers. Verify the actual session/model from available session metadata; if unavailable, report that limit. Never silently substitute or claim a requested model ran.
- Keep independent source review, configured CI, live acceptance, tracker state, and authorization separate. The author does not review their own change. A tracked issue/PR or a green build is not proof of the other gates. Do not invent a GitHub approving-review count; independently verify source review and satisfy only the count required by live repo rules. Use `Blocked` only for a concrete blocker; `In review` means pending evidence or review, not approval.

## Route by work type

Use the installed skill/agent roster, not assumptions about what is loaded. Read specialist guidance only for the relevant work:

- Issue intake, completeness, labels, duplicates, dependencies, stale candidates: read `oss-maintainers` and `github-triage`; use `triager` if available. If a skill is not listed in the active catalog, use the verified repository path `bundles/skills/<name>/SKILL.md` or delegate to an available specialist. Record the existing authorization/batch identity for audit, but do not request permission again for actions already included in it.
- Backlog accounting, milestones, board hygiene, taxonomy drift: read `bundles/skills/backlog-health/SKILL.md`; coordinate with `maintainer` if available.
- PR hygiene, linked issues, review routing, or stale drafts: read `bundles/skills/pr-shepherding/SKILL.md`; use `pr-shepherd` if available. For source overlap, exact-head proof, or merge order, read [integration evidence](references/integration-evidence.md).
- Community replies, contributor help, or docs gaps: read `bundles/skills/community-care/SKILL.md`; draft public text and do not post without authorization.
- Release proposal/readiness: read `bundles/skills/release-planning/SKILL.md`. An actual release run requires separate explicit authorization and `bundles/skills/release-manager/SKILL.md` plus `docs/release-process.md`.
- Source changes: read `bundles/skills/kollab-development/SKILL.md` and route to an available implementation owner. Keep user-facing docs, contributor instructions, and release notes in the same acceptance plan when relevant.
- GitHub Project writes: only when requested; read [issue and project mode](references/issues-and-projects.md). Verify existing user authorization covers the exact writes. If not, prepare a dry-run plan and request the required approval once; do not create an extra approval ceremony for already-authorized steps.

If a named specialist skill or agent is absent from the active runtime, do not claim it ran. Read the repository source if available or assign the task to a verified available owner.

## Coordinate ownership and continuity

- Before assigning, inventory the live issue/PR, linked work, current branches, exact heads, all worktrees, dirty/staged/untracked files, and existing owners. Separate file scopes and dependencies; no concurrent writers own the same paths.
- Each active writer gets one unique worktree, branch, owner, file scope, starting SHA, and observable checkpoint. A GitHub assignee/reviewer is routing metadata, not accepted ownership; record an owner only after that person/agent claims or acknowledges the task.
- Use one designated integrator and one sequential merge queue. Only that integrator may mark ready or merge PRs covered by the concrete authorization, after independent exact-head review, current-base checks, required CI, and resolved review threads. Do not re-ask for covered steps or merge outside scope. Follow the repo's required history method (Kollab requires merge commits). Writers use isolated worktrees; the integration checkout's `main` source stays read-only and may fast-forward only after the corresponding remote PR merge is verified. Read-only reviewers may share a checkout.
- Preserve other people's work before risky integration. Never reset, rebase, force-push, broad-restore, stage another owner's work, or automatically clean/delete existing worktrees or branches. Exact-target cleanup needs explicit authorization. See [coordination and continuity](references/coordination-and-continuity.md).
- Keep the task ledger, GitHub issue/PR, and project board distinct. At checkpoints report what changed, evidence, owner, next gate, and concrete blocker. Carry exact IDs, URLs, paths, SHAs, and timestamps through handoffs so another coordinator can resume without guessing.

## Close work with evidence

- Reconcile issue and PR state against actual code and accepted criteria. Close work only with retained behavior and acceptance evidence or a clear authorized non-fix disposition. Reopened work returns to intake and gets a fresh scope/owner/gate review.
- For merged changes, verify the host's merge commit, required exact-head checks and any live acceptance; then fast-forward local `main` and prove it equals `origin/main`. Check dirty/untracked path hashes where preservation matters. Never infer success from ancestry, labels, board state, or a clean merge tree.
- Track release readiness as separate stages: accepted scope, merged code, docs/changelog, synchronized source, required checks, built artifact identity, published release, installed version, and live verification. Read [release readiness](references/release-readiness.md); do not tag, deploy, install, or publish without separate explicit authorization.
- Keep reports bounded and timestamped. Counts and status must derive from captured source data; JSON, rendered views, and exports must agree. State missing evidence and limits plainly. Do not promise zero regressions, no loss, or full live acceptance without proof.
