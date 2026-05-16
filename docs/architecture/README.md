---
title: "Architecture Docs"
doc_type: architecture-index
created: 2026-04-22
modified: 2026-04-22
status: active
---
# Architecture Docs

This folder follows an `arc42 + ADR + RFC` taxonomy.

The canonical current-state architecture docs stay at the top level.
Everything else is sorted by role: supplemental reference, RFCs,
accepted decisions, historical records, or archive.

## Start Here

- [architecture-overview.md](architecture-overview.md) - Monorepo structure, package boundaries, data flow
- [terminal-rendering-architecture.md](terminal-rendering-architecture.md) - TUI rendering model and coordinator-facing rules
- [event-system-architecture.md](event-system-architecture.md) - Event bus, hooks, and execution flow
- [tool-calling-architecture.md](tool-calling-architecture.md) - XML/native tool-calling protocols and shared backend

## Supplemental Reference

- [reference/altview-framework-reference.md](reference/altview-framework-reference.md) - Alternate-buffer view lifecycle and session model
- [reference/attach-mode-rendering-pipeline-reference.md](reference/attach-mode-rendering-pipeline-reference.md) - Attach client and daemon rendering path
- [reference/agent-hud-reference.md](reference/agent-hud-reference.md) - Model-facing HUD diffs and hub wake rules
- [reference/agent-dns-reference.md](reference/agent-dns-reference.md) - Agent discovery, identity, and trust notes

## Decisions

- [decisions/ADR-0001-architecture-doc-taxonomy.md](decisions/ADR-0001-architecture-doc-taxonomy.md) - Standardizes the architecture doc layout

## RFCs

- [rfcs/README.md](rfcs/README.md) - Placement rules for architecture RFCs and dated spec batches
- [rfcs/RFC-2026-04-06-agent-designation-merge.md](rfcs/RFC-2026-04-06-agent-designation-merge.md) - Agent vs identity redesign proposal *(rejected)*
- [rfcs/RFC-2026-04-04-agent-hub-unification.md](rfcs/RFC-2026-04-04-agent-hub-unification.md) - Multi-phase hub and agent convergence plan *(shipped)*
- [rfcs/RFC-2026-04-05-kollabor-mesh.md](rfcs/RFC-2026-04-05-kollabor-mesh.md) - Cross-runtime agent bridge draft *(draft)*

## Records

- [records/README.md](records/README.md) - Placement rules for reviews, audits, implementation records, and test snapshots
- [records/reviews/REVIEW-2026-04-06-agent-designation-merge-architecture.md](records/reviews/REVIEW-2026-04-06-agent-designation-merge-architecture.md) - Architecture critique of the identity redesign
- [records/reviews/REVIEW-2026-04-06-agent-designation-merge-ux.md](records/reviews/REVIEW-2026-04-06-agent-designation-merge-ux.md) - UX review of the same proposal
- [records/implementation/IMPLEMENTATION-2026-04-05-hub-system-release-v0.5.0.md](records/implementation/IMPLEMENTATION-2026-04-05-hub-system-release-v0.5.0.md) - Hub system release implementation record
- [records/implementation/IMPLEMENTATION-2026-04-08-daemon-first-architecture.md](records/implementation/IMPLEMENTATION-2026-04-08-daemon-first-architecture.md) - Daemon-first runtime implementation record
- [records/implementation/IMPLEMENTATION-2026-04-10-daemon-transparency-refactor-phase-4-5.md](records/implementation/IMPLEMENTATION-2026-04-10-daemon-transparency-refactor-phase-4-5.md) - Daemon transparency refactor implementation record
- [records/implementation/IMPLEMENTATION-2026-04-18-wait-for-user-loop-fix.md](records/implementation/IMPLEMENTATION-2026-04-18-wait-for-user-loop-fix.md) - wait_for_user loop fix implementation record
- [records/audits/AUDIT-2026-04-05-hub-completion.md](records/audits/AUDIT-2026-04-05-hub-completion.md) - Hub completion checklist and fixes
- [records/audits/AUDIT-2026-04-10-plugin-command-migration-phase-4-5-step-8.md](records/audits/AUDIT-2026-04-10-plugin-command-migration-phase-4-5-step-8.md) - Plugin command migration audit
- [records/audits/AUDIT-2026-04-20-hub-bridge-verification-gaps.md](records/audits/AUDIT-2026-04-20-hub-bridge-verification-gaps.md) - Hub bridge and notification verification gaps
- [records/test-results/TEST-RESULTS-2026-04-05-hub-system.md](records/test-results/TEST-RESULTS-2026-04-05-hub-system.md) - Hub validation results

## Dated RFC Batches

- [rfcs/RFC-2026-04-11-core-systems-batch.md](rfcs/RFC-2026-04-11-core-systems-batch.md) - Coordinated spec batch for loop prevention, notifications, tool loading, and context service *(historical-index)*
- [rfcs/RFC-2026-04-14-agent-pool-and-project-agents.md](rfcs/RFC-2026-04-14-agent-pool-and-project-agents.md) - Agent pool and project-scoped agent proposal *(revised-spec)*
- [rfcs/RFC-2026-04-20-hub-project-scoping.md](rfcs/RFC-2026-04-20-hub-project-scoping.md) - Project-scoped hub state rollout *(shipped)*

## Archive

- [archive/README.md](archive/README.md) - Where retired or superseded architecture material should go
