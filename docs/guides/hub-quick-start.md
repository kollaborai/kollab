---
title: "Hub Quick Start"
created: 2026-04-05
modified: 2026-04-05
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
kollab --agent jarvis

# Terminal 2
kollab --agent coder

# Terminal 3
kollab --agent reviewer
```

Each agent gets a gem designation (lapis, peridot, ruby, etc.)
assigned automatically. The first agent to start becomes the
coordinator (marked with `*` in status).

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
kollab --hub msg jarvis "refactor the database layer"

# View the last 100 lines of an agent's output
kollab --hub capture jarvis 100

# Stream an agent's output in real-time (read-only)
kollab --attach jarvis

# Shut down an agent remotely
kollab --hub kill lapis
```

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
/hub cron add jarvis 5m "status update: what are you working on?"
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

Each agent designation gets a vault at `~/.kollab/hub/vaults/<designation>/`
with three tiers:

- **stream.jsonl** -- raw append-only log of everything (ground truth)
- **working_memory.md** -- rolling context injected into the system prompt
- **crystallized.md** -- distilled long-term knowledge

When an agent is launched with the same designation, it gets its
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
kollab --agent jarvis
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

Org files live in `bundles/agents/` and define agent names,
capabilities, and team structure.

```
/hub orgs
```

## Common Commands Reference

| Command | What it does |
|---|---|
| `/hub status` | Who's online, coordinator, states |
| `/hub whoami` | Your designation and identity |
| `/hub msg <agent> <text>` | Send message to agent |
| `/hub broadcast <text>` | Message all agents |
| `/hub kill <agent>` | Remote shutdown |
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

| CLI Flag | What it does |
|---|---|
| `kollab --hub status` | List online agents (no TUI) |
| `kollab --hub msg <agent> <text>` | Send message (no TUI) |
| `kollab --hub capture <agent> [n]` | View agent output (no TUI) |
| `kollab --hub kill <agent>` | Remote shutdown (no TUI) |
| `kollab --attach <agent>` | Stream agent output live |
| `kollab --agent <name>` | Launch with agent identity |
| `kollab --designation <gem>` | Override hub designation |
