---
title: "Provider-neutral /goal command and continuation contract"
status: revised-after-three-adversarial-reviews
owner: kollab
date: 2026-09-20
---

# Provider-neutral `/goal` command and continuation contract

status: revised for phase-0 implementation; three adversarial review rounds are
included below. Round 3's P0/P1 gates are acceptance criteria.

## decision summary

Kollab should add a durable, provider-neutral goal layer with these boundaries:

- A goal is a user-owned objective plus a continuation, verification, budget,
  and stop contract for one interactive chat session.
- A goal is keyed to a durable `conversation_uid` that survives `/resume` and
  session-ID rotation, not to a TUI process, provider session ID, rotating
  `current_session_id`, project-wide singleton, or agent name.
- There is one unfinished goal per conversation in phase 1. Creating another
  goal while one is unfinished is rejected; replacement requires an explicit
  `/goal clear`.
- The daemon owns goal state and continuation. TUI clients and attached clients
  observe it; they must not run duplicate continuations.
- `plugins/hub/task_ledger.py` remains the project-scoped child-task ledger.
  Goals may link to task IDs, but a task card is not a goal and a completed task
  is not proof that its parent goal is complete.
- Phase 1 uses typed runtime/provider control plus concrete evidence. It does
  not call a second paid evaluator model after every turn. An evaluator model is
  a separately budgeted phase-2 option.
- Structured user content is preserved end to end. Text projection is used only
  for command parsing, keyword matching, display, and redacted logging. Image
  bytes are stored as attachment/artifact references when needed, never
  stringified into objectives, logs, Hub broadcasts, or event payloads.
- Goal state is transactional SQLite (records, events, attempts) with a
  partial unique index enforcing one unfinished goal per conversation. JSON
  files are not used for goal state in phase 1; round 3 reproduced a
  lost-update race in the previously proposed shared-index layout.
- Crash recovery is conservative: an attempt that may have reached the
  provider pauses the goal for reconciliation and is never automatically
  re-executed. Only provably undispatched attempts may auto-retry.
- Every goal carries default automatic turn and time limits independent of
  any optional token budget. Budgets are enforced per provider request at
  dispatch time as a documented gate, not a mid-turn kill.
- Continuation decisions consume typed turn outcomes (origin, goal identity,
  cancellation/error status, per-request usage) rechecked under the turn
  lock; bare completion flags are not eligibility evidence.

The intended result is the conservative persistence and continuation behavior of
Codex combined with Claude's clear progress/reason presentation, without copying
either provider's private session format or adding an unbounded autonomous loop.

## 1. assignment, hypothesis, and method

### assignment

Recon how the current Codex `/goal` works, compare it with Claude Code's `/goal`,
map both against Kollab's live command, daemon, event-hook, multimodal, task,
and persistence seams, then produce an implementation-ready provider-neutral
contract and two hostile review passes.

### hypothesis

The useful abstraction is not a provider-specific slash command. It is a
durable, daemon-owned session contract with:

1. an objective that persists across turns;
2. explicit evidence required for completion;
3. safe-boundary continuation with user-input priority;
4. hard stops for pause, budget, quota, repeated blockers, errors, and no
   progress; and
5. an append-only audit trail that survives reconnects and restarts.

The main Kollab risk is creating a second, competing task system or allowing a
TUI/attached daemon race to create duplicate provider turns.

### method

Evidence was collected from:

- the current local Codex CLI and feature registry;
- the current official Codex goal implementation, continuation template, and
  guide;
- the current official Claude Code goal documentation;
- Kollab's indexed source graph, exact source snippets, current tests, and dirty
  worktree state.

Observed facts, inferences, and proposed behavior are labeled separately below.
No agent was spawned and no paid model call was made for this recon or review.

## 2. current evidence

### 2.1 Codex: observed current behavior

Local evidence on 2026-09-17:

- `codex --version` reported `codex-cli 0.155.0`.
- `codex features list` reported `goals stable true`.
- The installed native package contains the goal runtime, tool, steering,
  SQLite state, app-server, and TUI modules, including
  `ext/goal/src/tool.rs`, `ext/goal/src/steering.rs`,
  `state/src/runtime/goals.rs`, `app-server/src/request_processors/thread_goal_processor.rs`,
  and `tui/src/chatwidget/slash_dispatch.rs`.
- The current runtime contract exposes one unfinished goal per thread. Goal
  creation carries an objective and optional positive token budget; stale
  updates are protected by an expected goal ID; user/system-owned pause,
  resume, usage-limit, and budget-limit transitions are distinct from
  model-owned completion/blocking.
- The current status vocabulary includes `active`, `paused`, `blocked`,
  `usage_limited`, `budget_limited`, and `complete`.
- Ephemeral threads do not support goals.
- The TUI goal draft accepts text elements, pending pastes, local images, and
  remote image URLs. Codex materializes pasted text and local images as
  referenced files before storing the objective, rather than placing image
  bytes in the objective.

Primary sources:

