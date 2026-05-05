---
title: "Kollabor Mesh: Runtime-Agnostic Agent Bridge"
doc_type: architecture-rfc
created: 2026-04-05
modified: 2026-04-05
status: draft
depends_on:
  - ../records/audits/AUDIT-2026-04-05-hub-completion.md
  - RFC-2026-04-04-agent-hub-unification.md
---
# Kollabor Mesh: Runtime-Agnostic Agent Bridge

## Problem Statement

AI coding agents are fragmenting across runtimes. Claude Code, Codex,
Gemini CLI, OpenCode, and Kollab each have their own agent systems
with incompatible IPC, presence, and coordination mechanisms. A team
using multiple runtimes has no way to make their agents collaborate.

Kollabor Mesh bridges all five runtimes into a single agent network
using the one protocol every runtime already supports: MCP.

---

## Runtime Landscape

```
                  claude code    codex          gemini cli     opencode       kollab
                  -----------    -----          ----------     --------       ------
lang              typescript     rust           typescript     typescript     python
ipc               filesystem     json-rpc/stdio A2A protocol   SSE/HTTP       unix sockets
                  (inbox files)  (app server)   (json-rpc)     (server)       + mailbox

presence          team config    threads/       agent cards    none           heartbeat files
                  json file      sessions       (A2A)          (stateless)    + socket ping

hooks             settings.json  hooks.json     settings.json  plugins (TS)   hooks.json
                  (11+ events)   (5 events)     (11 events)    (7+ hooks)     + event bus

multi-agent       agent teams    spawn_agent    subagents      in-process     hub mesh
                  (filesystem)   parallel (24)  (tool-based)   agents         (coordinator)

mcp               yes            yes (toml)     yes            yes            yes
config format     settings.json  config.toml    settings.json  opencode.json  config.json
context file      CLAUDE.md      AGENTS.md      GEMINI.md      AGENTS.md      system_prompt.md
state dir         ~/.claude/     ~/.codex/      ~/.gemini/     ~/.config/     ~/.kollab/
                                                               opencode/
persistence       none (teams)   sqlite+jsonl   30-day chats   sqlite         vaults
sandbox           none           landlock+      docker         permission     permission
                                 seccomp        (enterprise)   rules          system
```

### Key Observations

1. Every runtime supports MCP. This is the universal extension point.
2. Every runtime has hooks (lifecycle event interception).
3. No runtime can talk to any other runtime natively.
4. Kollab's hub system is the most complete coordination layer
   (presence, messaging, vaults, work queue, coordinator election).
5. The gap is not capability -- it's interoperability.

---

## Runtime Technical Details

### Claude Code

identity & coordination:
  agent teams: lead + workers (2-16 instances)
  filesystem inbox: ~/.claude/teams/{team}/inboxes/{name}.json
  task list: ~/.claude/teams/{team}/ (one JSON per task)
  team config: ~/.claude/teams/{team}/config.json (members array)
  discovery: read team config for member list
  polling: file-based, no persistent connection

hooks (settings.json or .claude/settings.json):
  events: SessionStart, SessionEnd, PreToolUse, PostToolUse,
          UserPromptSubmit, Stop, PermissionRequest,
          SubagentStart, SubagentStop, TaskCreated,
          TaskCompleted, TeammateIdle, FileChanged,
          CwdChanged, InstructionsLoaded, Notification
  handler types: command (shell), http, prompt, agent
  exit codes: 0=success, 2=block
  input: JSON on stdin (session_id, cwd, hook_event_name, etc)
  output: JSON on stdout (decision, updatedInput, etc)

MCP config (.claude/settings.json):
  "mcpServers": {
    "name": {
      "command": "path/to/server",
      "args": ["arg1"],
      "env": {"KEY": "value"}
    }
  }

subagents:
  spawned via Agent tool (in-context, not subprocess)
  isolated context window, no nesting
  only summary returns to parent

constraints:
  no cross-session persistence
  no peer discovery outside team config
  flat hierarchy (single lead)

### Codex CLI

identity & coordination:
  threads: durable session containers (UUID-based)
  spawn_agent: creates subagent subprocess
  spawn_agents_parallel: up to 24 concurrent agents
  git worktree isolation per parallel agent
  JSON-RPC 2.0 over stdio (app server protocol)
  Agent Client Protocol (ACP): open standard for agent-client

