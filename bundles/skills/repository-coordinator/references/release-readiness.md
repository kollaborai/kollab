# Release readiness and verification

Read this reference for release planning, packaging, publication, or deployment status. Discover the target project's current release procedure, source of truth, available tools and applicable specialists; none of those has a universal filename or provider.

## Route and gate

- For a proposal, inspect the current changelog or equivalent, release guidance, and merged change/issue state when available. A proposed version, milestone, or changelog is planning evidence only.
- For execution, follow the project's actual release procedure and verify that the user's existing authorization covers the specific actions. If policy or the user requires a separate authorization, obtain it; do not re-ask for actions already covered by an explicit approved plan. Use an applicable specialist if available, but do not require a particular skill. Follow the project's source review, checkout, and build-source requirements.
- Keep separate evidence for: accepted scope; merged changes and exact merge revisions; docs/changelog completeness; intended base and required checks; build identity and artifact verification; publication; installed version; and live deployment/acceptance. A tag, green build, or release page alone does not prove the later stages.
- Verify versions, dependency constraints, lockfiles, changelog agreement, required workflows, artifact metadata/identity, and the exact source revision as applicable to the release. After installation or deployment, verify the actual binary/host and report what remains unverified.
- A release proposal alone does not authorize execution. Do not create/move a tag, publish, deploy, install, or send an external announcement without the specific authorization required by the user and current project rules; an existing explicit plan may already cover some or all of these actions.

## Handoff

Report the target version, exact source and merge SHAs, checks and artifact evidence, published/installed/live state, remaining gates, and the next authorized owner. Keep planned, ready, released, installed, and live-verified states distinct.
