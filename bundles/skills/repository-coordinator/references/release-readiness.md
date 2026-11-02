# Release readiness and verification

Read this reference for release planning, packaging, publication, or deployment status. `release-planning`, `release-manager`, and `docs/release-process.md` own detailed procedures; this reference keeps the coordination state honest.

## Route and gate

- For a proposal, read `release-planning`, the canonical changelog, current release docs, and current merged PR/issue state. A proposed version, milestone, or changelog is planning evidence only.
- For an actual release, obtain separate explicit authorization for the actions in scope, then read and follow `release-manager` and `docs/release-process.md`. Release-source/version changes land through a reviewed worktree PR; build only from clean, synchronized merged `main`.
- Keep separate evidence for: accepted scope; PRs merged and exact merge SHAs; docs/changelog completeness; current main and required checks; build identity and artifact verification; publication; installed version; and live deployment/acceptance. A tag, green build, or release page alone does not prove the later stages.
- Verify versions, dependency constraints, lockfiles, changelog agreement, required workflows, artifact metadata/identity, and the exact source commit using the current release process. After installation or deployment, verify the actual binary/host and report what remains unverified.
- Release authorization does not automatically authorize deployment, installation, tag creation, or external announcements. Never create/move a tag, publish, deploy, install, or message external parties without the specific authorization required by the current repo rules and the user.

## Handoff

Report the target version, exact source and merge SHAs, checks and artifact evidence, published/installed/live state, remaining gates, and the next authorized owner. Keep planned, ready, released, installed, and live-verified states distinct.
