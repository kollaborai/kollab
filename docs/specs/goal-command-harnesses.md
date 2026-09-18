---
title: "Provider-neutral /goal command and continuation contract"
status: proposed-after-two-adversarial-reviews
owner: kollab
date: 2026-09-17
---

# Provider-neutral `/goal` command and continuation contract

status: proposed for phase-0 implementation; two adversarial review rounds are
included below.

## decision summary

Kollab should add a durable, provider-neutral goal layer with these boundaries:

- A goal is a user-owned objective plus a continuation, verification, budget,
  and stop contract for one interactive chat session.
- A goal is scoped to a stable Kollab session/daemon attachment, not to a TUI
  process, provider session ID, project-wide singleton, or agent name.
- There is one active goal per session in phase 1. Creating another goal while
  one is unfinished is rejected; replacement requires an explicit `/goal clear`.
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
| ownership | persistent thread goal | session goal | stable Kollab chat session owned by daemon |
| active count | one unfinished goal per thread | one goal per session | one active/unfinished goal per session in phase 1 |
| completion authority | typed goal update plus evidence audit | transcript-only evaluator verdict | typed runtime/provider control plus concrete evidence |
| continuation | idle thread, no queued input, no pending work | after evaluator verdict; background deferral/check-ins | idle queue, no pending work, no queued user input |
| pause | user/system controlled | user/error controlled | user/system controlled; cooperative safe boundary |
| blocked/impossible | explicit blocked state | evaluator impossible clears goal | explicit blocked state after evidence-backed repeated blocker |
| budget | token/time accounting; budget-limited distinct | token display and evaluator billing | provider usage accounting; budget/quota stops are distinct |
| objective injection | internal goal context | session goal hook/evaluator context | escaped internal context item, never a fake user message |
| attachments | materialized references | condition is text/transcript-based | attachment refs only; never stringify bytes |
| task/delegation | separate from goal | background agents separate | existing TaskLedger remains child-work primitive |
| restart | persistent thread state | active goal restored on resume | daemon-owned state restored with lease/CAS |
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
- one active goal per stable session;
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
  event and removes the session's active pointer while retaining a bounded
  summary and event history.
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
session_id              stable Kollab chat/daemon session identity
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
record_version          CAS/version guard for stale updates
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
complete, cleared, lease_recovered, error
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

## 7. persistence, ownership, and concurrency

### 7.1 storage location and format

Implement `GoalStore` under `kollabor/state/goal_store.py`. Use the existing
project-scoped Kollab data-path policy used by `get_hub_dir()` and
`context_registry` rather than inventing a second config root. The phase-1
layout is:

```text
<project-scoped-kollab-data>/goals/
  index.json                 session -> active goal pointer
  <goal-id>.json             current GoalRecord
  <goal-id>.events.jsonl     append-only redacted GoalEvent stream
  .locks/<session-id>.lock   creation/active-pointer/claim lock
```

The implementation should extract a shared project-data path helper if the
current import direction would otherwise make core state depend on
`HubPlugin`. It must not duplicate the Hub task state machine.

Reuse the proven TaskLedger persistence properties: advisory lock, read under
lock, temp-file write, flush, fsync, atomic replace, restrictive directory
permissions, and corruption recovery that never silently invents a goal. On
startup, reconcile orphan goal files and stale index pointers; preserve them as
history and emit a recovery event rather than deleting them.

If a later implementation selects SQLite, it must preserve the same external
contract, per-session uniqueness, append-only events, CAS protection, and test
fixtures. SQLite is not permission to add a second unbounded transcript store.

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

### 7.3 creation transaction

`create()` holds the per-session lock across:

1. reading and validating the active pointer;
2. rejecting any unfinished goal;
3. writing the new GoalRecord;
4. appending `created`; and
5. atomically publishing the active pointer.

Recovery must handle a crash between steps without producing two active goals.
The acceptance test starts two writers concurrently and requires exactly one
successful creation.

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
turn.

### 8.3 safe-boundary continuation

After a goal-owned turn completes, the daemon may enqueue one continuation only
when all are true:

- the GoalRecord is still `active` and the goal ID/version/lease match;
- the current turn has fully completed;
- no tool call, background job, or approval is pending;
- the input queue has no user message waiting;
- no newer session command has invalidated the continuation;
- the provider/runtime is healthy and within quota/budget; and
- no other goal continuation is in flight for the session.

The check and claim are one guarded operation. A user message arriving before
dispatch wins over the continuation: cancel the pending continuation, leave the
goal active, and route the user message through the normal hook pipeline. The
next safe boundary may schedule another goal turn.

Every continuation carries `goal_id`, `record_version`, `continuation_seq`,
`session_id`, and `turn_id`. A stale completion cannot update a newer goal.

### 8.4 progress and no-progress stop

Progress is one of:

- a relevant tool result or artifact/state change;
- a typed `progress` control with concrete evidence reference; or
- a verified external wait result tied to a live handle.

