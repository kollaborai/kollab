---
title: "ADR-0001 Architecture Doc Taxonomy"
doc_type: architecture-decision
created: 2026-04-22
modified: 2026-04-22
status: accepted
---
# ADR-0001: Architecture Doc Taxonomy

## Status

Accepted on 2026-04-22.

## Context

`docs/architecture/` had grown into a mixed folder containing current
architecture references, draft proposals, dated spec batches, review
writeups, implementation records, audits, and test-result snapshots.

That made it hard to answer basic questions:

- Which docs describe the current system?
- Which docs are proposals?
- Which docs are historical engineering records?
- Where should a new architecture document go?

## Decision

Organize `docs/architecture/` into these buckets:

- top level: current canonical architecture references in an `arc42`-style
  layout for the repo's main system views
- `reference/`: supplemental current-state reference material that is more
  detailed or more specialized than the top-level canon
- `rfcs/`: proposals, design explorations, and dated spec batches that
  describe intended or evaluated changes
- `decisions/`: accepted architecture decisions using ADR naming
- `records/`: historical engineering records such as reviews, audits,
  implementation records, and test-result snapshots
- `archive/`: superseded or intentionally retired material

## Consequences

- Canonical docs stay easy to find.
- Proposal docs no longer compete with current-state references.
- Reviews and implementation records remain searchable without looking like source of truth.
- Future accepted decisions should be added as `ADR-xxxx-*.md` in
  `docs/architecture/decisions/`.
- Superseded docs should move to `docs/architecture/archive/` instead of
  remaining mixed into active folders.
