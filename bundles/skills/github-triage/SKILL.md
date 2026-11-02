---
name: github-triage
description: Triager workflow for kollaborai/kollab issues - classify type/area/priority/status, check completeness, find duplicates and related PRs, propose milestones, and run stale sweeps, all as dry-run batches. Use for "triage", "label issues", "triage #123", "find dupes", "stale pass". Requires the oss-maintainers rulebook.
metadata:
  version: "1"
---

# Issue triage (triager)

Read `oss-maintainers` first. Labels only from its taxonomy.

## Modes

| Invocation | Does |
|---|---|
| `triage` | every open issue with no type label or with `needs-triage`, oldest first |
| `triage #N` | one issue, full depth |
| `sweep` | whole backlog: fixed-by-merged-PR → duplicates → questions → stale |
| `stale` | stale policy pass only |
| `recheck` | items in `status/needs-info|needs-repro` that got a substantive reply |

## Per-issue procedure

1. **Gather**: `gh issue view N --comments --json ...`; linked PRs
   (`gh pr list --search "N in:body"`); prior triage notes; touched code
   (`file-grep` for symbols, error strings, paths named in the issue).
2. **Is it Kollab?** If it is about another product with no Kollab signal → propose
   `invalid` (human closes). Body beats template dropdowns.
3. **Completeness** (bugs): steps, expected vs actual, version (`kollab --version`),
   OS, logs. Narrative steps count; error text or logs count. Missing essentials →
   `status/needs-info` or `status/needs-repro` with 1-3 specific questions drafted
   (comment goes in a separate comment batch, never auto-posted).
   Don't apply needs-* to enhancements or questions, or when it just needs
   engineering investigation.
4. **Classify**: one type, ≥1 area, platform if specific, priority per rulebook §4,
   `regression` if it worked before. Confidence high/medium/low with evidence.
   Low confidence → leave the label off and list it under Escalations.
5. **Dupes/related**: `gh search issues "<error text|symbol>" -R kollaborai/kollab --state open`.
   Apply the rulebook §6 bar. Max 3 matches. Note issue↔draft-PR pairs.
6. **State**: well specified + self-contained + clear done-criteria →
   `status/ready-for-agent`; needs design/product call, credentials, hardware,
   or manual testing → `status/ready-for-human`; otherwise `status/accepted`.
   Remove `needs-triage` when a status is set.
7. **Milestone**: propose only for accepted items with a priority (§5).
8. Add the row to the plan. Do not mutate.

## Bulk first pass (the 76 unlabeled issues)

Batch by area so the human reviews coherent chunks (e.g. B1 hub+relay, B2 acp,
B3 webui, B4 engine/agent, B5 llm/profiles, B6 conversation/goal/voice,
B7 ci/testing/windows, B8 enhancements). Put security-titled items
(e.g. `security(acp)…`, credential handling) in their own escalation list.

## Stale pass

Use rulebook §6 windows and exemptions. Output two lists: propose `status/stale`;
propose closure to the human (with a drafted, issue-specific closing note).
Remove `status/stale` (in a batch) when a human comments substantively.

## Hand-offs

Approved `status/ready-for-agent` items → work packet to `koordinator` (rulebook §8).
Questions and contributor-facing replies → `bea`. Anything with an open PR → tell `noor`.
Report to `iris`.
