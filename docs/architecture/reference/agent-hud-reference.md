---
title: "Agent HUD Reference"
doc_type: reference
created: 2026-05-15
modified: 2026-05-15
status: active
---
# Agent HUD Reference

Agent HUD is the model-facing instrumentation channel for context that did not
come directly from the human. It replaces ad hoc fake user turns for hub events,
runtime nudges, and other environment updates.

## Goals

- keep non-human context out of standalone conversation turns
- preserve open-channel visibility in UI and logs
- show the model only compact diffs, not repeated full snapshots
- let hub wake semantics decide whether a new LLM turn is needed
- keep normal tool continuation loops in control after a wake

## Payload Shape

HUD diffs use one user payload only when there is a real user message or an
actionable hub wake:

```text
<agent_hud>
[hub:lapis->koordinator]
+ DOCS/GATE TASK COMPLETE. 2 commits shipped.

[system:hub_nudge]
+ [nudge] 3 other agents are working.
</agent_hud>

real user message, if present
```

Each block is a changed HUD item:

```text
[section:label]
+ first changed line
  continuation line
```

Current sections:

- `system`: runtime nudges, tool grants, skill activation notes, vault nudges
- `hub`: incoming hub messages and open-channel context

Raw request logs may include:

- `agent_hud: true`
- `agent_hud_sources: ["hub"]` or `["pending"]`

Provider adapters ignore those fields. Raw-log readers use them to distinguish
instrumentation from human-authored user messages.

## Wake Rules

Hub display/logging and LLM wake are separate decisions.

- display/log every hub message
- queue every hub message as a HUD diff
- wake immediately for actionable messages when the agent is idle
- buffer actionable messages when the LLM is busy, then wake once after the turn
- do not wake for passive acknowledgements

`standing by` is not enough to suppress a wake. A completion report can end with
`standing by` and still wake if it includes evidence such as a task id, commit
hash, shipped/completed wording, or an active task match.

Pure acknowledgements should observe only, for example:

- `got it`
- `confirmed`
- `thanks`
- `received`
- `收到`
- `等待下一个任务`

## Turn Rules

`inject_system_message()` queues a HUD diff and logs it. It must not append a
standalone conversation turn by itself.

Queued HUD diffs are drained in two places:

- the next real user message batch
- the one post-processing hub retry after an actionable busy-turn buffer

Tool-result continuations must not flush HUD diffs by themselves.
