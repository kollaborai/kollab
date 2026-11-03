---
name: pr-shepherding
description: PR shepherd workflow for kollaborai/kollab - PR hygiene (title, linked issue, labels, template, checks), review routing, draft and stale PR follow-up, and issue-PR linking, all as dry-run batches. Never merges, approves, or un-drafts. Use for "PR queue", "review routing", "stale PRs", "shepherd #N". Requires the oss-maintainers rulebook.
metadata:
  version: "1"
---

# PR shepherding

Read `oss-maintainers` first. You keep PRs moving; you do not judge or land them.

## Hard limits specific to PRs

- Never `gh pr merge`, `gh pr review --approve`, `gh pr ready`, `--enable-auto`/`--disable-auto`.
  The designated integrator owns readiness and merge after the repository's gates.
- Never push to a PR branch, rebase, or resolve conflicts. That is coder work via koordinator.
- Never re-run or cancel workflows.

## Queue snapshot (read-only)

```bash
gh pr list -R kollaborai/kollab --state open --limit 200 \
  --json number,title,isDraft,author,labels,createdAt,updatedAt,reviewDecision,mergeable,body,headRefName
gh pr checks <n> -R kollaborai/kollab
gh pr view <n> -R kollaborai/kollab --json files,commits,reviews,comments
```

## Per-PR checklist

| Check | Pass when | If not |
|---|---|---|
| Title | conventional `type(scope): summary` | propose retitle text (human or author applies) |
| Linked issue | body has `Fixes/Closes/Refs #n`, issue open | find the issue; propose the link in a comment batch |
| Labels | type + area copied from the linked issue; priority inherited | add in a label batch |
| Template | `.github/pull_request_template.md` sections filled | list the missing sections |
| Checks | ci/tests, ci/standards-check, ci/security-scan green | summarize the failing job + first error line |
| Size | <400 changed lines or clearly justified | suggest split |
| Draft | draft = intentional hold | note age; ask author only via approved comment |
| Conflicts | `mergeable` not CONFLICTING | flag for coder via koordinator |

## Review routing

Route by area labels and `git log --format='%an' -- <paths>` for the touched
paths. Propose reviewers in a batch; never request without approval. If the
PR touches release, workflows, auth, or security paths, mark it
`status/ready-for-human` and tell `iris`.

## Stale PRs

Draft with no commits or human comments for 21 days → propose a nudge comment.
45 days → propose `status/stale` and list for the human to decide close/keep.
Exempt PRs linked to p0/p1 issues.

## Hand-offs

- failing checks / conflicts / requested changes the author agent should fix →
  work packet to `koordinator` (rulebook §8) naming the PR and branch.
- PR changes user-visible behavior → tell `kenji` (changelog, `breaking-change`).
- PR has no issue and looks like a new feature → tell `tomas`.
Report to `iris`.
