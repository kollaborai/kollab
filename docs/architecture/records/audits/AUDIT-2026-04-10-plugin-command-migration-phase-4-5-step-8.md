---
title: "Phase 4.5 Step 8 Plugin Command Audit"
doc_type: architecture-audit
created: 2026-04-10
modified: 2026-04-10
status: historical
---
# Phase 4.5 Step 8 — Plugin Command Audit

Written 2026-04-10 during phase 4.5 of the daemon transparency refactor.

Phase 4.5 step 7 migrated the core slash commands (/agent, /skills,
/restart, /mcp, /permissions, /resume, /status) through a state_service
abstraction so they work identically in local and attach mode. Step 8
audits the remaining plugin commands to decide what needs migration and
what should stay client-local. This doc is the audit deliverable;
actual command migrations are scheduled for phase 4.6 unless noted
below.

## summary table

```
command              priority     scope for step 8          defer to 4.6
-------------------- -----------  ------------------------  ---------------
/save                DONE         phase 2 migrated          —
/matrix              N/A          no slash command          —
/deepthought         LOW          read-only client-side     —
/sub list/status     LOW          client-only, safe         —
/sub create/stop     LOW          subprocess i/o, not state —
/sub message         DEFER        MessageInjector rewrite   4.6
/terminal new        MEDIUM       state.create_terminal     candidate
/terminal kill       MEDIUM       state.kill_terminal       candidate
/terminal list       MEDIUM       state.list_terminals      candidate
/terminal view       SKIP         needs streaming transport 4.6
/hub status          HIGH         state.get_hub_summary     candidate
/hub whoami          HIGH         state.get_hub_identity    candidate
/hub work            HIGH         state.get_work_queue      candidate
/hub msg             HIGH-BLOCK   cross-process, big rpc    4.6
/hub broadcast       HIGH-BLOCK   cross-process, big rpc    4.6
/hub stop/spawn      HIGH-BLOCK   cross-process orchestrat. 4.6
/hub cron/tasks      MEDIUM       in-memory state           candidate
/hub feed/console    SKIP         altview modals, client ok —
/hub vault/vaults    LOW          filesystem reads          candidate
/hub on/off/user     SKIP         config preferences        —
/hub org/orgs        DEFER        spawns processes          4.6
/hub notify/bridge   SKIP         local task mgmt           —
/branch              DEFER        modal UI + session mgmt   4.6
/sessions            DEFER        modal UI                  4.6
```

## classification definitions

- **DAEMON-STATE**: mutates or reads daemon-side state (conversation_history,
  hub_plugin internals, MCP integration, process registry). NEEDS
  state_service migration to work in attach mode.
- **CLIENT-ONLY**: purely UI/display concerns — altview modals, status
  bar widgets, visual effects. SAFE as-is; no migration needed.
- **HYBRID**: client UI over daemon state. NEEDS read-only state_service
  integration but the UI layer stays on the client.

## per-plugin findings

### save_conversation plugin (/save)

Already migrated in phase 2. Verified at save_conversation_plugin.py:201-216
where it calls `state_service.save_conversation(save_format)`. Subcommands
(transcript, markdown, jsonl, clipboard, both, local) all route through
the state service.

Status: DONE, no step 8 action required.

### terminal_plugin (/terminal /term /tmux /t)

Five subcommands: new, view, list, kill, attach (view alias).

Pure state operations (new/kill/list) are candidates for state_service
migration. The shapes:

```python
# snapshots.py additions
@dataclass
class TerminalSessionSnapshot(Snapshot):
    name: str = ""
    command: str = ""
    pid: int = 0
    created_at: str = ""  # iso8601
    status: str = ""       # "alive" | "dead"

@dataclass
class TerminalStateSnapshot(Snapshot):
    sessions: list[TerminalSessionSnapshot] = field(default_factory=list)
    session_count: int = 0

# interface.py additions
async def get_terminal_state(self) -> TerminalStateSnapshot: ...
async def create_terminal_session(self, name: str, command: str) -> TerminalSessionSnapshot: ...
async def kill_terminal_session(self, name: str) -> bool: ...
```

view and attach require live streaming of the session's ring buffer to
the client — that's a separate streaming-transport problem (not a
snapshot concern). Deferred to phase 4.6.

### agent_orchestrator (/sub /sa /subagent)

Six subcommands: list, status, create, capture, stop, message.

- list / status / capture are client-only reads of the plugin's in-memory
  agent registry. Safe.
- create / stop spawn or terminate subprocesses via orchestrator. The
  subprocess handle lives on the client's local orchestrator instance,
  not in daemon state. Works as-is in local mode.
- message sends text to agent stdin via `_send_keys`. Also local.

The completion notification path uses MessageInjector which has the
known emit_with_hooks return-format bug (see
docs/blog/how-the-hub-was-born.md:55). That notification is silently
swallowed under live hooks. Deprecate MessageInjector in phase 4.6;
replace with `llm.inject_system_message` under the hub _history_lock,
same pattern as skill activation (kollabor/state/local.py:1094-1120).

### hub plugin (/hub /mesh)

30+ subcommands. The command surface covers:

- identity / roster: status, whoami, stop, spawn, agents
- messaging: msg, broadcast
- work queue: work, queue, claim
- storage: vault, vaults, tasks
- scheduling: cron add/list/delete/clear
- notifications: notify enable/disable/channel/url/threshold/test/status
- bridge: bridge status/send/enable/disable/setup
- organizations: org, orgs
- configuration: on, off, user
- UI: feed, console