hooks (~/.codex/hooks.json or .codex/hooks.json):
  events: SessionStart, PreToolUse, PostToolUse,
          UserPromptSubmit, Stop
  requires: [features] codex_hooks = true in config.toml
  handler: script path with matcher (tool filter)
  exit codes: 0=success, 2=block
  input: JSON on stdin
  timeout: configurable (default 600s)

MCP config (.codex/config.toml):
  [[mcp_servers]]
  name = "server-name"
  type = "stdio"
  command = "/path/to/server"
  args = ["arg1"]
  env = {"KEY" = "value"}
  startup_timeout_seconds = 30
  tool_timeout_seconds = 300

  also supports: streamable_http type with bearer token

sandbox:
  linux: landlock (filesystem) + seccomp (syscalls)
  seccomp allows AF_UNIX sockets (important for mesh)
  macOS: seatbelt framework
  modes: read-only, workspace-write, danger-full-access

state:
  sessions: ~/.codex/sessions/ (JSONL)
  database: state_5.sqlite in CODEX_HOME
  config: ~/.codex/config.toml

### Gemini CLI

identity & coordination:
  subagents: exposed as tools to main agent
  local subagents: spawn as gemini-cli --yolo instances
  remote subagents: A2A protocol (REST/JSON-RPC/gRPC)
  agent cards: markdown + YAML frontmatter
  max 30 turns per subagent, 10 min timeout
  stateless workers (no presence model)

hooks (.gemini/settings.json or settings.json):
  events: SessionStart, SessionEnd, Notification,
          PreCompress, BeforeAgent, AfterAgent,
          BeforeModel, AfterModel, BeforeToolSelection,
          BeforeTool, AfterTool
  execution: synchronous, parallel by default
  optional: sequential: true (chain outputs)
  exit codes: 0=success, 2=block
  handler types: command (shell), plugin (npm package)
  plugin tag: geminicli-plugin

MCP config (.gemini/settings.json):
  "mcpServers": {
    "name": {
      "command": "path/to/server",
      "args": ["arg1"],
      "env": {"KEY": "value"}
    }
  }
  also supports: httpUrl type for remote servers