Assistant prose, a repeated plan, a status restatement, or a failed observation
without a state change is not progress. The runtime canonicalizes the blocker
key. Three consecutive goal turns with the same blocker and no new evidence
transition to `blocked`, append the reason, and stop automatic continuation.
The user can resolve the blocker and explicitly `/goal resume`.

### 8.5 completion authority

Only a typed `GoalControl.complete` from the provider/runtime boundary or an
explicit future human completion command may complete a goal. The control must
contain:

- the expected `goal_id` and record version;
- a concise summary;
- one or more evidence references;
- the acceptance claims each reference supports; and
- any linked TaskLedger IDs and their terminal status.

The runtime rejects completion when the evidence is empty, inaccessible,
stale/indirect, inconsistent with the current scope, or attached linked tasks
remain nonterminal. It must perform a final audit against the current
worktree/external state before writing `complete`.

No completion may be inferred from the words “done”, a near-exhausted budget,
absence of an obvious error, or a provider's final prose alone.

### 8.6 budget, quota, and errors

- `token_budget` is optional and positive. If set, enforce it using trusted
  provider usage at each turn and account for the in-flight turn before
  transitioning to `budget_limited`.
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

The envelope may be produced by a provider-native tool call or structured
response metadata, but it must be normalized before the state store sees it.
Text scraping is forbidden. The runtime validates goal ID, version, scope,
evidence, permissions, and linked tasks before mutating state.

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
3. mixed text/image `/goal`: objective is the text projection and the image is
   represented by a scoped artifact ID/media type/checksum;
4. ordinary text/image user turns during a goal: original structured content
   reaches the provider boundary unchanged, while goal matching/continuation
   uses only the safe text projection.

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

`/goal pause` acknowledges “pause requested” if a tool is running and records
`paused` only at the safe boundary. `/goal clear` follows the same rule,
stops any not-yet-dispatched continuation, requests cooperative tool
cancellation where supported, and retains the audit trail. Ctrl-C may request
the same pause/clear behavior but must not erase state.

## 11. implementation seams and order

The implementation should follow these seams, in order:

1. `kollabor/state/goal_store.py`: record, event, lock, index, lease, CAS,
   recovery, redaction, and isolated test store.
2. `kollabor/goals/service.py`: state transitions, evidence audit, budget/
   blocker policy, task links, and guarded continuation decisions.
3. `kollabor/commands/system_commands/handlers/goal.py` plus registry wiring:
   raw remainder parsing, command errors, and status rendering.
4. Existing StateService/LLMService integration: goal context, turn identity,
   queue-idle guard, user-input priority, and attached-daemon routing.
5. Provider adapter normalization for `GoalControl`.
6. TUI/status event rendering only after the daemon behavior is proven.

Do not put goal state in `plugins/hub/plugin.py`, duplicate TaskLedger's task
methods, or add a second path that calls providers outside the current queue.

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

Build only the isolated GoalStore/service state machine, fake-provider control,
concurrency/recovery tests, and command parser contract. No automatic real
provider continuation.

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

## 17. final review recommendation

The contract is suitable for phase-0 implementation only if the P0/P1 gates in
round 2 are treated as acceptance criteria, not future polish. The recommended
build order is GoalStore and fake-provider harness first, then command wiring,
then daemon continuation, then TUI rendering.

The spec is not implementation proof. Before calling the feature complete,
verify the real attached-daemon path, event-hook consumers, provider boundary,
restart/recovery, and isolated no-paid-call test evidence listed in section 12.

## 18. unresolved choices that must not be guessed during implementation

These are narrow implementation decisions, not permission to expand scope:

- choose the exact stable session ID source already owned by StateService/
  daemon attach; do not derive it from PID or TUI instance;
- choose the existing artifact/image-store API for attachment refs; do not add a
  parallel byte store;
- choose whether the provider adapter uses a hidden tool or structured response
  envelope, but normalize both to the same `GoalControl` contract;
- choose the existing event-bus event type/serialization while preserving the
  event fields and redaction rules above;
- choose JSON-file storage as the default phase-1 pattern unless a code-level
  concurrency test proves SQLite is required; changing storage must not change
  the external state machine or acceptance tests.

Anything outside these choices requires a new design review, especially global
goal listing, adoption, child goals, remote execution, evaluator-model billing,
or changes to TaskLedger semantics.

## 19. limitations

- Provider behavior and public documentation can drift; retain the installed
  command/version evidence when implementation begins.
- The local Codex binary and official source establish Codex behavior, not a
  guarantee that every future build has the same feature gate or UI.
- Claude's evaluator behavior is documented by Anthropic, but it is not a
  provider-neutral contract for Kollab.
- The current Kollab repository has no live `/goal` implementation. This file
  specifies the smallest safe design and the proof required; it does not claim
  that any goal loop is already running.
