---
name: backlog-health
description: Maintainer workflow for kollaborai/kollab - backlog accounting, label-taxonomy rollout and drift checks, milestone scheme, project-board hygiene, and the weekly health report. Use for "set up labels", "backlog health", "how many issues", "milestones", "clean up the board". Requires the oss-maintainers rulebook.
metadata:
  version: "1"
---

# Backlog health (maintainer)

Read `oss-maintainers` first. You own the system, not individual verdicts:
the taxonomy, the priority scheme, milestones, the board, and the team's cadence.

## Modes

| Ask | Mode |
|---|---|
| (no args), "status", "how bad is it" | Accounting |
| "set up labels", "taxonomy" | Taxonomy rollout |
| "milestones", "plan releases" | Milestone setup (with release-planner) |
| "board", "project" | Board hygiene |
| "weekly report" | Health report |

## Accounting (read-only, always first)

```bash
gh label list -R kollaborai/kollab --limit 200 --json name,color,description
gh issue list -R kollaborai/kollab --state open --limit 500 --json number,title,labels,assignees,milestone,createdAt,updatedAt,author
gh pr list    -R kollaborai/kollab --state open --limit 200 --json number,title,labels,isDraft,createdAt,updatedAt,author,reviewDecision,body
gh api 'repos/kollaborai/kollab/milestones?state=all'
```

Report: open issues/PRs; % with a type, priority, area, status; unassigned;
no milestone; age buckets (<7d, 7-30d, 30-90d, >90d); draft vs ready PRs;
issue↔PR pairs found via "Fixes/Closes/Refs #n"; labels in use that are not in
`labels.json` (drift); top 5 actions. Then ask which mode to run.

Baseline (2026-11-01, verified read-only): 78 open issues (76 unlabeled, 2 `bug`),
9 open PRs (all drafts, unlabeled), 0 milestones, only the 9 GitHub default labels.

## Taxonomy rollout

1. Diff `references/labels.json` (oss-maintainers) against `gh label list`.
2. Plan batch T1 = create missing labels, `needs-triage` first. Commands:
   `gh label create "<name>" -R kollaborai/kollab --color <hex> --description "<desc>"`
   (no `--force`; an existing label is reported, not overwritten).
3. Plan batch T2 = edit descriptions of kept defaults only if the human wants it.
4. Never delete or rename a label. Propose it, with the count of items using it.
5. After T1 is applied, hand the backlog to the triager (hub-msg `tomas`).

## Milestone setup

Propose the scheme from the rulebook §5 with the release-planner's version
proposal (`kenji`). Commands go in batch M1:
`gh api -X POST repos/kollaborai/kollab/milestones -f title=v0.19.0 -f description=...`
(only after approval; never close or delete milestones).

## Board hygiene

The repo has Projects enabled; whether a board exists is unverified. If the human
wants one, propose columns that mirror status labels: Needs triage → Accepted →
Ready (agent/human) → In progress (linked open PR) → Done. Do not create a
project without approval.

## Health report (weekly)

Same numbers as Accounting plus deltas since last report, p0/p1 list with owner
and milestone, stale candidates from the triager, PRs waiting >7 days from the
PR shepherd, and next release readiness from the release planner. Write to
`.kollab/oss/reports/<date>-health.md` and summarize in chat.

## Coordinating the team

You are the lead. Split work by hub-msg: incoming/untriaged → `tomas`; PRs →
`noor`; versions/changelog → `kenji`; questions/docs/contributor replies → `bea`.
Merge their plans into one approval request per run so the human approves once.
Settle taxonomy disputes; propose new labels rather than letting agents invent them.
