# /goal — durable session goals with bounded continuation

Spec: [docs/specs/goal-command-harnesses.md](../specs/goal-command-harnesses.md)
(three adversarial review rounds included; round-3 gates are acceptance
criteria).

A goal is a user-owned objective plus a continuation, verification, budget,
and stop contract for one conversation. The daemon owns goal state and
continuation; TUI clients observe it. Exactly one unfinished goal may exist
per conversation (`conversation_uid` — durable across `/resume`, unlike the
rotating session id).

## Commands

```text
/goal [--budget N] <objective>  create one goal and schedule its first turn
/goal                           show the active goal and next action
/goal show                     show the active goal in full
/goal pause                    stop continuation at the next safe boundary
/goal resume [--budget N] [--turns N]  resume after a stop
/goal clear                    stop and preserve the goal as cleared history
/goal history [N]              list recent finished goals (default 10)
```

Control subcommands are daemon-side state operations: they work while a turn
is processing (routed ahead of the `is_processing` gate), and pause/clear
record a durable stop intent immediately, applied at the next safe boundary.
Attach clients execute `/goal` daemon-side through the `state.goal_command`
RPC — the daemon owns the conversation identity, store, and driver, and
`goal.state_changed` events stream back over the attach socket.
`--` is the end-of-options delimiter for objectives beginning with `--`.

## How it works

- **Loop**: each goal turn runs on the existing queue-processor path (the
  same machinery as Hub continuation — no second provider loop). After a
  turn settles, a guarded checklist decides whether one more round may
  dispatch: stop intents, typed outcome (`ok|cancelled|error` only `ok`
  continues), empty input queue, no pending work, provider health, budget
  admission, and default bounds.
- **Bounds** (independent of any token budget): 40 goal turns / 2h active
  time by default; per-request token accounting; budgets are dispatch gates
  with documented in-flight overshoot; resumed budgets are total ceilings.
  Provider errors pause the goal after one failed turn (the queue records
  `last_turn_error`; the driver maps it to a typed error outcome) — a goal
  never burns turns against a dead endpoint.
- **Evidence**: the runtime records every tool result of a goal turn.
  Completion requires `goal_report` citing refs the runtime itself
  recorded (provenance), produced after the last mutating tool call of the
  turn (freshness), committed only after the whole tool batch settles.
  Refs re-cited don't count (dedup). Objectives without a registered
  deterministic checker complete as **model-declared**, labeled as such
  everywhere.
- **Blockers**: same blocker 3× (or two blockers alternating over 6 turns)
  with no new evidence → `blocked`. New evidence resets the run.
- **Steering (§9.3)**: every goal turn injects one refreshed internal
  context item (`[goal context]` — objective, turn N/limit, budget, blocker
  state, standing policy). Never a synthetic user message.
- **Crash safety**: dispatch intent is committed (`goal_attempts` row)
  before provider submission; lease epochs fence stale owners; a crashed
  daemon's unsettled attempt recovers as **ambiguous** → goal pauses for
  reconciliation and is never automatically re-executed.
- **Attachments**: pasted images are promoted from the process-local
  ephemeral store into `goals/artifacts/<sha256>.<ext>` before creation is
  acknowledged; a missing/corrupt artifact at rehydration pauses the goal
  with `artifact_missing` instead of continuing without it.

## The goal_report tool

```xml
<goal_report kind="complete" version="12" reason="tests pass">
pytest: 12 passed :: full suite green after the fix
</goal_report>
```

One evidence `ref :: claim` per line. Registered through the unified tool
pipeline (`register_plugin_tag`/`register_plugin_handler`), so hooks and
permissions apply. Prose claiming "done" is never completion.

## Storage

`~/.kollab/projects/<encoded>/goals/goals.db` (SQLite, WAL) + `artifacts/`.
Tables: `goals`, `goal_events` (append-only), `goal_attempts`,
`goal_evidence`. A partial unique index enforces one unfinished goal per
conversation at the storage layer.

## Configuration

`kollabor.goals.enabled` (default `true`) gates the layer. Disabling leaves
`GoalStore` readable so finished/active goals stay inspectable.

## Status vocabulary

`active | paused | blocked | usage_limited | budget_limited | complete |
cleared` — each stop state renders a distinct next action; none auto-retry.

## Verification

- `tests/unit/test_goal_store.py` — store, concurrency, P0 crash harness
- `tests/unit/test_goal_service.py` — state machine, evidence, bounds
- `tests/unit/test_goal_driver.py` — steering/evidence/boundary wiring
- `tests/tmux/specs/goal-command.json` — live command surface
