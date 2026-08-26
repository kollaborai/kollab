---
title: "Provider-neutral /goal command contract"
status: proposed
owner: kollab
---

# Provider-neutral `/goal` command contract

status: proposed; research snapshot 2026-08-25

## objective

Define a provider-neutral Kollab `/goal` command for declaring a durable objective,
inspecting its execution, and resuming or cancelling work. The contract borrows
observable ideas from Claude Code and Codex CLI without requiring either provider,
its prompt syntax, or its session store.

## evidence and method

The local installations were queried directly on 2026-08-25:

- `claude --version` -> `2.1.246 (Claude Code)`.
- `claude --help`, `claude agents --help`, and `claude mcp --help` were run from
  `/Users/malmazan/dev/kollab`. Help documents interactive-by-default sessions,
  `-p/--print`, `--resume`, `--continue`, `--fork-session`, `--session-id`,
  `--no-session-persistence`, `--permission-mode`, `--agent`, `--agents`,
  `--bg/--background`, and the `agents` background-session manager. It does not
  document a built-in `/goal` command.
- `codex --version` -> `codex-cli 0.149.1`.
- `codex --help`, `codex exec --help`, `codex resume --help`, `codex agents
  --help`, and `codex queue --help` were run locally. Help documents interactive
  sessions, `exec`, `resume`, `fork`, `queue`, `archive`, `delete`, `--ephemeral`,
  JSONL output, sandbox modes, approval policies, and remote app-server sessions.
  It does not document a built-in `/goal` command.

These command outputs are authoritative for the installed versions above. They
are not evidence that undocumented slash commands do or do not exist. Public
references used for terminology and lifecycle comparison:

- Claude Code CLI reference: https://docs.anthropic.com/en/docs/claude-code/cli-reference
- Claude Code subagents: https://docs.anthropic.com/en/docs/claude-code/sub-agents
- Codex CLI documentation: https://developers.openai.com/codex/cli/
- Codex CLI non-interactive mode: https://developers.openai.com/codex/cli/usage/

Where behavior is not stated by local help or the linked documentation, this
spec labels it as inference or leaves it intentionally unspecified.

## web evidence (retrieved 2026-08-25)

The official Claude Code `/goal` guide describes a session-scoped completion
condition evaluated after each turn by a small model. The evaluator returns
`met`, `not yet met`, or `impossible`; the goal clears on completion,
impossibility, unrecoverable authentication/credit/context/model errors, or
`/goal clear`. One goal is active per session, and `/goal` with no arguments
shows condition, elapsed time, evaluated turns, token spend, and the latest
reason. Active goals are restored when a session is resumed, while achieved or
cleared goals are not. Background work defers evaluation; idle check-ins back
off and are capped. Source: https://code.claude.com/docs/en/goal

The official OpenAI Codex Goals guide describes a persistent, thread-scoped
objective with a measurable outcome, verification surface, constraints, and
budget. `/goal`, `/goal pause`, `/goal resume`, and `/goal clear` manage the
lifecycle. Continuation is conservative: it occurs only after a turn finishes,
when the thread is idle, no user input is queued, and no work is pending.
Completion must be supported by concrete evidence (tests, benchmarks, files,
logs, or artifacts); budget exhaustion is distinct from success. The guide
lists support beginning with Codex 0.128.0. Source:
https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex.md

A Codex issue documenting the command confirms observed states including
`pursuing`, `paused`, `achieved`, `unmet`, and `budget-limited`, and calls for
lifecycle/help discovery. The issue is corroborating context rather than an
API contract: https://github.com/openai/codex/issues/20536


## comparison (verified behavior)

| concern | Claude Code 2.1.246 | Codex CLI 0.149.1 | implication for Kollab |
|---|---|---|---|
| command/start | interactive default; `-p` prints and exits | interactive default; `exec` is non-interactive | `/goal` is a command inside a session, with an explicit non-interactive API later |
| state | sessions can continue/resume/fork; persistence can be disabled | sessions can resume/fork/archive/delete; `exec --ephemeral` disables persistence | goal identity must be separate from provider session IDs |
| scope | current directory plus `--add-dir`; optional worktree | `-C/--cd`, `--add-dir`, sandbox workspace root | capture cwd and allowed roots at creation |
| execution | built-in tools, permission modes, background agents | shell execution under read-only/workspace-write/danger-full-access and approval policy | every goal action passes Kollab permission policy |
| delegation | `--agent` and custom `--agents`; background `agents` view | `agents` browses app-server sessions; no equivalent goal declaration in help | delegation is optional capability, not core goal semantics |
| cancellation/resume | resume/continue/fork documented; cancellation mechanics are interactive UX | resume/fork and queue documented; cancellation is not specified in help | define cancellation state and resume explicitly in Kollab |
| output | text/json/stream-json in print mode; session transcript is provider-owned | text or JSONL events; last message can be written to a file | expose stable goal events and artifact references, not provider wire formats |
| safety | permission modes, allowed/disallowed tools, bypass flags | approval policy and sandbox modes, dangerous bypass flags | safe defaults and explicit confirmation for mutating work |

## contract

### syntax

Interactive forms:

- `/goal <objective>` — create a goal and begin execution.
- `/goal` — show the active goal, or offer a picker when none is active.
- `/goal list [--all]` — list goals visible in the current project/user scope.
- `/goal show <goal-id>` — show state, plan summary, timestamps, and artifacts.
- `/goal pause <goal-id>` — request cooperative pause at the next safe boundary.
- `/goal resume <goal-id>` — continue a paused or interrupted goal.
- `/goal cancel <goal-id>` — request cancellation; never silently delete history.
- `/goal adopt <goal-id>` — make an existing goal active in this session.

The objective is the remainder of the input, preserved verbatim. Future options
may include `--scope`, `--profile`, and `--delegation`, but phase 1 must reject
unknown options rather than guessing.

### lifecycle and state

A goal has a stable opaque `goal_id`, `objective`, `status`, `created_at`,
`updated_at`, `owner_scope`, `session_refs`, `parent_goal_id` (nullable), and
`artifacts`. Status transitions are:

`planned -> running -> {paused, succeeded, failed, cancelled}`

`paused -> running` is resume. `running -> cancelled` is allowed only after a
cancellation request is acknowledged. Terminal goals are immutable except for
append-only events and metadata. Provider session IDs are references, not keys.

The implementation must persist goals by default in Kollab's project/user data
store, with an explicit ephemeral mode for tests and automation. Persistence
must exclude credentials and redact secrets from objectives, events, and logs.

### execution semantics

Creating a goal records an initial `goal.created` event, snapshots the effective
project root and permission policy, and schedules work through Kollab's existing
agent/tool pipeline. A goal may produce multiple turns and provider sessions.
Each action emits events (`planned`, `started`, `progress`, `tool_request`,
`tool_result`, `paused`, `resumed`, `succeeded`, `failed`, `cancel_requested`,
`cancelled`). Event payloads include monotonic sequence numbers and timestamps.

No provider may bypass the normal tool authorization, hook, or approval path.
A goal cannot change its scope, model profile, or permission mode silently after
creation; changes require an explicit event and user confirmation.

### interactive UX

`/goal` displays a compact status line and a deterministic picker for multiple
goals. Long objectives are truncated only for display; `show` provides the full
value. Progress is incremental and must remain readable in inline and alternate
screen TUI modes. Errors identify the goal ID, current state, and a next action.

Pause/cancel are cooperative. Ctrl-C may request cancellation of the active goal,
but must not erase persisted events. Resume offers the last known checkpoint and
reports when no resumable checkpoint exists.

### delegation

Phase 1 permits one execution owner per goal. A later delegation phase may create
child goals with `parent_goal_id`; children inherit scope and the strictest
permission policy unless explicitly approved otherwise. Child output is linked by
IDs and summarized in the parent. Provider-specific subagent names are metadata,
not part of the contract.

### output and artifacts

Goal output is an ordered event stream plus a final summary. Tool output too large
for the model/UI is stored using Kollab's existing artifact mechanism and exposed
as a path/reference. Artifacts record producer event, media type, size, checksum,
and retention scope. `/goal show` must never inline unbounded output.

### safety and permissions

Defaults are read-only inspection where possible and normal Kollab approval for
writes, network access, and destructive commands. `--dangerous` is not a phase-1
option. Every denial is recorded as an event visible to the user. Goals inherit
project trust and allowed directories at creation; adopting a goal in another
scope requires re-validation and may be refused.

## phased implementation

### phase 1: local durable goal (minimum contract)

Implement parser/registry entries, goal store, state machine, event schema,
`create/list/show/pause/resume/cancel/adopt`, active-goal UI, and tests for
transitions, restart recovery, duplicate commands, and permission denials.
Use one existing Kollab agent execution at a time; no child delegation.

### phase 2: checkpoints and artifacts

Persist resumable checkpoints at safe tool boundaries, integrate artifact
references, stream progress events, and add JSON output for scripts. Define
retention and garbage collection without deleting terminal summaries.

### phase 3: delegation and remote execution

Add child goals, concurrency limits, parent aggregation, remote/session
references, and provider adapters. Require explicit scope/permission
negotiation before remote adoption.

## non-goals

- Reproducing Claude Code or Codex CLI slash-command spelling beyond `/goal`.
- Exposing provider-specific session files, event schemas, model names, or APIs.
- Implicit background execution, automatic retries, or permission escalation.
- Replacing Kollab's existing hooks, command registry, tool executor, or artifact
  storage with a parallel subsystem.
- Guaranteeing identical plans or output across providers.
- Treating an objective as a promise of success; failures remain inspectable.

## acceptance criteria

- A user can create, inspect, pause, resume, cancel, and adopt a goal using only
  documented commands.
- Restarting Kollab preserves goal identity and append-only event history.
- No goal action bypasses existing permission/approval and hook pipelines.
- Tests cover every state transition, invalid transition, cancellation race,
  scope mismatch, redaction, and artifact reference behavior.
- Provider adapters can be swapped without changing the goal store or command
  contract.

## limitations

This is a proposal, not an implementation. Local CLI help cannot establish every
interactive behavior, especially undocumented `/goal` commands, signal handling,
or provider backend persistence. The cited web pages may change; retain the
installed-version command transcripts in release research notes when implementing.