agent discovery:
  .gemini/agents/*.md (project)
  ~/.gemini/agents/*.md (user)
  /agents list, /agents reload, /agents enable/disable

state:
  sessions: ~/.gemini/tmp/<project_hash>/chats/
  config: ~/.gemini/settings.json
  context: GEMINI.md (supports @./path imports)
  extensions: ~/.gemini/extensions/

A2A protocol:
  open standard for cross-platform agent communication
  transports: REST, JSON-RPC, gRPC
  30-minute timeout for long-running tasks
  agent card endpoints for discovery

### OpenCode

identity & coordination:
  in-process agents (no subprocess spawning)
  primary agents: cycle via Tab key
  subagents: invoked via @mentions or automatically
  agents defined in .opencode/agents/*.md or config
  AgentSelfIdentityPlugin: injects identity into prompt
  AgentAttributionPlugin: tracks which agent wrote what
  hcom tool: inter-agent messaging across terminals

hooks (TS/JS plugins):
  plugin locations: ~/.config/opencode/plugins/ (global)
                    .opencode/plugins/ (project)
  hook points: tool, command, file, session, permission.ask,
               auth, compaction
  plugin format: async (context) => ({ hooks: { ... } })
  context: project, client, $, directory, worktree

MCP config (opencode.json):
  "mcp": {
    "name": {
      "type": "local",
      "command": ["path/to/server", "arg1"],
      "enabled": true
    }
  }
  also supports: "remote" type with OAuth

server architecture:
  opencode serve --port <port>
  port derived from directory path hash (10000-60000)
  multiple TUI clients connect to same server
  SSE for real-time event streaming
  ACP protocol: JSON-RPC over stdio for editor integration

state:
  database: ~/.local/share/opencode/opencode.db (SQLite)
  config: ~/.config/opencode/opencode.json (global)
          opencode.json (project)
  auth: ~/.local/share/opencode/auth.json
  agents: .opencode/agents/*.md

### Kollab (Native)

identity & coordination:
  hub mesh: peer-to-peer via unix sockets
  presence: heartbeat files in ~/.kollab/hub/presence/
  coordinator: flock() election on hub.lock
  designations: gem pool (24 names, numbered overflow)
  vaults: persistent memory per designation
  work queue: capability-matched task assignment
  open channel: all agents see all messages
  org system: JSON org charts launch entire teams

socket protocol (/tmp/kollabor-hub/{agent_id}.sock):
  transport: unix domain sockets
  format: JSON lines (newline-terminated)
  actions: message, ping, get_context, get_output,
           get_frames, subscribe, get_status, shutdown,
           roster_update
  fallback: filesystem mailbox (poll every 5s)

hooks:
  hooks.json: Claude Code compatible event names
  event bus: native hook registration (async, prioritized)
  both systems coexist

state:
  config: ~/.kollab/config.json
  presence: ~/.kollab/hub/presence/
  vaults: ~/.kollab/hub/vaults/{designation}/
  work queue: ~/.kollab/hub/work-queue.json

---

## Architecture

### Design Principle

MCP is the universal bridge. every runtime supports it.
hooks make it automatic. the mesh server translates between
MCP tool calls and the kollab hub's native socket protocol.

```
 claude code    codex cli     gemini cli    opencode     kollab
 ----------     ---------     ----------    --------     ------
     |              |              |            |            |
     | mcp          | mcp          | mcp        | mcp        | native
     | stdio        | stdio        | stdio      | stdio      | hub socket
     |              |              |            |            |
     +--- hooks ----+--- hooks ----+--- hooks --+-- plugin --+
     |              |              |            |            |
     +--------------+--------------+------------+------------+
                              |
                    +--------------------+
                    |   kollabor-mesh    |
                    |                    |
                    |  MCP server (stdio)|<--- external runtimes
                    |         +         |
                    |  hub peer (socket) |<--- kollab agents
                    |         +         |
                    |  presence manager  |
                    |  message relay     |
                    |  work queue proxy  |
                    |  vault proxy       |
                    +--------------------+
```

### Two Layers

layer 1 - MCP server (the universal translator):
  exposes mesh tools via stdio MCP transport.
  any runtime that supports MCP can call these tools.
  this is the pull-based interface (agent calls tools).

layer 2 - hooks adapters (per-runtime event relay):
  thin scripts that fire on lifecycle events.
  auto-announce on session start, auto-depart on end.
  this is the push-based interface (events trigger actions).

---

## MCP Tool Interface

### mesh_announce

Register this agent on the mesh. Called automatically by
SessionStart hook or manually by the agent.

  params:
    name: string           agent's self-identified name
    capabilities: string[] what this agent can do
    runtime: string        "claude"|"codex"|"gemini"|"opencode"|"kollab"
    project_dir: string    working directory path

  returns:
    agent_id: string       unique ID for this session
    designation: string    gem name assigned by mesh
    roster: object[]       current online agents

### mesh_roster

Get the current roster of all online agents across all runtimes.

  params: (none)

  returns:
    agents: [
      {
        designation: string    gem name
        runtime: string        which CLI
        state: string          idle|working|blocked
        capabilities: string[]
        current_task: string
        project: string
      }
    ]

### mesh_send

Send a message to another agent by designation.

  params:
    to: string             target designation ("*" for broadcast)
    content: string        message body
    scope: string          "direct"|"broadcast" (default: "direct")

  returns:
    delivered: boolean     true if delivered or queued
    queued: boolean        true if target offline, message queued

### mesh_receive

Poll for messages addressed to this agent. Returns and clears
pending messages. The agent should call this periodically or
rely on hooks for push notification.

  params:
    limit: int             max messages to return (default: 50)

  returns:
    messages: [
      {
        id: string
        from_designation: string
        from_runtime: string
        content: string
        timestamp: float
        scope: string
      }
    ]

### mesh_work_queue

Interact with the shared work queue.

  params:
    action: string         "list"|"add"|"claim"|"complete"
    task: string           (for "add") task description
    priority: int          (for "add") 1-10, higher = sooner
    capabilities: string[] (for "add") required capabilities
    slot_id: string        (for "claim"|"complete") work item ID

  returns:
    items: object[]        (for "list") pending/assigned work
    slot: object           (for "add"|"claim") the work item
    success: boolean       (for "complete")

### mesh_vault_read

Read this agent's persistent memory from the vault system.
Vaults persist across sessions, tied to designation.

  params:
    section: string        "working"|"crystallized"|"stream"
                           (default: "working")
    lines: int             (for "stream") last N entries

  returns:
    content: string        vault contents

### mesh_vault_write

Write to this agent's persistent memory.

  params:
    content: string        what to remember
    section: string        "working"|"stream"|"crystallized"
                           (default: "stream")

  returns:
    success: boolean

### mesh_status

Update this agent's status on the mesh.

  params:
    state: string          "idle"|"working"|"blocked"|"thinking"
    current_task: string   short description of current work

  returns:
    ack: boolean

### mesh_goodbye

Deregister from the mesh. Called automatically by SessionEnd hook.

  params:
    reason: string         optional departure reason

  returns:
    vault_saved: boolean   whether vault was persisted

---

## Hooks Adapters

### Purpose

MCP tools are pull-based (agent must call them). Hooks make
mesh participation automatic by pushing lifecycle events.

Each runtime gets a thin adapter script that:
  1. receives hook event JSON on stdin
  2. calls the mesh server's internal API
  3. exits 0

### Claude Code Adapter

install location: .claude/settings.json (project) or
                  ~/.claude/settings.json (global)

config:
  {
    "mcpServers": {
      "kollabor-mesh": {
        "command": "kollabor-mesh",
        "args": ["serve"]
      }
    },
    "hooks": {
      "SessionStart": [{
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "kollabor-mesh hook session-start"
        }]
      }],
      "SessionEnd": [{
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "kollabor-mesh hook session-end"
        }]
      }],
      "UserPromptSubmit": [{
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "kollabor-mesh hook user-input"
        }]
      }]
    }
  }

behavior:
  SessionStart -> mesh_announce (auto-register)
  SessionEnd -> mesh_goodbye (auto-depart)
  UserPromptSubmit -> broadcast user input to mesh

### Codex Adapter

install location: .codex/hooks.json or ~/.codex/hooks.json
requires: [features] codex_hooks = true in config.toml

hooks.json:
  {
    "events": {
      "SessionStart": [{
        "matcher": {},
        "handler": "kollabor-mesh hook session-start"
      }],
      "Stop": [{
        "matcher": {},
        "handler": "kollabor-mesh hook session-end"
      }],
      "UserPromptSubmit": [{
        "matcher": {},
        "handler": "kollabor-mesh hook user-input"
      }]
    }
  }

config.toml addition:
  [[mcp_servers]]
  name = "kollabor-mesh"
  type = "stdio"
  command = "kollabor-mesh"
  args = ["serve"]

  [features]
  codex_hooks = true

note: codex's seccomp rules explicitly allow AF_UNIX sockets.
MCP stdio transport is stdin/stdout, completely unaffected by
sandbox. mesh server runs outside the sandbox.

### Gemini CLI Adapter

install location: .gemini/settings.json or ~/.gemini/settings.json

settings.json additions:
  {
    "mcpServers": {
      "kollabor-mesh": {
        "command": "kollabor-mesh",
        "args": ["serve"]
      }
    },
    "hooks": {
      "SessionStart": [{
        "command": "kollabor-mesh hook session-start"
      }],
      "SessionEnd": [{
        "command": "kollabor-mesh hook session-end"
      }],
      "BeforeModel": [{
        "command": "kollabor-mesh hook user-input"
      }]
    }
  }

note: gemini hooks are synchronous. the mesh hook scripts must
be fast (<1s) to avoid blocking the agent.

### OpenCode Adapter

install location: .opencode/plugins/kollabor-mesh.ts or
                  ~/.config/opencode/plugins/kollabor-mesh.ts

plugin:
  import { exec } from "child_process";

  export default async (context) => ({
    hooks: {
      session: {
        start: async () => {
          exec("kollabor-mesh hook session-start");
        },
        end: async () => {
          exec("kollabor-mesh hook session-end");
        }
      }
    }
  });

opencode.json addition:
  {
    "mcp": {
      "kollabor-mesh": {
        "type": "local",
        "command": ["kollabor-mesh", "serve"]
      }
    },
    "plugins": ["file:.opencode/plugins/kollabor-mesh.ts"]
  }

### Kollab (Native Integration)

no adapter needed. the mesh server joins the hub mesh as a
native peer. kollab agents communicate via unix sockets
directly. the mesh server acts as a relay for external agents.

when hub is enabled and mesh is configured, the hub plugin
auto-starts the mesh server as a background task.

config.json addition:
  {
    "plugins": {
      "hub": {
        "mesh_enabled": true,
        "mesh_designation": "nexus"
      }
    }
  }

### Installer CLI

for convenience, the mesh ships with an installer that writes
the correct config for each runtime:

  kollabor-mesh install --runtime claude
  kollabor-mesh install --runtime codex
  kollabor-mesh install --runtime gemini
  kollabor-mesh install --runtime opencode
  kollabor-mesh install --runtime all

  flags:
    --project    write to project config (default)
    --global     write to user-level config
    --dry-run    show what would be written

the installer:
  1. detects existing config file location
  2. merges MCP server entry (doesn't overwrite existing)
  3. adds hook entries
  4. prints summary of changes

---

## Mesh Server Internals

### Process Model

the MCP server and hub peer run in the same process.

  +-------------------------------------------------+
  |  kollabor-mesh process                          |
  |                                                 |
  |  +------------+  +------------+  +------------+ |
  |  | MCP stdio  |  | hub socket |  | presence   | |
  |  | server     |  | client     |  | manager    | |
  |  |            |  |            |  |            | |
  |  | one per    |  | one shared |  | writes     | |
  |  | connected  |  | connection |  | heartbeats | |
  |  | runtime    |  | to hub     |  | for all    | |
  |  | session    |  | mesh       |  | external   | |
  |  +-----+------+  +-----+------+  +-----+------+ |
  |        |               |               |        |
  |  +-----+---------------+---------------+------+ |
  |  |           unified state                     | |
  |  |                                             | |
  |  |  roster: all agents, all runtimes           | |
  |  |  message_queue: per-agent pending msgs      | |
  |  |  vault_store: per-designation persistence   | |
  |  |  work_queue: shared task queue              | |
  |  +---------------------------------------------+ |
  +-------------------------------------------------+

### MCP stdio server (per-session)

each external CLI session starts its own MCP server process
(this is how MCP works -- the CLI spawns the server as a
subprocess). but each mesh server process connects to the
same shared backend:

  option A: shared unix socket
    a long-running mesh daemon listens on
    /tmp/kollabor-mesh/mesh.sock
    each MCP stdio server process is a thin proxy that
    forwards tool calls to the daemon.
    daemon manages all state.

  option B: shared filesystem (simpler for MVP)
    state stored in ~/.kollab/mesh/
    roster.json, messages/, vaults/
    each MCP process reads/writes with file locking.
    no daemon needed. mesh state is eventually consistent.

  option C: in-process with kollab hub
    mesh server runs inside the kollab process.
    MCP stdio servers proxy to it via unix socket.
    most integrated but requires kollab running.

recommended: option A for standalone, option C when kollab
is running. option B as fallback.

### Hub Integration

when a kollab instance is running with hub enabled:

  1. mesh server joins hub as designation "nexus"
  2. mesh server has a socket at /tmp/kollabor-hub/{id}.sock
  3. kollab agents discover "nexus" via normal presence scan
  4. messages from external agents are relayed through nexus
  5. messages to external agents are caught by nexus and queued

when no kollab instance is running:

  1. mesh server operates standalone
  2. external agents communicate via MCP tools only
  3. roster and messages stored in filesystem
  4. when kollab starts, it discovers mesh state and syncs

### Roster Management

unified roster combines:
  - kollab hub presence files (native agents)
  - external agent registrations (via mesh_announce)

each roster entry:
  {
    agent_id: string        unique session ID
    designation: string     gem name (from pool or agent name)
    runtime: string         "claude"|"codex"|"gemini"|"opencode"|"kollab"
    name: string            self-reported agent name
    state: string           idle|working|blocked|thinking
    capabilities: string[]  what agent can do
    current_task: string    what agent is doing now
    project: string         working directory
    started_at: float       unix timestamp
    last_seen: float        last activity timestamp
  }

designation assignment:
  - kollab agents: assigned by hub coordinator (existing logic)
  - external agents: assigned by mesh server from same gem pool
  - agent name preference: if agent name matches a gem name, use it
  - collision handling: numbered variants (peridot-2, peridot-3)
  - designation persists across sessions (keyed by name + runtime)

### Message Relay

external -> kollab:
  1. external agent calls mesh_send(to="peridot", content="...")
  2. MCP server receives tool call
  3. looks up peridot in unified roster
  4. peridot is kollab agent -> send via unix socket
  5. standard HubMessage format, from_designation = sender's gem
  6. peridot sees it as a normal hub message

external -> external:
  1. agent A calls mesh_send(to="sapphire", content="...")
  2. MCP server queues message for sapphire
  3. sapphire's next mesh_receive call returns the message
  4. OR sapphire's hooks adapter pushes a notification

kollab -> external:
  1. kollab agent sends hub message to external agent
  2. hub delivers to nexus (mesh server) via socket
  3. mesh server queues for target external agent
  4. target's next mesh_receive returns the message

broadcast:
  1. any agent sends scope="broadcast"
  2. mesh server fans out to:
     - all kollab agents via hub broadcast
     - all external agents via message queue
  3. open channel model preserved across runtimes

### Vault Proxy

external agents get persistent memory through the mesh:

  storage: ~/.kollab/hub/vaults/{designation}/
    stream.jsonl        append-only event log
    working_memory.md   rolling context
    crystallized.md     long-term knowledge

  mesh_vault_write appends to the appropriate file.
  mesh_vault_read returns the contents.

  vault is keyed by designation, not session ID. this means:
    - agent "alice" (claude code) gets designation "topaz"
    - alice's session ends
    - next day, alice reconnects, gets "topaz" again
    - mesh_vault_read returns yesterday's working memory
    - alice has persistent memory across sessions

  designation persistence: mesh stores a mapping of
  {name + runtime} -> designation in
  ~/.kollab/mesh/designation_map.json
  same agent always gets same designation.

### Work Queue Proxy

the mesh exposes the kollab WorkSlot system to external agents:

  mesh_work_queue("list") -> returns pending WorkSlots
  mesh_work_queue("add", task=...) -> creates new WorkSlot
  mesh_work_queue("claim") -> claims best-matched WorkSlot
  mesh_work_queue("complete", slot_id=...) -> marks complete

  capability matching works across runtimes:
    kollab coordinator scores all agents (native + external)
    external agent capabilities come from mesh_announce
    best match wins regardless of runtime

---

## Scenarios

### Scenario 1: Mixed-Runtime Team

  user launches kollab --agent jarvis (hub coordinator)
  opens 3 claude code sessions with mesh MCP configured
  opens 2 codex sessions with mesh MCP configured

  on startup:
    jarvis joins hub as coordinator (designation: jarvis)
    mesh server starts as hub peer (designation: nexus)
    cc sessions trigger SessionStart -> mesh_announce
    codex sessions trigger SessionStart -> mesh_announce
    all 5 external agents get gem designations

  jarvis sees roster:
    jarvis*     kollab    coordinator, idle
    nexus       kollab    mesh bridge
    topaz       claude    idle
    lapis       claude    idle
    sapphire    claude    idle
    peridot     codex     idle
    bismuth     codex     idle

  jarvis adds work items via hub queue:
    "implement auth module" (capabilities: [code, backend])
    "write frontend tests" (capabilities: [code, test, frontend])
    "review PR #42"        (capabilities: [review])

  coordinator assigns by capability match.
  codex agents claim backend tasks.
  claude code agents claim frontend tasks.
  everyone sees assignment messages on the open channel.

### Scenario 2: Agent Joins Mid-Session

  team is running. user opens gemini cli with mesh configured.

  gemini SessionStart hook fires:
    mesh_announce(name="gemini-1", runtime="gemini",
                  capabilities=["code", "security"])

  mesh assigns designation "garnet"
  broadcasts: "garnet (gemini) has come online"

  jarvis sees garnet on roster, sends:
    "garnet: we have a security review pending, want it?"

  garnet calls mesh_receive, gets jarvis's message.
  garnet calls mesh_work_queue("claim") for the security task.
  garnet works on it, calls mesh_status(state="working").
  garvis sees garnet's status change on roster.

### Scenario 3: Persistent Memory Across Runtimes

  day 1: codex agent "bismuth" works on auth module.
  calls mesh_vault_write("auth module uses JWT, refresh
  tokens stored in redis, session TTL is 24h").
  session ends. mesh_goodbye fires. vault saved.

  day 2: claude code agent reconnects, gets "bismuth" again
  (same name + runtime = same designation).
  calls mesh_vault_read, gets yesterday's notes.
  continues work with full context. no lost knowledge.

### Scenario 4: Kollab Feed Shows Everything

  user runs /hub feed in kollab.
  the feed shows messages from all runtimes:

    [topaz/claude]    started working on login page
    [peridot/codex]   auth module tests passing
    [garnet/gemini]   security review: no issues found
    [jarvis/kollab]   nice work team, moving to phase 2
    [user -> jarvis] ship it

  one unified view. one open channel. all runtimes.

### Scenario 5: Standalone Mode (No Kollab Running)

  user doesn't use kollab. just claude code and codex.
  installs kollabor-mesh via pip install kollabor-mesh.
  runs: kollabor-mesh install --runtime claude --runtime codex

  mesh daemon starts on first agent connect.
  all MCP tools work. roster, messaging, vaults, work queue.
  no kollab required. mesh operates independently.

  later, if user starts kollab, mesh auto-connects to hub.
  existing external agents appear on kollab's roster.

---

## Edge Cases

### Multiple Mesh Servers

if two kollab instances both try to start mesh servers,
they'd conflict. solution: mesh daemon uses flock() on
~/.kollab/mesh/mesh.lock (same pattern as hub
coordinator). first one wins, subsequent MCP stdio servers
connect to the existing daemon as clients.

### MCP Server Lifecycle

each CLI session spawns its own MCP server process. the mesh
handles this by making the MCP stdio server a thin proxy:

  CLI session starts -> spawns "kollabor-mesh serve" (MCP)
  MCP process connects to mesh daemon via unix socket
  tool calls forwarded to daemon, responses forwarded back
  CLI session ends -> MCP process exits
  daemon keeps running (serves other sessions)

daemon auto-exits after last client disconnects + idle timeout
(configurable, default 5 minutes).

### Message Ordering

MCP is request/response, not streaming. messages queue in
FIFO order per-agent. mesh_receive returns them in order.
for time-sensitive coordination, hooks push notifications
that trigger the agent to call mesh_receive.

### Agent Naming Collisions

two agents from different runtimes could have the same name.
designations are unique (gem pool). the mesh assigns
designations deterministically:

  {name}-{runtime} -> consistent designation
  "alice"-"claude" -> always gets "topaz"
  "alice"-"codex"  -> always gets "lapis"

mapping stored in ~/.kollab/mesh/designation_map.json.

### Codex Sandbox Compatibility

codex's seccomp filter blocks network syscalls but explicitly
allows AF_UNIX. MCP stdio transport uses stdin/stdout pipes,
completely unaffected by sandbox. the mesh server process runs
outside the sandbox (it's the parent, not the child). no
compatibility issues.

### Gemini Hook Latency

gemini hooks are synchronous -- the CLI blocks until hooks
complete. mesh hook scripts must be fast. the session-start
hook just writes a registration to a unix socket (<10ms).
no blocking on mesh daemon response needed for announce.

### OpenCode In-Process Agents

opencode runs agents in-process (no subprocess spawning).
all agents in an opencode session share one MCP connection.
the mesh sees this as one agent (the opencode session) not
individual sub-agents. opencode's hcom tool can be used
alongside mesh tools for internal coordination.

### Offline Agents

when an external agent goes offline without calling
mesh_goodbye (crash, kill -9, network drop):

  detection: mesh daemon pings registered agents every 30s
  via MCP server liveness check (process alive?).
  if unreachable for 60s, marked offline.
  messages queued (not dropped) for 24h.
  if agent reconnects with same name+runtime, queued
  messages delivered on first mesh_receive.

### Hot Reload

if the mesh daemon restarts (crash, upgrade):
  state persisted to ~/.kollab/mesh/ (filesystem)
  on restart, roster rebuilt from:
    - kollab hub presence files (native agents)
    - designation_map.json (external agent mappings)
  external agents re-announce on next tool call.
  message queues restored from filesystem.

---

## Implementation Phases

### Phase 1: MCP Server Core (~500 lines python)

deliverables:
  kollabor-mesh serve      start MCP stdio server
  kollabor-mesh daemon     start background mesh daemon

MCP tools implemented:
  mesh_announce, mesh_roster, mesh_send, mesh_receive,
  mesh_status, mesh_goodbye

architecture:
  daemon: asyncio server on unix socket
  MCP stdio: thin proxy that forwards to daemon
  state: in-memory roster + message queues
  persistence: JSON files in ~/.kollab/mesh/

test with:
  claude code (most familiar, hooks well-documented)
  two cc sessions messaging each other through mesh

### Phase 2: Hub Integration (~200 lines)

deliverables:
  mesh daemon joins kollab hub as peer
  kollab agents see external agents on roster
  external agents see kollab agents via mesh_roster
  bidirectional message relay

changes:
  mesh daemon creates unix socket in /tmp/kollabor-hub/
  mesh daemon writes presence file
  mesh daemon responds to hub socket actions
  mesh daemon translates between MCP calls and hub messages

test with:
  kollab agent + claude code agent messaging each other

### Phase 3: Hooks Adapters (~100 lines per runtime)

deliverables:
  kollabor-mesh hook session-start
  kollabor-mesh hook session-end
  kollabor-mesh hook user-input
  kollabor-mesh install --runtime <name>

adapter scripts for:
  claude code, codex, gemini cli, opencode

test with:
  install on each runtime, verify auto-announce works

### Phase 4: Vault + Work Queue (~300 lines)

deliverables:
  mesh_vault_read, mesh_vault_write
  mesh_work_queue (list, add, claim, complete)
  designation persistence (name+runtime -> gem mapping)

backed by:
  kollab vault system (stream.jsonl, working_memory.md, etc)
  kollab WorkSlot system (work-queue.json)

test with:
  agent writes to vault, disconnects, reconnects, reads back
  work item created by kollab, claimed by codex agent

### Phase 5: Packaging + Polish

deliverables:
  pip install kollabor-mesh     standalone package
  npx kollabor-mesh             for TS-native runtimes
  kollab hub config option       auto-start mesh with hub
  /hub mesh status command       show mesh state in kollab

documentation:
  README with quickstart per runtime
  config examples for each CLI

---

## File Structure

```
packages/kollabor-mesh/              standalone package
  src/kollabor_mesh/
    __init__.py
    cli.py                           CLI entry point
    daemon.py                        mesh daemon (asyncio)
    mcp_server.py                    MCP stdio proxy
    roster.py                        unified roster management
    relay.py                         message relay logic
    vault_proxy.py                   vault read/write proxy
    work_queue_proxy.py              work queue proxy
    hub_bridge.py                    kollab hub integration
    hooks/
      session_start.py               hook script
      session_end.py                 hook script
      user_input.py                  hook script
    installer/
      claude.py                      cc config writer
      codex.py                       codex config writer
      gemini.py                      gemini config writer
      opencode.py                    opencode config writer
    models.py                        MeshAgent, MeshMessage, etc
    config.py                        mesh configuration
    persistence.py                   filesystem state management

runtime state:
  ~/.kollab/mesh/
    mesh.lock                        daemon flock
    mesh.sock                        daemon unix socket
    roster.json                      current roster snapshot
    designation_map.json             name+runtime -> designation
    messages/                        per-agent message queues
      {designation}/
        pending.jsonl                queued messages
```

---

## What Dies (After Full Adoption)

nothing. the mesh is purely additive. it doesn't replace any
runtime's native coordination. it bridges them.

kollab hub keeps working exactly as it does today.
claude code teams keep working.
codex parallel agents keep working.
gemini subagents keep working.
opencode in-process agents keep working.

the mesh is an overlay network. agents participate in BOTH
their native system AND the mesh simultaneously.

## What This Enables (End State)

```
                    +------------------+
                    |  kollabor mobile |
                    |  (iOS/Android)   |
                    +--------+---------+
                             | WebSocket
                             v
+--------------+    +------------------+    +------------------+
|   mentiko    |--->| kollabor-engine  |<---|  notification    |
|  control     |    |   (REST API)     |    |  channels        |
|  plane       |    +--------+---------+    +------------------+
+--------------+             |
                             | reads mesh state
                             v
                +------------------------+
                |     kollabor mesh      |
                |                        |
                |  kollab agents         |
                |  claude code agents    |
                |  codex agents          |
                |  gemini cli agents     |
                |  opencode agents       |
                |  [future runtimes]     |
                |                        |
                |  unified:              |
                |    roster              |
                |    messaging           |
                |    work queue          |
                |    vaults              |
                |    open channel        |
                +------------------------+
```

any agent, any runtime, one mesh. persistent memory for all.
work coordination across runtimes. open channel visibility.
and when a new CLI shows up -- just add an MCP server entry
and a hooks adapter. the mesh grows automatically.
