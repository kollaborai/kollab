---
name: release-planning
description: Release planner workflow for kollaborai/kollab - propose the next version, map accepted issues and merged PRs to milestones, draft CHANGELOG entries and release notes, and produce a readiness checklist. Plans only; never bumps versions, commits, tags, publishes, or creates releases. Use for "what's in the next release", "draft changelog", "release readiness". Requires the oss-maintainers rulebook.
metadata:
  version: "1"
---

# Release planning

Read `oss-maintainers` first, then `docs/release-process.md`,
`SOFTWARE_DEVELOPMENT_LIFECYCLE.md`, `CHANGELOG.md`, and (read-only, for the
invariant and topology) `bundles/skills/release-manager/SKILL.md`.
The `release-manager` skill is for a human-authorized release run. You do not
load it as your own procedure and you never perform its mutations.

## Never

bump versions, edit files in the repo, commit, tag, push, create a GitHub
Release, upload to PyPI, dispatch Homebrew, or close milestones.

## Inputs (read-only)

```bash
gh release list -R kollaborai/kollab --limit 10
git log --oneline <last-tag>..origin/main
gh pr list -R kollaborai/kollab --state merged --search "merged:>=<last-release-date>" --json number,title,labels,body
gh issue list -R kollaborai/kollab --milestone <m> --state all
```

## Outputs (files under `.kollab/oss/release/`, never in the repo tree)

1. **Version proposal**: patch vs minor with reasons (any `breaking-change` or new
   user-facing feature → minor; fixes only → patch).
2. **Milestone map**: which accepted items belong in the next patch/minor; p0/p1
   gaps with no fix PR.
3. **Changelog draft** in the existing `CHANGELOG.md` style, grouped by type,
   each line citing PR/issue numbers. Mark unverified lines.
4. **Readiness checklist**: open p0/p1 in the milestone, draft PRs it depends on,
   CI status on main, unreleased `breaking-change` notes, docs gaps. Verdict:
   ready / not ready with blockers.

Milestone create/assign proposals go to `iris` as a batch; the human approves.
The actual release is cut by the human (optionally with a coder running the
`release-manager` skill under explicit authorization via koordinator).
Report to `iris`.
