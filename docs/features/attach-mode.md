---
title: "Attach Mode"
created: 2026-04-10
modified: 2026-04-10
status: active
---
# Attach Mode

## Overview

Attach mode enables a daemon-first architecture: run a single kollab daemon in the background, then attach multiple TUI clients to it. The daemon owns all state (conversation history, profile, agent, active skills). Clients are thin -- they render UI, forward input, and display responses.

This solves two problems:
- Resource sharing: multiple terminals attach to the same conversation without re-syncing
- Background execution: long-running agents stay alive without an active TUI session

The state_service abstraction makes commands work identically in local mode and attach mode. Code depends on the StateService protocol, never on a specific implementation.

## Canonical Attach Path

The canonical attach implementation is the in-app proxy in
`TerminalLLMChat._initialize_attach_proxy()` (`kollabor/application.py`). The
CLI parses `--attach <identity>`, passes that identity into `TerminalLLMChat`,
and the application owns the socket handshake, local TUI, input forwarding,
RemoteStateService RPC client, permission bridge, heartbeat tracking, pending
RPC visibility, widget state updates, Ctrl+Z detach, and Ctrl+C owned-daemon
shutdown semantics.

Do not add new behavior to `kollabor.attach_client.AttachClient`. That module is
the legacy standalone terminal mirror and now emits a `DeprecationWarning` when
constructed directly. The old `_handle_cli_attach` short-circuit path has been
removed, so future attach work should extend the in-app proxy and its
StateService/RPC boundary instead.

## Quick Start

Three-command happy path:

```bash
# 1. Start a detached daemon
kollab --detached --agent tech-dude --profile openai-oauth

# 2. Check daemon status from any terminal
kollab --hub status

# 3. Attach with a TUI (the daemon picked a gem name like koordinator)
kollab --attach koordinator
```

Detach with Ctrl+Z (daemon keeps running). Reattach anytime with --attach.

## Launch Flags

These flags cross the attach-client -> daemon boundary via RPC (kollabor/application.py:173-209):

```
--profile <name>      Switch LLM profile on daemon
--agent <name>        Switch active agent on daemon
--skill <name>        Load a skill onto active agent
--system-prompt <path> Install custom system prompt
--context <name>      Attach to named conversation context
--save                Persist profile choice to config
--local               With --save, persist to project config not global
```

In attach mode, these flags are stashed in _attach_pending_flags during init, then drained as RPC calls after RemoteStateService is wired up (kollabor/application.py:1436-1442). This prevents the bug where the client's shadow state diverges from the daemon.

Example:

```bash
kollab --attach koordinator --profile openai-oauth --skill code-review
# daemon switches profile + loads skill, client reflects the change
```

## The State Service

StateService is the unified abstraction for reading/writing daemon state (kollabor/state/interface.py).

Two implementations:
- LocalStateService: in-process direct access (local mode)
- RemoteStateService: RPC wrapper around daemon's LocalStateService (attach mode)

Commands and widgets use the protocol, never the implementation. This is why /profile set, /agent set, /skills load, /permissions, /mcp, /resume, /restart all work in both modes without code changes.

### StateService Methods

Read methods (phase 2-4.5):
- get_conversation, save_conversation
- get_session_stats, get_active_profile, list_profiles
- get_permission_state, get_mcp_state, get_hub_state
- get_processing_state, get_system_info
- get_active_agent, list_agents, list_skills
- get_system_prompt, list_contexts, get_active_context

Write methods (phase 4.5):
- set_active_profile, set_agent, clear_agent
- activate_skill, deactivate_skill, set_system_prompt
- set_approval_mode, restart_session
- enable_mcp_server, disable_mcp_server
- clear_session_approvals, clear_project_approvals
- create_context, attach_to_context, archive_context
- resume_conversation

RPC handlers are registered on the daemon at kollabor/state/handlers.py:24-77.

## Multi-Context Support

The --context flag enables conversation switching on a single daemon.

ConversationContext (kollabor/state/context.py) holds:
- name: context identifier
- profile_name, agent_name, system_prompt: LLM configuration
- conversation_history: full message list