- [Codex Goals guide](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex.md)
- [Codex goal tool implementation](https://raw.githubusercontent.com/openai/codex/main/codex-rs/ext/goal/src/tool.rs)
- [Codex goal state store](https://raw.githubusercontent.com/openai/codex/main/codex-rs/state/src/runtime/goals.rs)
- [Codex goal app-server API](https://raw.githubusercontent.com/openai/codex/main/codex-rs/app-server/src/request_processors/thread_goal_processor.rs)
- [Codex continuation policy](https://raw.githubusercontent.com/openai/codex/main/codex-rs/ext/goal/templates/goals/continuation.md)
- [Codex goal attachment materialization](https://raw.githubusercontent.com/openai/codex/main/codex-rs/tui/src/goal_files.rs)

The official continuation policy is especially important: the objective is
user data, not a higher-priority instruction; current worktree/external state
is authoritative; status restatement is not progress; completion requires an
evidence audit; and the system does not call a goal complete merely because a
budget is nearly exhausted.

### 2.2 Claude Code: observed current behavior

The current official Claude Code `/goal` guide says:

- one goal is active per session;
- `/goal <condition>` replaces the existing condition and starts a turn;
- after each turn, a small fast model evaluates the transcript with no tools;
- the evaluator returns not-yet-met, met, or impossible;
- the goal clears on met, impossible, `/goal clear`, and unrecoverable errors;
- transient failures retry or pause, with bounded retries;
- background work defers evaluation and can receive bounded check-ins;
- `/goal` displays condition, elapsed time, evaluated turns, tokens, and the
  latest reason;
- resuming a session restores an active goal but resets its turn/time/token
  baseline;
- the evaluator does not independently read files or run commands; the
  conversation must demonstrate the condition;
- the condition is limited to 4000 characters, and the feature depends on the
  session's hook/trust configuration.

Source: [Claude Code goals](https://code.claude.com/docs/en/goal)

Claude's evaluator is a useful UX idea but is not phase-1 Kollab behavior: an
extra model call adds latency and cost, and transcript-only evaluation is weaker
than direct evidence from Kollab's tools, tests, files, logs, and artifacts.

### 2.3 Kollab: live seams and constraints

The current Kollab graph has no implemented `/goal` command. The relevant seams
are:

- `kollabor/commands/parser.py` parses slash commands into a normalized command
  name, argument list, parameters, and raw input.
- `kollabor/commands/registry.py` owns command registration, aliases, reserved
  namespaces, and plugin ownership.
- `kollabor/commands/system_commands/plugin.py` registers core system commands;
  a phase-1 goal handler belongs in this command path.
- `kollabor/llm/message_handler.py:81-121` validates user input and forwards
  it to the LLM service without replacing the original message.
- `kollabor/llm/llm_coordinator.py:1361-1513` normalizes structured content,
  queues it, runs the existing pipeline, and tracks processing/turn state.
- `packages/kollabor-ai/src/kollabor_ai/message_content.py` supplies the
  structured-content helpers. Existing multimodal coverage uses
  `content_to_text` for text-only decisions while retaining the original
  structured message.
- `plugins/hub/presence.py:17-35` resolves the project-scoped Hub data directory
  through Kollab's config path policy. `kollabor/state/context_registry.py:52-62`
  already reuses that project-scoping seam for daemon state.
- `plugins/hub/task_ledger.py:194-763` persists one JSON task card per file,
  uses advisory locks plus atomic temp-file/fsync/replace writes, records
  checkpoints, and rejects late mutations to terminal cards.
- `plugins/hub/plugin.py:9293-9323` exposes the existing `/hub tasks` command
  surface; it is not a goal command and must remain independently usable.
- `tests/unit/test_multimodal_user_input_pipeline.py` exercises the real hook
  pipeline and attached-daemon submission for text-only, image-only, and mixed
  messages. Goal management must preserve the same structured-message rule.

These seams imply a small core goal service and store, not a new Hub task
implementation and not a provider-specific fork of the LLM pipeline.

## 3. comparison and design consequences

| concern | Codex | Claude Code | Kollab decision |
|---|---|---|---|
| ownership | persistent thread goal | session goal | durable conversation identity owned by daemon |
| active count | one unfinished goal per thread | one goal per session | one unfinished goal per conversation in phase 1 |
| completion authority | typed goal update plus evidence audit | transcript-only evaluator verdict | typed runtime/provider control plus concrete evidence |
| continuation | idle thread, no queued input, no pending work | after evaluator verdict; background deferral/check-ins | idle queue, no pending work, no queued user input |
| pause | user/system controlled | user/error controlled | user/system controlled; cooperative safe boundary |
| blocked/impossible | explicit blocked state | evaluator impossible clears goal | explicit blocked state after evidence-backed repeated blocker |
| budget | token/time accounting; budget-limited distinct | token display and evaluator billing | per-request usage accounting; default turn/time bounds; budget/quota stops distinct |
| objective injection | internal goal context | session goal hook/evaluator context | escaped internal context item, never a fake user message |
| attachments | materialized references | condition is text/transcript-based | attachment refs only; never stringify bytes |
| task/delegation | separate from goal | background agents separate | existing TaskLedger remains child-work primitive |
| restart | persistent thread state | active goal restored on resume | SQLite state restored with lease epochs; ambiguous dispatch pauses for reconciliation |
| evaluator cost | no separate evaluator required | small evaluator model billed | no paid evaluator in phase 1; optional later |

The direct design lesson is: use Codex's durable state and conservative
continuation, borrow Claude's compact status/reason UX, and avoid Claude's
per-turn evaluator until Kollab has an explicit cost and evidence policy.

## 4. scope, invariant, and non-goals

### objective

Allow a user to declare one durable objective in a Kollab session, let the
existing agent/tool pipeline make bounded progress across safe continuation
turns, inspect evidence-backed state, pause/resume/clear it, and reconnect to it
without duplicate execution.

### invariant

At every observable boundary, exactly one authoritative `GoalRecord` describes
the session's active goal, and every continuation is attributable to one
`goal_id`, one daemon owner, one monotonic turn sequence, one input queue
snapshot, and one evidence/result event.

### in scope for phase 1

- `/goal` command registration and raw-objective parsing;
- one active goal per durable conversation (`conversation_uid`);
- project-scoped durable storage and append-only redacted events;
- typed goal control (`progress`, `complete`, `blocked`) through the normal
  provider/tool boundary;
- conservative automatic continuation at safe queue boundaries;
- pause, resume, clear, budget, quota, error, and repeated-no-progress stops;
- daemon ownership, lease/CAS protection, reconnect visibility, and restart
  recovery;
- status output and attached-client notifications;
- links to existing Hub task IDs without changing TaskLedger's state machine;
- fake-provider tests with no network, no agent spawning, and no paid calls;
- structured text/image/mixed-input preservation and redaction checks.

### explicit non-goals

- no replacement of `TaskLedger` or `/hub tasks`;
- no project-wide goal singleton, cross-session adoption, or remote goal
  migration in phase 1;
- no child goals, parallel goal runners, or autonomous agent fan-out;
- no provider-specific session IDs as goal identity;
- no hidden permission escalation or `--dangerous` goal mode;
- no second paid evaluator model after each turn;
- no text scraping of assistant prose to mark a goal complete;
- no unbounded background retries or periodic check-in loop in phase 1;
- no stringification, logging, or broadcasting of image bytes;
- no claim that a written spec is a live implementation.

## 5. user-facing command contract

Phase 1 deliberately keeps the surface small and single-session. It does not
promise project-wide discovery before project-wide ownership semantics exist.

```text
/goal [--budget N] <objective>  create one goal and schedule its first turn
/goal                           show the active goal and next action
/goal show                     show the active goal in full
/goal pause                    stop continuation at the next safe boundary
/goal resume [--budget N]      resume after pause/block/quota/budget stop
/goal clear                    stop and preserve the goal as cleared history
/goal history [N]              list recent completed/cleared goals (default 10)
```

Rules:

- `/goal <objective>` uses the remainder after the command as the objective.
  Strip only outer whitespace; preserve internal whitespace, punctuation, and
  line breaks. The maximum is 4000 Unicode characters in phase 1.
- An empty or image-only objective is rejected with an actionable error. A
  mixed text/image input may create a text objective plus attachment references.
- Unknown options are rejected. The handler must not guess whether an objective
  beginning with `--` is a flag. Support `--` as the explicit end-of-options
  delimiter for an objective that begins with `--`.
- If an unfinished goal already exists, creation fails with the existing goal's
  short ID and the next actions `/goal show`, `/goal clear`, or `/goal resume`.
  It does not silently replace the goal.
- `/goal clear` is a user stop, not deletion. It appends a terminal `cleared`
  event and removes the conversation's active goal while retaining a bounded
  summary and event history.
- Goal control subcommands (`show`, `pause`, `resume`, `clear`, `history`) are
  daemon-side state operations, not LLM turns. They must remain available
  while a turn is processing: the input path evaluates them before any
  `is_processing` gate (`kollabor/state/local.py:2236` currently rejects input
  before command parsing) and must not exclude image-bearing messages from
  command detection (`local.py:2275`). `pause` and `clear` durably record a
  stop intent immediately at request time; the running turn applies it at its
  next safe boundary.
- Phase-2 commands such as `/goal list`, `/goal cancel <id>`, and `/goal adopt
  <id>` require an explicit project/user scope and lease protocol. They are not
  aliases invented in phase 1.

The handler must parse the text projection of a structured message, never pass a
list of text/image parts to `shlex` or call string methods on it. The original
structured input is retained until the existing message-normalization boundary
decides whether attachment references are needed.

## 6. domain model

### 6.1 GoalRecord

The durable record contains at least:

```text
goal_id                 opaque UUID; stable across restart/attach
conversation_uid        durable conversation identity; survives /resume and
                        session-ID rotation (see 6.5)
session_id              rotating current session id; diagnostic only, never a key
project_root            normalized project root captured at creation
objective               bounded text; never image bytes
status                  active | paused | blocked | usage_limited |
                        budget_limited | complete | cleared
created_at, updated_at  wall-clock timestamps
turn_count              goal-owned provider turns attempted
continuation_seq        monotonic continuation counter
tokens_used            nullable trusted provider total; never fake zero
token_budget            nullable positive limit
time_used_seconds       monotonic elapsed total projected to wall display
last_reason             bounded user-visible reason
blocked_reason          canonical blocker key plus detail, when applicable
no_progress_count       consecutive same-blocker/no-progress count
last_evidence           bounded redacted evidence references
attachment_refs         artifact IDs/media types/checksums; no byte payloads
task_ids                optional linked TaskLedger IDs
scope_snapshot          cwd, allowed roots, model/profile, permissions, trust
owner_daemon_id         current continuation owner, if claimed
owner_lease_expires_at  lease expiry for crash recovery
lease_epoch             fencing counter, incremented on lease recovery; all
                        attempt and control writes must match it
record_version          CAS/version guard for stale updates
execution_generation    bumped on pause/resume/clear/scope change; attempts and
                        controls carrying a stale generation are rejected even
                        when record_version matches
auto_turn_limit         default 40 goal turns; independent of token budget
auto_time_limit_s       default 7200s accumulated active time, monotonic
completion_declared     true when no deterministic checker applied (see 8.5)
```

The model may add `provider_session_refs` and `last_turn_id`, but those are
references and diagnostics, never identity keys. Secrets, credentials, raw
prompts containing secrets, and image bytes are excluded or redacted.

### 6.2 GoalEvent

Every state change appends a compact event with:

```text
event_id, goal_id, session_id, sequence, timestamp
kind, actor, daemon_id, turn_id, reason
status_before, status_after
usage_delta, evidence_refs, task_ids, redaction_version
```

Required kinds include:

```text
created, claimed, turn_started, progress, turn_completed,
paused, resumed, blocked, usage_limited, budget_limited,
complete, cleared, lease_recovered, error,
pause_requested, clear_requested, reconcile_paused, generation_bumped,
artifact_missing
```

Events are audit metadata, not a transcript. Large tool output and media are
artifact references with media type, size, checksum, and retention scope.

### 6.3 Goal versus TaskCard

`GoalRecord` answers: “What durable outcome is this session pursuing, and may it
continue?”

`TaskCard` answers: “What assigned unit of work exists in the project ledger?”

The goal service may link a valid `TaskCard.id` and show its current status, but:

- TaskLedger remains the only writer for task-card state.
- A TaskCard may outlive, predate, or be unrelated to a goal.
- A goal cannot become complete while explicitly linked child tasks are still
  nonterminal, unless the goal's completion evidence explicitly records why the
  task is no longer part of the acceptance boundary.
- `/hub tasks` must not start, pause, or clear a goal implicitly.

### 6.4 GoalAttempt

Every dispatch is a durable attempt row committed before any provider
submission:

```text
attempt_id              opaque UUID
goal_id, conversation_uid, continuation_seq
lease_epoch             owner fence at dispatch time
execution_generation    generation at dispatch time
state                   pending | dispatched | acked | settled | cancelled | ambiguous
provider_request_ref    provider-side request id when known
turn_id, outcome        set at settle time
usage_deltas            per-provider-request usage records
```

`record_version` CAS covers every write. `lease_epoch` and
`execution_generation` exist so lease renewal and accounting writes do not
collide with execution invalidation: a recovered owner or a resumed
generation rejects the old owner's late attempt writes and controls even
when the record version would have matched.

### 6.5 durable conversation identity (prerequisite)

`current_session_id` rotates on resume by design — `resume_conversation()`
mints a fresh ID so resumed history appends to a new `.jsonl`
(`kollabor/state/local.py:2453`). It can never be a goal key. Phase 0 adds a
`conversation_uid`:

- a UUID generated once per conversation, persisted in conversation save
  metadata, and propagated by `resume_conversation()` to the rotated session;
- exposed by StateService; `GoalRecord` keys on it;
- legacy conversations without one mint and persist it on first load (no
  goals predate the feature).

Behavior:

- reconnect/reattach: same uid, same goal; the lease decides continuation
  ownership;
- `/resume` into the same conversation: the uid carries; the goal survives;
- context switch to another conversation: the goal remains owned by the
  previous uid; status shows "active in another conversation"; control
  returns when the user switches back; no transfer in phase 1;
- new conversation / session reset: no active goal;
- the same conversation open in two daemons: creation resolves via the
  unique index; the lease loser observes read-only.

## 7. persistence, ownership, and concurrency

### 7.1 storage location and format

Implement `GoalStore` under `kollabor/state/goal_store.py`, SQLite-backed.
Use the existing project-scoped Kollab data-path policy used by `get_hub_dir()`
and `context_registry` rather than inventing a second config root:

```text
<project-scoped-kollab-data>/goals/
  goals.db                   SQLite (WAL): goals, goal_events, goal_attempts
  artifacts/<sha256>.<ext>   durable goal attachment bytes (see 9.2)
```

Schema essentials:

```sql
CREATE TABLE goals ( /* GoalRecord columns */ ,
  record_version INTEGER NOT NULL,
  lease_epoch INTEGER NOT NULL DEFAULT 0 );
CREATE UNIQUE INDEX one_unfinished_goal_per_conversation
  ON goals(conversation_uid)
  WHERE status NOT IN ('complete', 'cleared');
CREATE TABLE goal_events ( /* event columns; insert-only */ );
CREATE TABLE goal_attempts ( /* attempt columns; see 6.4 */ );
```

Rationale: round 3 reproduced a lost-update race in the previously proposed
layout — one shared `index.json` guarded by per-session advisory locks let two
concurrent writers each publish a pointer, leaving one persisted goal. A
single SQLite transaction is the atomic commit boundary for record, event,
attempt, and pointer writes; the partial unique index enforces one unfinished
goal per conversation at the storage layer, not by lock discipline.

Requirements: WAL mode, `busy_timeout`, one connection per daemon,
`BEGIN IMMEDIATE` for writes, restrictive directory permissions. Corruption
recovery must never silently invent a goal; on startup, reconcile unfinished
attempts per 7.4 and emit recovery events rather than deleting history.
SQLite is not permission to add a second unbounded transcript store; large
outputs remain artifact references.

The implementation should extract a shared project-data path helper if the
current import direction would otherwise make core state depend on
`HubPlugin`. It must not duplicate the Hub task state machine.

### 7.2 owner lease

The daemon that owns the session claims the goal with `(goal_id,
record_version, daemon_id, lease_expiry)`. Only the current owner may enqueue a
continuation. A claim or update fails safely when:

- the goal ID is stale;
- the record version changed;
- another live daemon holds an unexpired lease; or
- the session/project scope does not match.

After a daemon crash, a new daemon may recover an expired lease at an explicit
safe boundary, append `lease_recovered`, and continue at most once for the next
continuation sequence. An attached TUI observes state; it does not claim a
second runner merely because it rendered the goal.

Recovery increments `lease_epoch`. All attempt and control writes carry the
epoch they were issued under, so a previous owner's late writes fail the fence
even when `record_version` would have matched. Lease expiry alone never
implies the crashed owner's dispatch did or did not execute; see 7.4.

### 7.3 creation transaction

`create()` is one `BEGIN IMMEDIATE` transaction:

1. insert the GoalRecord (the partial unique index rejects a second
   unfinished goal for the conversation);
2. insert the `created` event; and
3. commit.

Either the whole goal exists or none of it does; there is no pointer/file
crash window to reconcile. The acceptance test starts two concurrent
creations and requires exactly one success and one unique-constraint
rejection.

### 7.4 dispatch protocol and crash recovery

The lease/CAS contract cannot establish whether an external request executed.
Dispatch therefore uses durable attempt states:

1. **Intent first.** Before provider submission, commit an attempt row
   (`state=dispatched`, with `goal_id`, `continuation_seq`, `lease_epoch`,
   `execution_generation`) in its own transaction.
2. **Ack.** On provider acceptance, flip to `acked` and store
   `provider_request_ref` when the provider returns one.
3. **Settle.** When the turn settles, record the typed outcome and
   per-request usage, and mark `settled`.
4. **Recover.** On lease recovery, inspect unfinished attempts:
   - `pending` (never submitted): mark `cancelled`; automatic retry is
     allowed for this class only.
   - `dispatched` or `acked` without settle: mark `ambiguous`, transition the
     goal to `paused` with reason `reconcile`, and emit `reconcile_paused`.
     Ambiguous attempts are never automatically re-executed. `/goal resume`
     continues from the next continuation sequence and keeps the ambiguous
     attempt as history; re-running the same work is a human decision.

P0 acceptance test: a fake provider accepts the request, the daemon is killed
before ack/settle, and recovery must pause with a reconcile reason and a
provider request count of exactly one — no re-dispatch.

## 8. lifecycle and execution semantics

### 8.1 state machine

```text
                 +--------------------+
                 |                    v
create -> active -> paused --------> active
              |   |   |                 |
              |   |   +---------------> |
              |   +--> blocked --------+|
              |   +--> usage_limited --+|
              |   +--> budget_limited -+|
              +-----------------------> complete
              +-----------------------> cleared
```

More precisely:

- `active -> paused` is user/system/error controlled.
- `paused`, `blocked`, `usage_limited`, and `budget_limited` each transition to
  `active` only through `/goal resume`, with scope/lease revalidation and any
  required blocker, quota, or budget condition resolved.
- `active -> blocked` requires a typed blocker reason and the repeated-blocker
  threshold below, or a validated unrecoverable blocker.
- `active -> usage_limited` means provider/account/runtime quota or usage
  cannot safely continue; it is not completion.
- `active -> budget_limited` means the goal's explicit token/time budget was
  reached; it is not completion.
- `active -> complete` requires a typed completion update and evidence audit.
- Any unfinished state may become `cleared` through `/goal clear`; clear is
  terminal and retains history.
- `complete`, `cleared`, and a recovered terminal state are immutable except for
  audit metadata.

### 8.2 creating and starting

The slash command records the goal and returns a visible acknowledgement. It
then schedules a goal-owned start turn through the existing StateService/
LLMService queue. The command handler must not call a provider synchronously or
bypass hooks, permissions, approvals, tool execution, or attached-daemon
routing.

The objective is injected as an escaped internal context item with source
`goal`, clearly marked as user data. It is not rewritten as a synthetic user
message, and the original `/goal` command is not sent as an ordinary provider
turn. That first injection and every continuation's refreshed steering are
defined in 9.3.

### 8.3 safe-boundary continuation

The queue processor emits one typed outcome per scheduling boundary:

```text
TurnOutcome {
  turn_id
  origin: user | goal | command | hub
  goal_id: present for goal-owned turns
  status: ok | cancelled | error
  error: bounded detail when status=error
  request_usage: per-provider-request usage records
}
```

Bare `turn_completed` flags are not eligibility evidence: they also fire after
cancellation and exceptions (`queue_processor.py:1500`) and for slash-command
emissions (`kollabor/state/local.py:2374`). Only `origin=goal, status=ok`
outcomes drive continuation. `cancelled` maps to a pause with reason
`cancelled`; `error` follows the error policy in 8.6.

After a goal-owned `ok` outcome, the daemon may enqueue one continuation only
when all are true:

- the GoalRecord is still `active` and the goal ID/version/lease/epoch match;
- the current turn has fully completed;
- no tool call, background job, or approval is pending;
- the input queue has no user message waiting;
- no newer session command has invalidated the continuation;
- the provider/runtime is healthy and within quota/budget; and
- no other goal continuation is in flight for the conversation.

The check and claim are one guarded operation, re-evaluated after acquiring
the existing turn lock — not at outcome-emission time — so a user message or
command arriving during processing wins before dispatch.

Settle-driven wake: when a pending tool batch, approval, or background job
tied to a goal-owned turn settles, the daemon runs the same guarded
eligibility check. An active goal must never sleep indefinitely waiting for a
periodic tick.

The Hub continuation path (`kollabor/llm/message_handler.py`) routes through
the same eligibility check as ordinary queue processing; it may not bypass it.

A user message arriving before dispatch wins over the continuation: cancel the
pending continuation, leave the goal active, and route the user message
through the normal hook pipeline. The next safe boundary may schedule another
goal turn.

Every continuation carries `goal_id`, `record_version`, `continuation_seq`,
`conversation_uid`, `lease_epoch`, `execution_generation`, and `turn_id`. A
stale completion cannot update a newer goal.

### 8.4 progress and no-progress stop

Progress is one of:

- a relevant tool result or artifact/state change;
- a typed `progress` control with concrete evidence reference; or
- a verified external wait result tied to a live handle.

Assistant prose, a repeated plan, a status restatement, or a failed observation
without a state change is not progress. The runtime canonicalizes the blocker
key. Evidence is deduplicated: an evidence reference already recorded for the
goal never counts as new evidence, so repeated successful reads of the same
artifact cannot present as progress. Three consecutive goal turns with the
same canonical blocker and no new evidence, or six consecutive goal turns
cycling between at most two canonical blockers with no new evidence,
transition to `blocked`, append the reason, and stop automatic continuation.
The user can resolve the blocker and explicitly `/goal resume`.

Independent of blockers and budgets, `auto_turn_limit` (default 40) and
`auto_time_limit_s` (default 7200) stop the goal as `budget_limited` with
reason `auto_turn_limit`/`auto_time_limit` when reached. These defaults bound
every goal even when no token budget is set.

### 8.5 completion authority

Only a typed `GoalControl.complete` from the provider/runtime boundary or an
explicit future human completion command may complete a goal. The control must
contain:

- the expected `goal_id` and record version;
- a concise summary;
- one or more evidence references;
- the acceptance claims each reference supports; and
- any linked TaskLedger IDs and their terminal status.

Evidence provenance and acceptance verification are separate checks:

- **Provenance (deterministic, phase 1).** Every evidence reference must be
  produced by a tool execution inside a goal-owned attempt. The runtime
  records provenance (`attempt_id`, tool-result id) itself; the model's claim
  alone never establishes it.
- **Freshness (deterministic, phase 1).** Evidence produced before the last
  file-mutating tool call of its turn is stale: the runtime compares its own
  recorded tool-call order and rejects it. Artifact checksums are verified
  against the goal artifact store at completion time.
- **Verification (declared, phase 1; checkers, phase 2).** Phase 1 defines no
  general checker proving that a test output establishes the objective. When
  the objective admits no registered deterministic check, completion is
  written with `completion_declared=true`, displayed as
  `complete (model-declared)`, and recorded as such in the event. It is never
  presented as verified.

The runtime rejects completion when the evidence is empty, inaccessible,
stale/indirect, inconsistent with the current scope, not attributable to a
goal-owned attempt, or attached linked tasks remain nonterminal. Completion
controls arriving mid-batch are held and committed only after the entire tool
batch settles; the model cannot complete a goal while a tool that could
invalidate the evidence is still running.

No completion may be inferred from the words “done”, a near-exhausted budget,
absence of an obvious error, or a provider's final prose alone.

### 8.6 budget, quota, and errors

- `token_budget` is optional and positive. Usage is accounted per provider
  request, not per turn: a goal turn may issue several provider requests (the
  `queue_processor.py` tool loop re-prompts after every tool batch), each
  contributing its own usage delta.
- The budget is a dispatch gate, not a mid-turn kill: before each dispatch,
  if `tokens_used >= token_budget`, transition to `budget_limited` instead of
  dispatching. Documented overshoot is therefore bounded by the requests of
  the turn already in flight when the limit was crossed.
- Budgets are total ceilings across pause/resume. `/goal resume --budget N`
  replaces the ceiling and is rejected when `N <= tokens_used`; resume
  without `--budget` keeps the remaining original ceiling.
- `tokens_used` is nullable when a provider does not report usage. The service
  must not display an invented zero. A configured hard budget with unavailable
  usage pauses safely as `usage_limited` rather than over-running silently.
- `time_used_seconds` is measured with a monotonic clock and displayed from
  persisted totals; wall timestamps are for audit only.
- Existing Kollab provider/account limits map to `usage_limited` with a concrete
  recovery action. The goal remains inspectable and is not silently deleted.
- Retryable transport errors pause the goal at a safe boundary under the
  existing retry policy. Permanent scope/auth/model errors pause or block with
  an explicit reason; they never trigger an unbounded retry loop.
- `/goal resume --budget N` may add/replace a budget only after recording the
  user action and validating the new positive limit. Resume never silently
  clears a budget or scope restriction.

## 9. provider boundary and structured content

### 9.1 typed GoalControl

Provider adapters expose a small provider-neutral control envelope to the goal
service:

```text
GoalControl {
  goal_id: str
  expected_record_version: int
  kind: progress | complete | blocked
  reason: str
  evidence: [{kind, ref, claim}]
}
```

There is exactly one reporting surface: a `goal_report` tool registered
through the existing unified tool pipeline (`register_plugin_tag` +
`register_plugin_handler` returning `ToolExecutionResult` for XML-tool
providers; the equivalent native tool registration for native-tool
providers). Both paths normalize to the same `GoalControl`; no per-adapter
goal protocols. Because it runs through the real tool pipeline, hooks,
permissions, and approvals apply to goal reports like any other tool.
Text scraping is forbidden. The runtime validates goal ID, version,
generation, epoch, scope, evidence provenance, permissions, and linked tasks
before mutating state.

If a provider profile cannot produce a typed control, Kollab must fail goal
creation with a clear capability message or run in an explicitly non-running
manual-inspection mode. It must not pretend that arbitrary prose is completion
evidence.

### 9.2 text, image, and mixed inputs

Goal command recognition follows the same structured-content invariant already
used by the multimodal input pipeline:

- project `MessageContent` to text for command detection and objective text;
- classify an input as image-only when it has no user-authored text segments;
  synthetic placeholders such as `[image1]` do not satisfy the objective;
- keep the original structured parts available to the existing normalization/
  image-store boundary;
- persist only safe attachment/artifact references in `attachment_refs`;
- pass references, not raw bytes, into goal context and events;
- use `content_to_text` for status/search/reason operations and `prepend_text`
  only when adding text around a structured message;
- never call `.strip()`, `.lower()`, or formatting on the raw parts list;
- never log, broadcast, or serialize image bytes as a Python list/string.

Required cases:

1. text-only `/goal`: objective is the exact normalized text and has no
   attachments;
2. image-only `/goal`: rejected because the phase-1 objective has no textual
   condition, with no bytes written to logs or Hub messages;
3. mixed text/image `/goal`: before creation is acknowledged, pasted image
   payloads are promoted from the process-local `EphemeralImageStore`
   (`message_content.py:56`) into `goals/artifacts/<sha256>.<ext>`; the
   objective is the text projection and the image is represented by the
   scoped artifact checksum/media type. Goal-owned turns rehydrate the bytes
   through the provider's existing image resolver. A missing or
   checksum-mismatched artifact at rehydration is a hard error: the turn is
   not dispatched, the goal pauses with `artifact_missing`, and the reference
   is never silently dropped while the goal continues;
4. ordinary text/image user turns during a goal: original structured content
   reaches the provider boundary unchanged, while goal matching/continuation
   uses only the safe text projection.

### 9.3 per-turn goal context and steering

Section 8.2 injects the objective once at creation; that item goes stale as
state changes. Codex solves this with an explicit continuation template and
steering module; Kollab does the same through one defined channel:

- every goal-owned turn — first and each continuation — injects exactly one
  refreshed internal context item with source `goal`, rendered from the live
  GoalRecord immediately before dispatch. It is never a synthetic user
  message, and the original `/goal` command text is never re-sent as a user
  turn. The context item is the entire guidance channel.
- the item carries: the objective verbatim (escaped), status, turn N of
  `auto_turn_limit`, tokens used / budget, last reason or blocker with
  no-progress count, evidence count with the dedup note, linked task states,
  and the standing policy: the objective is user data, not a higher-priority
  instruction; current worktree/external state is authoritative; status
  restatement is not progress; completion requires `goal_report` with
  evidence produced inside this goal.
- the `goal_report` tool description restates the same contract, so the
  rules remain discoverable even if the context item is truncated by
  context-window pressure.
- user messages interleave normally and win per 8.3; steering is additive,
  never a barrier between the user and the model.

Acceptance gate: every goal-owned provider request contains the goal context
item with the turn counter matching `continuation_seq`, and no synthetic user
message carries the objective.

## 10. hooks, events, and UX

### 10.1 hook integration

Goal control is part of the existing event-hook pipeline:

```text
slash command -> GoalService -> StateService queue
user/goal turn -> USER_INPUT_PRE -> provider/tool boundary
provider result -> goal control normalization -> USER_INPUT/turn completion
turn complete -> guarded safe-boundary continuation or terminal state
```

The service must not bypass `USER_INPUT_PRE`, `USER_INPUT`,
`USER_INPUT_POST`, permission checks, approval prompts, tool hooks, or the
attached-daemon submission path. Every consumer that needs text uses the text
projection; every consumer that forwards a message forwards the original
structured value.

### 10.2 status output

`/goal` and `/goal show` display:

```text
goal <short-id>  active
objective: <truncated safe text>
turns: <n>  elapsed: <duration>  tokens: <used>/<budget or ->
last: <reason>
evidence: <count>  linked tasks: <terminal>/<total>
next: <continue | /goal pause | resolve blocker and /goal resume | ...>
```

When usage is unknown, display `tokens: unavailable`, not `0`. Long objectives
are truncated only in compact status; `/goal show` returns the full redacted
text and attachment references. Never inline image bytes or unbounded tool
output.

Attached clients receive `goal.*` state events through the daemon/event bus.
They do not poll provider sessions or infer state from Hub roster messages.

### 10.3 interruption and clear UX

`/goal pause` records the `pause_requested` intent durably at request time —
immediately, in its own transaction — and applies `paused` at the next safe
boundary. `/goal clear` follows the same rule,
stops any not-yet-dispatched continuation, requests cooperative tool
cancellation where supported, and retains the audit trail. Ctrl-C may request
the same pause/clear behavior but must not erase state.

## 11. implementation seams and order

The implementation should follow these seams, in order:

1. Durable conversation identity: persist `conversation_uid` in conversation
   save metadata, propagate it through `resume_conversation()`'s session-ID
   rotation (`kollabor/state/local.py:2453`), expose it on StateService.
2. `kollabor/state/goal_store.py`: SQLite schema, migrations, lease/epoch/CAS,
   attempt states, recovery, redaction, and isolated test store.
3. `kollabor/goals/service.py`: state transitions, evidence provenance/
   freshness checks, budget/blocker policy, task links, and guarded
   continuation decisions. GoalService owns policy and state only; execution
   stays on the existing QueueProcessor path — there is no second
   provider-calling loop.
4. `kollabor/commands/system_commands/handlers/goal.py` plus registry wiring:
   raw remainder parsing, command errors, status rendering, and daemon-side
   control operations available during processing.
5. Existing StateService/LLMService/QueueProcessor integration: goal context,
   typed TurnOutcome emission, queue-idle guard under the turn lock,
   user-input priority, settle-driven wake, and attached-daemon routing.
6. `goal_report` tool registration through the unified tool pipeline and
   `GoalControl` normalization.
7. TUI/status event rendering only after the daemon behavior is proven.

Do not put goal state in `plugins/hub/plugin.py`, duplicate TaskLedger's task
methods, or add a second path that calls providers outside the current queue.

### file manifest

New files:

```text
kollabor/state/goal_store.py                     SQLite GoalStore: schema,
                                                 events, attempts, lease/
                                                 epoch/CAS, recovery
kollabor/goals/service.py                        GoalService: transitions,
                                                 evidence provenance/
                                                 freshness, budget/blocker
                                                 policy, guarded continuation,
                                                 goal_report handler
                                                 (vault_write pattern)
kollabor/commands/system_commands/handlers/
  goal.py                                        /goal command handler,
                                                 daemon-side control ops,
                                                 status/history rendering
tests/unit/test_goal_store.py                    store, concurrency, crash
                                                 recovery (P0 harness)
tests/unit/test_goal_service.py                  state machine, evidence,
                                                 budget/blocker policy
tests/tmux/specs/goal-command.json               live command-surface spec
```

Modified files:

```text
kollabor/state/local.py                          conversation_uid propagation
                                                 through resume_conversation;
                                                 goal-control commands ahead
                                                 of the is_processing gate;
                                                 image-bearing command parsing
kollabor_ai conversation logger / save metadata  persist conversation_uid
kollabor_agent/queue_processor.py                typed TurnOutcome emission
                                                 at the scheduling boundary
kollabor/llm/message_handler.py                  hub continuation path runs
                                                 the same eligibility check
kollabor/llm/llm_coordinator.py                  goal context injection
                                                 (9.3), queue-idle guard
                                                 under the turn lock,
                                                 settle-driven wake
```

Data (not code): `<project-scoped-kollab-data>/goals/goals.db` and
`goals/artifacts/`. Docs: a `docs/features/` page ships with phase 1, not
phase 0.

## 12. acceptance and verification plan

All phase-1 checks use an isolated temporary Kollab config/project/data root, a
fake provider, deterministic clock/usage fixtures, and network-disabled test
configuration. They must not spawn agents or make paid model calls.

### command and state tests

- parse exact text objectives, multiline objectives, empty input, 4000-character
  boundary, and unknown options;
- reject a second unfinished goal atomically and preserve the first;
- verify every valid and invalid state transition;
- verify clear retains history and removes only the active pointer;
- verify pause/resume requires scope and lease revalidation;
- verify budget extension is explicit and audited;
- verify stale goal ID/version updates are rejected;
- verify three identical blockers stop at `blocked` and a new evidence-bearing
  resume resets the counter;
- verify terminal records cannot be reopened by late provider results;
- verify malformed/truncated storage is recovered without inventing state;
- run concurrent create/claim/update writers and require one authoritative
  active goal and monotonic event sequence.

### pipeline and runtime tests

- drive the actual slash-command/event-hook pipeline, not only service methods;
- start a goal through an attached daemon, disconnect the TUI, reconnect, and
  confirm the same goal ID/status/events are visible;
- force a daemon crash/expired lease and confirm exactly one recovery
  continuation;
- queue user input during the continuation race and confirm user input wins;
- leave a tool/approval/background job pending and confirm no continuation is
  dispatched;
- verify a goal-owned turn carries goal metadata through `turn_complete` and
  cannot recursively enqueue duplicate continuations;
- test fake provider controls for progress, complete, blocked, malformed
  evidence, stale version, and unsupported capability;
- verify normal provider permissions/hooks/approvals still run for goal turns;
- verify Hub task links do not mutate TaskLedger state and nonterminal linked
  tasks block completion.

### round-3 gates (P0/P1 acceptance criteria)

- crash after provider acceptance but before local ack/settle: recovery marks
  the attempt ambiguous, pauses with a reconcile reason, and the fake
  provider's request count stays at one;
- two concurrent goal creations against the real store: exactly one commits,
  the loser receives a unique-constraint rejection;
- `/resume` into another conversation while a goal is active, then back: the
  goal stays owned by its `conversation_uid`, status names the owning
  conversation while switched away, and control returns on switch-back;
- `/goal pause` and `/goal clear` submitted through the real RPC path while a
  turn is processing: the intent is durably recorded immediately, applied at
  the next boundary, and no continuation dispatches afterwards;
- alternating blockers (A/B/A/B over six turns) and default turn/time limits
  each stop the goal with no token budget set;
- a multi-provider-request turn accounts per-request usage; a hard budget at
  the boundary stops the next dispatch instead of a mid-turn kill;
- a pasted-image goal: the artifact is promoted before the creation ack,
  resolves in a fresh process, and a deleted artifact pauses with
  `artifact_missing` rather than silently continuing;
- a cancelled goal turn emits a `cancelled` outcome, produces no
  continuation, and leaves the goal paused rather than restarted;
- a checker-less objective completes with the `model-declared` label in
  status and events;
- a completion control carrying a pre-pause `execution_generation` is
  rejected after resume;
- a pending background job settling with no user input triggers the
  eligibility check within one event-loop turn;
- every goal-owned provider request carries the refreshed `goal` context item
  (9.3) with the turn counter matching `continuation_seq`, and no synthetic
  user message carries the objective.

### multimodal and redaction tests

- text-only, image-only, and mixed `/goal` inputs;
- ordinary text/image turns while a goal is active;
- assert image payload bytes are absent from GoalRecord, GoalEvent, logs, Hub
  broadcasts, status output, and exceptions;
- assert attachment IDs, media types, sizes, and checksums survive restart;
- assert `content_to_text` is used for text-only decisions while the structured
  message reaches the provider boundary unchanged;
- run the existing multimodal regression suite with goal hooks enabled.

### observable proof required before implementation is called complete

The handoff must include:

- exact test command and pass output;
- isolated data directory and event files inspected;
- one attached-daemon reconnect trace with goal ID and continuation sequence;
- provider-boundary assertion for structured text/image/mixed messages;
- no-network/no-paid-call assertion;
- `git diff --check`, targeted lint/type checks, and the preserved dirty-tree
  report.

## 13. phased delivery

### phase 0: harness and store proof

Build only the durable conversation identity plumbing, the SQLite GoalStore
(records/events/attempts, lease epochs, generations), the service state
machine, fake-provider control, the dispatch/recovery harness with the P0
crash test, and the command parser contract. No automatic real provider
continuation.

### phase 1: local durable goal

Wire `/goal`, daemon ownership, safe-boundary continuation, typed controls,
usage/budget stops, attached-client events, and the full acceptance matrix.

### phase 2: evaluator and artifacts, only if justified

Optionally add a separately configured evaluator model or deterministic
evidence checker. It must declare provider, cost, token/time budget, transcript
inputs, no-tool policy, and failure/stop behavior. Add richer artifact browsing
and bounded background check-ins only with evidence that phase 1 needs them.

### phase 3: project goals and delegation

Only after explicit scope/lease semantics are designed: `/goal list`, goal
adoption, child goals, parallel runners, remote sessions, parent aggregation,
and cross-project authorization.

## 14. rollback and operational safety

- Gate the feature behind a disabled-by-default/configurable feature flag until
  the isolated and attached-daemon suites pass.
- If continuation misbehaves, disable scheduling while keeping GoalStore read-
  only so users can inspect and clear goals.
- If storage migration fails, preserve the original files and refuse to start a
  second store; never reset or delete user goal history automatically.
- Rollback must not modify TaskLedger files, conversation history, image stores,
  or unrelated dirty work.
- A completed or cleared goal remains inspectable even when the feature flag is
  disabled.

## 15. adversarial review round 1 — product and contract attack

This pass attacked the first draft for ambiguous ownership, scope, authority,
UX, and overlap with existing Kollab systems. Findings were written before the
current contract was revised.

### R1-1 — P1 — duplicate source of truth

- who: product owner and Hub/task operator;
- what: the first draft described goals and TaskCards as parallel work systems;
- where: old lifecycle, delegation, and phase-1 command sections;
- why: implementers could mark a TaskCard done and infer goal completion, or
  create two schedulers with different retry/terminal semantics;
- example: `/hub tasks assign` completes while `/goal` keeps running, and the
  two status lines disagree;
- required change: define GoalRecord as the session continuation contract,
  TaskCard as project child work, and make links explicit and one-way;
- acceptance test: a linked nonterminal TaskCard rejects goal completion while
  `/hub tasks` remains independently functional.

disposition: fixed in sections 3, 4, 6.3, and 12. The goal service does not
write TaskCard state or infer completion from an unlinked task.

### R1-2 — P1 — session, project, and daemon scope were underspecified

- who: reconnecting TUI user and multi-daemon operator;
- what: “project/user data store” did not say which process owns continuation;
- where: old persistence and adoption language;
- why: two attached clients could both call the provider, or a project-wide
  goal could leak across sessions;
- example: a second CLI opens the same project and resumes the first session's
  goal without consent;
- required change: use a stable session ID, daemon owner lease, project-root
  snapshot, one active goal per session, and defer adoption;
- acceptance test: disconnect/reconnect and concurrent attach produce one
  continuation sequence and one provider turn.

disposition: fixed in sections 4, 7, 8, and 12.

### R1-3 — P1 — completion authority was too vague and could become expensive

- who: user paying for provider calls;
- what: the first draft permitted a provider-neutral “success” path without
  defining evidence or whether a second evaluator model was required;
- where: execution and acceptance sections;
- why: a hidden evaluator could double spend and transcript prose could be
  mistaken for proof;
- example: the assistant says “all tests pass” and the goal auto-completes even
  though no test output or file exists;
- required change: typed completion control, concrete evidence refs, final audit,
  no phase-1 paid evaluator, and explicit budget/quota stops;
- acceptance test: prose-only completion is rejected; fake-provider completion
  with valid evidence succeeds without a second model call.

disposition: fixed in sections 2, 8.5, 9.1, and 12.

### R1-4 — P1 — command surface overpromised global operations

- who: user following documented help;
- what: `/goal list`, `/goal show <id>`, `/goal cancel <id>`, and `/goal adopt`
  were listed without an identity, scope, or lease contract;
- where: old syntax and phase-1 plan;
- why: a command could mutate another session's goal or silently conflict with
  a daemon owner;
- example: `/goal adopt abc123` finds two projects with the same short ID;
- required change: phase 1 exposes only single-session commands; global list,
  ID-based cancel, and adoption move to phase 3;
- acceptance test: phase-1 help contains no command that can mutate another
  session.

disposition: fixed in section 5.

### R1-5 — P2 — multimodal input could regress into the exact image crash class

- who: user pasting an image into the CLI;
- what: the first draft said “preserve attachments” but did not state where
  text projection is allowed or how image-only goals behave;
- where: command parsing, output, and provider integration;
- why: a list of parts could reach `.strip()`, `.lower()`, `shlex`, logging, or
  Hub broadcast code;
- example: mixed `/goal` input is stored as `str(parts)`, leaking bytes and
  making the objective unreadable;
- required change: define text projection versus structured forwarding, reject
  image-only objectives, and store only artifact references;
- acceptance test: text/image/mixed cases run through the actual hook pipeline;
  image bytes never appear in logs/events/broadcasts/provider text projection.

disposition: fixed in sections 5, 9.2, and 12.

### R1-6 — P2 — status vocabulary did not match either runtime

- who: operator diagnosing why work stopped;
- what: the first draft used `planned/running/succeeded/failed/cancelled`,
  obscuring paused, blocked, quota, and budget stops;
- where: old state machine and UI contract;
- why: “failed” could trigger unsafe retry and “succeeded” could imply evidence;
- example: quota exhaustion is rendered as failure and the user retries forever;
- required change: use explicit active/paused/blocked/usage_limited/
  budget_limited/complete/cleared states and define transitions;
- acceptance test: each stop state renders a distinct next action and cannot
  silently auto-retry.

disposition: fixed in sections 6.1 and 8.

## 16. adversarial review round 2 — implementation and runtime attack

This pass attacked the revised contract for races, restart behavior, accounting,
hooks, attachments, and test validity.

### R2-1 — P0 — duplicate continuation after crash or reconnect

- who: daemon and attached TUI;
- what: a continuation could be enqueued twice between idle detection and state
  persistence;
- where: sections 7.2 and 8.3;
- why: duplicate provider turns violate user intent and can cause paid or
  destructive work twice;
- example: daemon A publishes idle, daemon B recovers the lease, then A sends
  its already-built continuation too;
- required change: claim under lock with goal ID/version/lease and continuation
  sequence, commit dispatch intent before provider submission, and make stale
  submissions no-ops;
- acceptance test: kill/reconnect two fake daemons at every claim/dispatch
  boundary and observe at most one provider request per sequence.

disposition: addressed by the lease/CAS/index contract in sections 7.2, 7.3,
8.3, and the runtime acceptance tests in section 12. This remains a hard
implementation gate, not an optional optimization.

### R2-2 — P1 — crash window between goal file and active pointer

- who: user restarting immediately after `/goal`;
- what: a crash could leave a goal record without an active pointer or two
  pointers to different unfinished goals;
- where: section 7.3;
- why: restart recovery could duplicate or lose work;
- example: the process dies after writing `<goal-id>.json` but before publishing
  `index.json`;
- required change: hold the session lock across creation, publish atomically,
  reconcile orphan records on startup, and test every write boundary;
- acceptance test: fault-inject each step and require one recoverable goal,
  never two active goals and never silent deletion.

disposition: addressed in sections 7.1, 7.3, 12, and 14.

### R2-3 — P1 — usage accounting could lie at the budget edge

- who: user with a hard token budget;
- what: provider usage can be delayed or absent, and the in-flight turn can
  overshoot a limit;
- where: GoalRecord accounting and budget semantics;
- why: displaying zero or marking budget success produces false guarantees;
- example: provider returns no usage, the loop continues three more turns, and
  `/goal` reports `0/1000`;
- required change: nullable usage, trusted-delta accounting, in-flight final
  accounting, and `usage_limited` when a hard budget cannot be enforced;
- acceptance test: fake provider omits usage and returns delayed usage; the
  loop stops safely and never displays an invented zero.

disposition: fixed in sections 6.1 and 8.6.

### R2-4 — P1 — user input race and cancellation priority

- who: user typing while a goal continuation is pending;
- what: automatic work could outrank newly queued user input or survive `/goal
  clear`;
- where: section 8.3 and UX section;
- why: the system would feel autonomous against the user's immediate command;
- example: user submits `/goal pause`, but the already-queued continuation
  starts first;
- required change: user input wins before dispatch, clear/pause invalidates
  pending sequence, and active tools stop only cooperatively at safe boundaries;
- acceptance test: inject user commands at each queue transition and assert no
  continuation starts after a winning pause/clear.

disposition: fixed in sections 8.3 and 10.3.

### R2-5 — P1 — provider control fallback could silently revert to prose

- who: provider adapter maintainer;
- what: an adapter without typed goal control might be tempted to parse final
  text for “done”;
- where: section 9.1;
- why: provider neutrality would become false and completion would be unsafe;
- example: a model writes “done” in an explanation and runtime completes with no
  test evidence;
- required change: capability check and explicit manual-inspection mode or
  creation failure; no text scraping;
- acceptance test: unsupported fake adapter produces a visible capability state
  and zero automatic completions.

disposition: fixed in section 9.1.

### R2-6 — P1 — hook/message preservation was not testable enough

- who: multimodal CLI user and hook/plugin maintainer;
- what: “preserve structured content” could pass unit tests while an event hook
  still stringified or broadcast it;
- where: old integration/test language;
- why: the existing image crash was specifically a consumer-boundary failure;
- example: goal matching works, then a post-input hook calls `.strip()` on the
  parts list and the daemon submission crashes;
- required change: require actual event-hook and attached-daemon tests with
  provider-boundary byte/reference assertions;
- acceptance test: text-only, image-only, mixed, and ordinary user turns pass
  through every USER_INPUT_PRE/USER_INPUT/USER_INPUT_POST consumer without a
  hook crash or image-byte log.

disposition: fixed in sections 9.2, 10.1, and 12.

### R2-7 — P2 — verification could accidentally incur real work

- who: reviewer running the new suite;
- what: a goal test could spawn an agent, call a real provider, or write into
  the user's normal config;
- where: old acceptance criteria;
- why: verification would be expensive, nondeterministic, and unsafe in a dirty
  shared checkout;
- example: a “completion” integration test consumes a paid call while checking
  a fake file change;
- required change: isolated runtime, fake provider, network disabled, no agent
  spawning, and explicit artifact/dirty-tree proof;
- acceptance test: test output records the temporary data root and fake provider
  request count; paid/network request count is zero.

disposition: fixed in sections 2, 12, and 14.

### R2-8 — P2 — attachment retention and leakage were incomplete

- who: user pasting a private image into a goal;
- what: storing an artifact reference without retention/cleanup rules could
  leave private media behind or display it in status;
- where: attachment refs and output sections;
- why: goal history can outlive the active session;
- example: `/goal show` in a later attach exposes a raw image path outside the
  project scope;
- required change: scoped artifact IDs, checksums/media metadata only in goal
  state, bounded display, explicit retention owner, and cleanup only after a
  future retention policy—not opportunistic deletion;
- acceptance test: status/events contain no bytes or unscoped path, and restart
  retains a valid scoped reference without broad cleanup.

disposition: addressed in sections 6.2, 7.1, 9.2, 10.2, and 12. Retention
policy is intentionally deferred rather than guessed.

### R2-9 — P2 — adoption was still an attractive unsafe shortcut

- who: operator with multiple projects and agents;
- what: users will ask for `/goal adopt` once reconnect works;
- where: phase plan and command contract;
- why: adding it without authorization, project identity, and lease transfer
  would reintroduce R1-2;
- example: a short goal ID is adopted from a different project root;
- required change: keep adoption out of phase 1 and make project/session/lease
  negotiation a phase-3 prerequisite;
- acceptance test: phase-1 parser rejects `adopt` with a clear “not available”
  message and cannot mutate another session.

disposition: fixed in sections 5 and 13.

## 17. adversarial review round 3 — implementation-readiness attack

This pass verified the round-2 "fixed" dispositions against live code paths
with isolated probes and a storage simulation. It found eight material gaps
plus two omissions; all are now contract requirements above, with their
required tests in the round-3 gates. Design guidance adopted alongside them:
GoalService is policy and state transitions only, execution stays on the
existing QueueProcessor path; one typed `goal_report` tool through the
existing tool infrastructure instead of per-adapter protocols; execution
generation is separate from record versions changed by accounting or lease
renewal; uncertain crash recovery pauses safely, and automatic recovery
exists only where execution can be reconciled.

### R3-1 — P0 — lease/CAS cannot decide duplicate execution

- who: daemon and provider;
- what: persisting dispatch intent does not establish whether an external
  request executed; daemon A submits and crashes before recording the
  result, daemon B retries after lease expiry;
- where: sections 7.2 and 8.3 as previously written;
- why: duplicate provider turns repeat paid or destructive work;
- required change: durable attempt states, owner fencing, ambiguous attempts
  recover as paused pending reconciliation, automatic retry only when
  non-execution is established;
- required test: crash after provider acceptance but before local
  acknowledgement; recover without repeating the operation.

disposition: fixed in 6.1, 6.4, 7.2 (epoch fencing), 7.4, and the round-3
gates.

### R3-2 — P1 — shared index.json has a reproduced lost-update race

- what: one shared `index.json` guarded by separate per-session advisory
  locks let two concurrent writers each add a pointer and overwrite each
  other — two successful writers left one persisted pointer in simulation;
  separate record/event/index writes also lacked an atomic commit boundary;
- required change: transactional storage with a uniqueness constraint on
  unfinished goals per session, or per-session state files with specified
  crash recovery at every write boundary.

disposition: fixed in 7.1/7.3 — SQLite with a partial unique index replaces
the JSON layout. Round 3's simulation is the code-level proof the original
escape hatch anticipated.

### R3-3 — P1 — stable session identity is a prerequisite, not a narrow choice

- what: `resume_conversation()` deliberately generates a new session ID
  (`kollabor/state/local.py:2453`); context switching replaces conversation
  history within one daemon; goals keyed to the rotating identity would
  disappear or follow the wrong conversation;
- required change: durable conversation identity plus explicit behavior for
  reconnect, `/resume`, session reset, context switching, and opening the
  same saved conversation twice;
- required test: switch contexts and resume saved history while a goal
  exists; prove exactly which conversation owns it.

disposition: fixed in 6.1, 6.5, 11 seam 1, and the round-3 gates.

### R3-4 — P1 — the input path cannot deliver the promised controls

- what: `send_message()` rejects input while processing before parsing
  commands (`local.py:2236`) — isolated probes showed `/goal pause`, `/goal
  clear`, and `/goal show` all returning "turn already in flight"; the same
  method excludes image-bearing messages from command parsing
  (`local.py:2275`), so a mixed `/goal` probe reached ordinary LLM input;
- required change: daemon-side goal control operations that remain available
  during processing, slash-command routing to them, structured attachments
  preserved, and stop requests durably recorded immediately even when the
  running tool must finish cooperatively.

disposition: fixed in 5 (control-command rule), 10.3, 11 seam 4, and the
round-3 gates.

### R3-5 — P1 — evidence audit promised more than {kind, ref, claim} verifies

- what: a valid test-output reference does not establish that it proves the
  objective, covers every requirement, or postdates the last relevant edit;
  deterministic checking was deferred to phase 2, leaving nothing verifiable
  in phase 1;
- required change: separate evidence provenance from acceptance
  verification; define supported deterministic checks and freshness rules
  now; label unrestricted natural-language completion as model-declared with
  evidence unless a human or defined checker verifies it; commit completion
  only after the entire tool batch settles.

disposition: fixed in 6.1 (`completion_declared`), 8.5, and the round-3
gates.

### R3-6 — P1 — bounded execution and hard budgets were not actually specified

- what: three consecutive identical blockers do not stop alternating
  blockers or repeated successful reads presented as progress; budgets were
  optional with no goal-wide turn or time limit; per-turn accounting cannot
  guarantee a token ceiling because Kollab makes multiple provider requests
  inside one turn (`queue_processor.py:821`);
- required change: independent automatic turn/time limits, evidence
  deduplication, per-request accounting, admission checks, documented
  bounded overshoot, and explicit resumed-budget semantics.

disposition: fixed in 6.1 (`auto_turn_limit`/`auto_time_limit_s`), 8.4, 8.6,
and the round-3 gates.

### R3-7 — P1 — pasted-image references do not survive restart

- what: the existing input image store is explicitly process-local
  (`message_content.py:56`); a reference resolving in its original store
  fails in a fresh one, so "reuse the existing image-store API" cannot
  satisfy durable attachments;
- required change: promote into durable artifact storage before
  acknowledging goal creation, rehydrate through the provider's image
  resolver, and define missing-artifact behavior.

disposition: fixed in 7.1 layout, 9.2 case 3, and the round-3 gates.

### R3-8 — P1 — the continuation boundary needs a concrete integration contract

- what: `turn_complete` also follows cancellation and exceptions
  (`queue_processor.py:1490`) and slash-command emissions
  (`local.py:2374`); it is not evidence that a successful goal turn reached
  an idle scheduling boundary, and the Hub continuation path
  (`kollabor/llm/message_handler.py:367`) was uncovered;
- required change: typed outcomes carrying turn origin, goal identity,
  cancellation/error status, and request usage; recheck eligibility after
  acquiring the existing turn lock; cover Hub continuation as well as
  ordinary queue processing.

disposition: fixed in 8.3 and the round-3 gates.

### R3-9 — P2 — two omissions

- cleared goals must remain inspectable, but no history command existed:
  `/goal history [N]` added in section 5;
- pending background work needs a completion-triggered eligibility check so
  an active goal cannot remain asleep indefinitely: settle-driven wake
  added in 8.3.

disposition: fixed in 5 and 8.3; both covered by the round-3 gates.

## 18. final review recommendation

Round 3's verdict was "revise before implementation"; this revision folds its
contracts in. The contract is suitable for phase-0 implementation only if the
P0/P1 gates in rounds 2 and 3 are treated as acceptance criteria, not future
polish. The recommended build order is conversation identity and the SQLite
GoalStore with the attempt/recovery harness first, then command wiring and
daemon-side controls, then typed-outcome continuation, then TUI rendering.

The spec is not implementation proof. Before calling the feature complete,
verify the real attached-daemon path, event-hook consumers, provider boundary,
restart/recovery, and isolated no-paid-call test evidence listed in section 12.

## 19. unresolved choices that must not be guessed during implementation

These are narrow implementation decisions, not permission to expand scope.
Resolved by round 3 and no longer open: session identity (`conversation_uid`,
6.5), storage format (SQLite, 7.1), attachment durability (goal artifact
store, 9.2), and the reporting surface (single `goal_report` tool, 9.1).

Still open:

- choose the existing event-bus event type/serialization while preserving the
  event fields and redaction rules above;
- choose SQLite pragma/migration details (WAL settings, schema versioning)
  without changing the external state machine or acceptance tests.

Anything outside these choices requires a new design review, especially global
goal listing, adoption, child goals, remote execution, evaluator-model billing,
or changes to TaskLedger semantics.

## 20. limitations

- Provider behavior and public documentation can drift; retain the installed
  command/version evidence when implementation begins.
- The local Codex binary and official source establish Codex behavior, not a
  guarantee that every future build has the same feature gate or UI.
- Claude's evaluator behavior is documented by Anthropic, but it is not a
  provider-neutral contract for Kollab.
- The current Kollab repository has no live `/goal` implementation. This file
  specifies the smallest safe design and the proof required; it does not claim
  that any goal loop is already running.
- Round 3 verified input routing, image lifetime, and the JSON storage race
  with isolated probes; no live goal implementation or paid provider
  execution has been tested. The cited line numbers are snapshots of
  2026-09-20 and will drift.
