# Compact Preview Finish PRD

## Objective

Finish `/compact preview` so context compaction is understandable before it is
destructive.

## User Impact

Users should see what will be preserved, removed, pinned, and roughly how many
tokens change before applying compaction.

## Scope

- Extend `plugins/context_compaction_plugin.py` and related tests.
- Keep preview non-mutating.
- Add or harden apply/confirm behavior so it either reuses a preview snapshot or
  reruns with clear timestamp/source.
- Stabilize preserved/removed/pinned bucket semantics.
- Avoid changing unrelated context state architecture.

## Acceptance Criteria

- `/compact preview` remains non-mutating.
- Apply/confirm mutates expected history only.
- Preview output includes preserved, removed, pinned, and token delta.
- Pin/drop buckets are stable under tests.
- User-facing output is concise and terminal-friendly.

## Validation

- Run context compaction unit tests.
- Add regression tests for preview/apply separation.
- Run stabilization gate if shared context behavior changes.

## Deliverable

Open a PR from `codex/compact-preview-finish` to `main` with before/after command
output snippets and validation evidence.
