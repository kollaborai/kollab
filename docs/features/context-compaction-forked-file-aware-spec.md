---
title: "Forked File-Aware Context Compaction Spec"
created: 2026-03-05
modified: 2026-03-05
status: proposed
---
# Forked File-Aware Context Compaction Spec

## Status
- Proposed
- Owner: Core LLM + Plugins
- Target: `ContextCompactionPlugin`

## Problem
Current compaction summarizes old conversation messages in-place, but it does not explicitly reason about:
- which files are still relevant to the active task
- which reviewed files can be safely deprioritized
- unresolved work that must stay in context

This can reduce precision in long coding sessions and can lose useful project memory density.

## Goals
- Improve compaction quality by running summarization in a dedicated forked turn.
- Make file relevance explicit (`keep` vs `drop`) during compaction.
- Preserve critical context: unresolved tasks, active files, current objective, decisions.
- Keep compaction safe and reversible.

## Non-Goals
- Changing default tool/permission behavior.
- Replacing existing conversation logger format.
- Building a fully interactive UI workflow in v1 (optional follow-up).

## Proposed Design
Add a new strategy for the compaction plugin:
- `plugins.context_compaction.strategy = "forked_file_aware"`

When threshold is reached, plugin performs:
1. Snapshot current conversation history.
2. Build candidate file list from recent tool activity and git state.
3. Run a forked summarization request (separate from normal conversation turn).
4. Parse structured JSON output with file relevance + summary payload.
5. Validate payload against safety invariants.
6. Stage compacted history and apply on next `LLM_REQUEST_PRE` (existing atomic swap pattern).

## Config Additions
Under `plugins.context_compaction`:
- `strategy`: `"basic"` (default) | `"forked_file_aware"`
- `forked_model_profile`: optional profile override for compaction-only calls
- `forked_timeout_seconds`: int, default `45`
- `max_file_candidates`: int, default `120`
- `always_keep_recent_files`: int, default `12`
- `require_valid_json`: bool, default `true`
- `ask_user_confirm_drops`: bool, default `false` (future UI hook)

## File Candidate Collection
Input sources (priority order):
1. Recent tool calls/results in history:
   - `read`, `edit`, `create`, `terminal` command outputs with file paths
2. Recent explicit file mentions in user/assistant messages
3. `git diff --name-only` (unstaged + staged if available)

Normalization rules:
- Convert to repo-relative canonical paths when possible.
- Deduplicate paths.
- Drop paths outside workspace.
- Hard cap by `max_file_candidates`.

## Forked Compaction Prompt Contract
Forked call receives:
- Session objective summary
- Candidate files list
- Recent unresolved tasks/todos
- Existing conversation slice to summarize

Required model output: strict JSON object:
```json
{
  "summary_markdown": "string",
  "keep_files": ["path/a.py", "path/b.md"],
  "drop_files": ["path/old_experiment.py"],
  "critical_decisions": ["..."],
  "unresolved_tasks": ["..."],
  "risks": ["..."],
  "confidence": 0.0
}
```

Validation:
- `keep_files` and `drop_files` must be disjoint.
- `summary_markdown` non-empty.
- `confidence` in `[0,1]`.
- Invalid JSON -> treat as compaction failure.

## Safety Invariants
Never drop/deprioritize context for:
- files modified in current working tree
- files edited in the most recent `N` turns
- files tied to unresolved tasks
- system and active agent instructions

If invariants fail:
- log validation failure
- abort compaction (no history rewrite)

## History Rewrite Behavior
Existing compaction shape remains:
- optional system message
- injected compaction summary user message
- retained recent messages

In forked strategy, injected summary also includes:
- `Active Files to Keep` list
- `Files Deprioritized` list
- unresolved task checklist

## Logging and Observability
Append JSONL event type:
- `context_compaction_forked`

Event fields:
- `round`
- `candidate_files_count`
- `keep_files_count`
- `drop_files_count`
- `summary_length`
- `confidence`
- `validation_passed`
- `aborted_reason` (optional)

## Failure Handling
On any failure (timeout, invalid JSON, provider error, invariant violation):
- increment failure counter
- keep original history untouched
- continue normal conversation flow
- disable for session after 3 consecutive failures (existing behavior)

## Rollout Plan
Phase 1:
- ship behind config flag (`strategy=basic` default)
- add unit tests and logging only

Phase 2:
- enable for dogfooding sessions
- compare metrics:
  - compaction success rate
  - average tokens per request after compaction
  - tool-call continuity errors

Phase 3:
- consider default switch after stability threshold

## Test Plan
Unit:
- candidate file extraction from mixed tool history
- JSON schema validation pass/fail
- invariant enforcement
- no-op on invalid fork response

Integration:
- long session with many file reads/edits
- compaction preserves active file set
- post-compaction task continuation quality

Regression:
- ensure no orphan tool-call state introduced by compaction rewrite

## Open Questions
- Should `drop_files` require user confirmation in TUI for v1?
- Should compaction include lightweight embeddings for file relevance ranking?
- Should we persist keep/drop decisions for subsequent rounds?

## Acceptance Criteria
- Forked strategy can be enabled with config only.
- Invalid fork response never mutates conversation history.
- Compacted summary explicitly includes file keep/drop decisions.
- Existing non-forked strategy behavior remains unchanged.
