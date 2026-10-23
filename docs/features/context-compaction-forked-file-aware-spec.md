---
title: "Forked File-Aware Context Compaction Spec"
created: 2026-03-05
modified: 2026-10-22
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

Current cross-strategy guardrail settings:
- `token_threshold_k`: optional lower token threshold in thousands; it cannot
  raise the effective safety budget.
- `compaction_ratio`: fraction of the active provider context window to use as
  the prompt budget (default `0.75`).
- `max_session_turns`: log and visibly warn after this many model turns
  (default `90`; `0` disables the warning).
- `summarization_model`: optional compaction-only model override.
- `hard_cap_compaction_attempts`: bounded synchronous retries at a hard-cap
  boundary (default `2`).

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

## Long-Session Guardrails (All Strategies)

These guards apply to both the basic and forked strategies:

- The hard prompt budget is the minimum of the active provider's
  `context_window * compaction_ratio`, the optional `token_threshold_k`
  override, and the existing 272K automatic cost/latency ceiling. A manual
  threshold may lower the safety budget, never raise it above the provider
  percentage.
- Enforce the budget before executing `TOOL_CALL_PRE` and again before the next
  model request. A no-shrink pause is advisory for ordinary background
  compaction only; it must not bypass a hard-cap boundary.
- At a boundary, await an in-progress round, apply its staged history, and
  retry forced compaction up to `hard_cap_compaction_attempts`. If provider
  summarization still fails, roll the old prefix into a deterministic recovery
  summary while preserving the system message, complete tool-call groups,
  recent turns, and at most 24 active-task excerpts of 4,000 characters each.
  If even that cannot fit, cancel the tool/request with a visible recovery
  action instead of sending an oversized prompt.
- A session turn budget is a visible/logged warning, not an automatic stop. Its
  turn count and start time persist with session stats, restore on resume, and
  reset when the session ID changes.
- Summarization model precedence is plugin `summarization_model`, shared
  `kollabor.llm.fast_model`, then the active session model. A configured
  `summarization_profile` supplies credentials; it does not silently replace
  that model precedence.
- Every compaction round logs its agent label, hub identity when available,
  selected summarization model, duration, round number, and outcome. The status
  widget shows the paused/cap state with current and budget token counts.

Context-window evidence must use the selected provider profile's runtime
metadata, not assume the public bundled catalog applies to OAuth. At the
2026-10-23T00:37Z verification, the OpenAI Codex OAuth `/models` response
reported 272,000 tokens for `gpt-6-luna`; the bundled public catalog reports a
larger window. A successful request log showed 326K input before a later
overflow; keep usage accounting and OAuth metadata separately labeled until
the runtime discrepancy is resolved. Use the lower provider metadata/percentage
ceiling as the safety budget and log observed usage.

## Failure Handling
For ordinary background compaction failures (timeout, invalid JSON, provider
error, invariant violation):
- increment the failure counter
- keep original history untouched
- continue normal conversation flow while it is below the hard prompt budget
- disable background compaction for the session after 3 consecutive failures

At a hard-cap boundary, the bounded forced retry and deterministic rollover
summary take precedence over the ordinary continue/disable behavior. If both
fail to produce a prompt under budget, cancel the next tool/request and display
an actionable recovery alert; do not send the oversized request.

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
- a paused 410K prompt over a 272K budget at 91 turns is compacted before
  `TOOL_CALL_PRE`
- failed forced compaction retries and then rolls over with a recovery summary
- 180 live tasks across 3 rounds remain bounded to 24 excerpts of 4,000
  characters
- the compacted task prompt estimate stays below 272K
- summarization-model precedence and per-agent model/duration log fields

Integration:
- long session with many file reads/edits
- compaction preserves active file set
- post-compaction task continuation quality
- tool boundary cancellation includes an actionable reason when recovery cannot fit

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