ContextRegistry uses snapshot-and-swap: when switching contexts, it snapshots the live conversation_history back to the registry, then loads the target context's history via list.clear() + list.extend(). This preserves the list identity so cached references in QueueProcessor, SessionManager, and hub plugin stay valid. See the [daemon transparency implementation record](../architecture/records/implementation/IMPLEMENTATION-2026-04-10-daemon-transparency-refactor-phase-4-5.md) for historical context.

Contexts persist at ~/.kollab/hub/contexts/<name>.json.

```bash
# Create a context for debugging work
kollab --attach my-daemon --context debug-session

# Switch contexts mid-conversation
/context backend-api
# all state (profile, agent, history) swaps instantly
```

Context RPCs: list_contexts, get_active_context, create_context, attach_to_context, archive_context (interface.py:300-363).

## Hub CLI

The --hub flag enables daemon management without attaching a TUI. These work against the daemon's hub plugin state via filesystem reads (presence files, vault files).

Subcommands (kollabor/cli.py:386-395, verify with _print_hub_help at line 1256):

```
kollab --hub status       show agent count, per-agent line, work queue
kollab --hub whoami       show this agent's identity, role, pid
kollab --hub work         show pending work queue
kollab --hub agents       list all discovered agents
kollab --hub stop <name>  stop a specific agent
kollab --hub stop all     stop every agent
kollab --hub capture <name> [lines]  grab last N lines from agent's stream
kollab --hub msg <name> <text>      send direct message to an agent
kollab --hub broadcast <text>       send message to all agents
kollab --hub user [name]            set/show hub username
kollab --hub on                    enable hub plugin
kollab --hub off                   disable hub plugin
kollab --hub org <name> [mission]  launch an organization from JSON chart
```

Presence files live at ~/.kollab/hub/presence/<agent_id>.json (plugins/hub/presence.py:23-27). Agents announce themselves via heartbeat; discovery scans this directory.

## Detaching and Reattaching

Ctrl+Z detaches from the daemon without killing it (kollabor/application.py:1488-1516):

1. Client sends {"type": "detach"} over the socket
2. Daemon cleans up the subscriber
3. Client closes socket and prints reattach instructions
4. Daemon continues running, preserving all state

Reattach with the same identity:

```bash
kollab --attach jarvis
# picks up exactly where you left off
```

State persists across attach cycles: conversation history, active profile, loaded skills, hub designation, work queue.

## Known Gaps

Deferred to phase 4.6 (`docs/architecture/records/audits/AUDIT-2026-04-10-plugin-command-migration-phase-4-5-step-8.md:259-267`):

- /login OAuth browser split: client runs browser, daemon stores token
- /hub msg, broadcast, stop, spawn, org: cross-process messaging via RPC
- /terminal view, attach: needs streaming transport for live session output
- /sub completion notification: MessageInjector rewrite
- /resume modal, search, branch, filter: session management UI
- MCP hot-reload on config change: restart message is current UX

Read-only queries that work in attach mode (phase 4.5 step 8):
- state.get_hub_status_text, state.get_hub_whoami_text, state.get_hub_work_text

## Troubleshooting

Common failure modes with grep-able error strings:

"cannot connect to <identity>: Connection refused"
- Daemon not running or socket path mismatch
- Check ~/.kollab/hub/presence/ for agent presence files
- Verify socket_dir exists: /tmp/kollabor-hub/

"attach failed: unexpected response"
- Daemon received attach request but didn't send attach_ack
- Check daemon logs for errors during attach handshake

"State service not available"
- Command tried to use state_service but it's not registered
- Verify RemoteStateService is registered in attach mode (application.py:1416-1434)

"RPC timeout after 10s"
- Drain ordering issue: RPC calls made before _read_remote_events loop is active
- Ensure drain runs AFTER the attach event reader is scheduled (application.py:1436)

"Permission denied" on socket
- Socket file has wrong permissions
- Remove stale sockets: rm /tmp/kollabor-hub/sock_*

"conversation_history identity changed"
- Direct list assignment broke cached references
- Use _replace_conversation_history helper (clear + extend)
- See resume_conversation_plugin.py:360, 943, 988 for fixes