The cross-process messaging bits (msg, broadcast, stop, spawn, org) are
hard. They involve:
1. Writing to AgentMessenger sockets (cross-process delivery)
2. Writing to AgentVault files (`~/.kollab/hub/vaults/<gem>/`)
3. Reading presence files (`~/.kollab/hub/presence/`)
4. Spawning child processes (org_launcher)

In attach mode, the client can't reach any of these — they're daemon-
side filesystems and sockets. A proper migration would require wrapping
each hub internal in a dedicated rpc: state.send_hub_message,
state.broadcast_hub_message, state.stop_hub_agent, etc. That's probably
10+ new rpc methods with nontrivial serialization.

The read-only queries (status, whoami, work) are cheap wins. They
mirror existing get_hub_state pattern from phase 3. Candidate shapes:

```python
# interface.py additions
async def get_hub_summary(self) -> dict[str, Any]: ...   # agents + work
async def get_hub_identity_brief(self) -> dict[str, Any]: ...  # whoami
async def get_work_queue(self) -> dict[str, Any]: ...    # work queue
```

The cron / tasks / vault reads are also cheap (in-memory + filesystem
read). Notify and bridge are per-process config preferences — keep
client-local.

### resume_conversation plugin (/resume /fork /branch /sessions)

/resume <id> was migrated in step 7. The other subcommands (modal picker,
/branch, /sessions search, filter flags) still mutate llm_service.
conversation_history locally via _replace_conversation_history which
uses the in-place list-identity-preserving pattern.

In attach mode these still don't work because the client's llm_service
is a shadow. Deferred to phase 4.6:
- /branch needs state.branch_conversation(session_id, branch_index)
- /sessions needs state.list_all_sessions() + state.search_sessions(query)
- /resume modal needs state.list_resumable_conversations(filters)

### deep_thought plugin (/deepthought)

Client-side pondering engine. Reads llm_service.conversation_history
but doesn't mutate it. Spawns parallel kollab instances via orchestrator.
In attach mode the read is stale (client shadow) — would need
state.get_conversation() for the read, but the ponder feature itself is
optional. Low priority.

### matrix plugin

No slash command registered. It's a fullscreen visual effect only.
Accessible via the fullscreen plugin framework, not the slash command
system. No action needed.

### hook_monitoring / context_compaction plugins

No slash commands. Hook into the event bus for passive monitoring.
Unaffected by the daemon transparency refactor.

## known blockers

### BROKEN: MessageInjector emit_with_hooks return format

Location: plugins/agent_orchestrator/message_injector.py:63-98
Symptom: KeyError 'content' when hooks are registered
Root cause: emit_with_hooks wraps return values; callers expect the
mutated context dict. See docs/blog/how-the-hub-was-born.md:55 for
history.

Impact: /sub completion notifications don't reach the LLM context
when hub is active. Silently swallowed.

Fix path: deprecate MessageInjector entirely. Route through
`llm.inject_system_message(body, subtype="...")` with the hub
_history_lock held, same pattern as kollabor/state/local.py:1094-1120
(skill activation).

Scheduled for phase 4.6.

### BLOCKED: hub cross-process delivery

/hub msg and /hub broadcast mutate:
- Vault stream (filesystem write)
- Messenger sockets (cross-process send)
- Hub plugin's internal _roster state

In attach mode, the client has NONE of those. Migration requires
wrapping AgentMessenger.send_to_agent + AgentVault.append_stream
behind rpc, which is a significant surface expansion. Scheduled for
phase 4.6.

### BLOCKED: terminal session streaming

/terminal view opens a live ring buffer stream to the client's UI.
Ring buffer lives in daemon memory. In attach mode, the client would
need to subscribe to a streaming rpc that pushes session output on
every write. This is a streaming-transport architectural addition,
not a snapshot concern. Scheduled for phase 4.6.

## recommendations for phase 4.6

Prioritized migration backlog derived from this audit:

1. **Hub read-only queries** (status, whoami, work, vault, vaults, tasks).
   Cheap, high user value, match existing snapshot rpc pattern. Should
   ship first in phase 4.6.

2. **Terminal state rpcs** (new, kill, list). Moderate complexity
   because TerminalSession has non-serializable fields (Popen, ring
   buffer). The rpc can return a TerminalSessionSnapshot with metadata
   only; view streaming stays client-only until the streaming
   transport lands.

3. **Hub cron/tasks** (add, delete, list). Small in-memory state that
   isn't persisted anyway. Rpc wrapper is straightforward.

4. **MessageInjector deprecation.** Replace with
   `llm.inject_system_message` under hub _history_lock. This fixes the
   /sub completion notification regression.

5. **Hub cross-process messaging** (msg, broadcast, stop). Non-trivial
   because it requires RpcServer wrappers around AgentMessenger and
   AgentVault.append_stream. Design work needed.

6. **Resume plugin modal/branch/search paths.** Requires list-all-sessions
   and search rpcs plus UI state sync. Moderate.

7. **Streaming transport for terminal view.** Requires a push-rpc
   subscription model that RpcClient doesn't currently have. This is
   architecturally the biggest item and should be scoped separately.

Everything else can stay client-local indefinitely — there's no user-
visible benefit to making /hub on/off, /hub notify, or /deepthought
route through the daemon.

## phase 4.5 step 8 delivered items

This document is the deliverable. Code changes that ship in phase 4.5
alongside this audit (if any) are limited to the read-only cheap wins
(/hub status, whoami, work — ~3 new rpcs) to validate the pattern
works for the hub plugin specifically. The remaining items deliberately
defer to phase 4.6.
