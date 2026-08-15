---
title: "Hub Quick Start"
created: 2026-04-05
modified: 2026-08-06
status: active
---
# Hub Quick Start

The hub is a zero-config peer-to-peer mesh that lets multiple kollab
agents discover each other, exchange messages, persist memory across
sessions, and coordinate work -- all automatically. Launch agents in
separate terminals and they find each other through presence files and
unix sockets. No server, no broker, no setup.

## Launch Your First Agent Mesh

Open three terminal windows and launch one agent in each:

```bash
# Terminal 1
kollab --agent coder

# Terminal 2
kollab --agent research

# Terminal 3
kollab --agent technical-writer
```

Each process gets a hub identity (lapis, peridot, ruby, etc.) assigned
automatically. The identity is the name used for messages, capture, vaults, and
offline inbox delivery. The `--agent` value is a separate agent bundle that
defines behavior. The first process to start becomes the coordinator (marked
with `*` in status).

For a stable identity and an explicit bundle, use both flags:

```bash
kollab --agent coder --as lapis --profile openai-oauth
```

Here `coder` is the bundle, `lapis` is the hub identity, and `openai-oauth` is
the provider profile. See [Agents and Skills](../features/agents.md) for the
full mapping and organization-role format.

Check who's online from any agent:

```
/hub status
```

Or from the command line without entering the TUI:

```bash
kollab --hub status
```

## Send Messages Between Agents

From any agent, send a direct message:

```
/hub msg lapis fix the auth bug in login.py
```

All agents see all messages (open channel model). The target agent
gets the message injected into its conversation context so the LLM
can act on it.

Broadcast to everyone:

```
/hub broadcast we're switching to the v2 API, update your imports
```

## Hub CLI (No TUI Needed)

Manage agents from the command line without starting an interactive
session:

```bash
# See who's online
kollab --hub status

# Send a message to an agent
kollab --hub msg lapis "refactor the database layer"

# View the last 100 lines of an agent's output
kollab --hub capture lapis 100

# Attach interactively to a live agent
kollab --attach lapis

# Shut down an agent remotely
kollab --hub stop lapis
```

`capture` targets a live hub peer or live orchestrator session. It does not
target an agent bundle and it cannot read an offline inbox. If status shows
`offline inboxes: lapis(1)` but not a live `lapis` peer, the message is queued
for later delivery and `capture lapis` correctly reports that no capturable
agent exists yet. Send with `--hub msg lapis ...` and capture the identity shown
under the online roster after it reconnects.

## Assign Tasks

The task ledger persists tasks to disk as JSON files. Tasks survive
context compaction -- they're injected into the system prompt on
every LLM turn, so agents never forget active assignments.

```
/hub tasks assign peridot "refactor the config loader to use dataclasses"
```

Check task status:

```
/hub tasks list
/hub tasks mine
/hub tasks status <task-id>
```

Tasks follow a lifecycle: `active -> done -> QA review -> closed`.
When an agent completes a task, the assigner can approve or reject
it through QA review.

## Schedule Recurring Messages

Hub cron lets you send messages to agents on a schedule:

```
/hub cron add lapis 5m "status update: what are you working on?"
/hub cron add all 1h "run the test suite and report results"
```

Intervals support `s` (seconds), `m` (minutes), `h` (hours), or
combinations like `2h30m`.

Manage cron jobs:

```
/hub cron list
/hub cron delete <id>
/hub cron clear
```

## Persistent Memory (Vaults)

Each agent identity gets a vault at `~/.kollab/hub/vaults/<identity>/`
with three tiers:

- **stream.jsonl** -- raw append-only log of everything (ground truth)
- **working_memory.md** -- rolling context injected into the system prompt
- **crystallized.md** -- distilled long-term knowledge

When an agent is launched with the same identity, it gets its
vault hydrated back. It remembers previous sessions.

Inspect vaults:

```
/hub vault lapis
/hub vaults
```

## Dreaming

When an agent has been idle for 5+ minutes (configurable), it
automatically enters a dreaming cycle:

1. Reads recent entries from its vault stream
2. Sends them to the LLM for distillation
3. Appends extracted insights to `crystallized.md`

This happens in the background without interrupting active
conversations. Agents get smarter over time by reviewing their
own history.

## Set Up Telegram Bridge

Talk to your agents from your phone:

```bash
export KOLLAB_HUB_BRIDGE_TOKEN=your-bot-token    # from @BotFather
export KOLLAB_HUB_BRIDGE_CHAT_ID=your-chat-id    # from @userinfobot
kollab --agent coder --as lapis
/hub bridge setup
/hub bridge enable
```

Voice messages on Telegram get transcribed locally via whisper.
See [Telegram Bridge Setup](telegram-bridge-setup.md) for the
full walkthrough.

## Launch Organizations

Define a team in a JSON org chart and launch them all at once:

```
/hub org my-team "build the authentication system"
```

Bundled org files live in `plugins/hub/organizations/`; user overrides live in
`~/.kollab/hub/organizations/`. Each role has an `identity` (the hub name), an
`agent_bundle` (the behavior package), and an optional role prompt.

```json
{
  "identity": "qa-eng",
  "role": "QA Engineer",
  "agent_bundle": "research",
  "prompt": "Test the change and report reproducible failures.",
  "reports_to": "apps-lead"
}
```

The launcher turns that role into `kollab --agent research --as qa-eng` and
adds the role prompt. Copy a bundled chart to the user override directory when
you want to customize it without editing the installation.

```
/hub orgs
```

## Common Commands Reference

| Command | What it does |
|---|---|
| `/hub status` | Who's online, coordinator, states |
| `/hub whoami` | Your hub identity and display name |
| `/hub msg <agent> <text>` | Send message to agent |
| `/hub broadcast <text>` | Message all agents |
| `/hub stop <agent\|all>` | Remote shutdown (`kill` is an alias) |
| `/hub spawn <name> <task>` | Spawn a sub-agent |
| `/hub capture <name> [lines]` | View agent output |
| `/hub stop <name\|all>` | Stop agent(s) |
| `/hub agents` | List all active agents |
| `/hub cron add <target> <interval> <msg>` | Schedule recurring message |
| `/hub cron list` | Show active cron jobs |
| `/hub tasks assign <agent> <directive>` | Assign a task |
| `/hub tasks mine` | Show your active tasks |
| `/hub tasks list` | All tasks across the mesh |
| `/hub bridge setup` | Configure Telegram bridge |
| `/hub bridge enable` | Start bridge loop |
| `/hub vault [name]` | Inspect agent vault |
| `/hub feed` | Live activity dashboard |
| `/hub console` | Agent management UI |
| `/hub org <name>` | Launch an organization |
| `/hub dns resolve [name]` | Resolve agent / list the DNS roster |
| `/hub dns endpoint` | Show off-box A2A endpoint status |
| `/hub dns connect <authority>` | Import a remote mesh's published keys |

| CLI Flag | What it does |
|---|---|
| `kollab --hub status` | List online agents (no TUI) |
| `kollab --hub msg <agent> <text>` | Send message (no TUI) |
| `kollab --hub capture <agent> [n]` | View agent output (no TUI) |
| `kollab --hub kill <agent>` | Remote shutdown (no TUI) |
| `kollab --attach <identity>` | Interactive TUI proxy to a live agent |
| `kollab --agent <bundle>` | Launch with a behavior bundle |
| `kollab --as <identity>` | Choose a stable hub identity |
