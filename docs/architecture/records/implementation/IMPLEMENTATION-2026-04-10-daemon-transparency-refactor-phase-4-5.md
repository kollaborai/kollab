---
title: "Daemon Transparency Refactor Implementation Record"
doc_type: architecture-implementation-record
created: 2026-04-10
modified: 2026-05-04
status: historical
phase: "4.5"
---
# Daemon Transparency Refactor Implementation Record

## Summary

Phase 4.5 improved attach-mode consistency by making launch flags and command
state flow across the attach-client to daemon boundary. The work addressed cases
where the attach client displayed or mutated local shadow state instead of the
daemon's active profile, agent, skills, MCP state, permissions, or conversation
context.

This record summarizes the implementation and validation. It is historical
context for the attach-mode architecture, not an active work plan.

## Objectives

- Make `kollab --attach <agent> --profile <name>` and related launch flags affect
  daemon state reliably.
- Ensure attach-mode status widgets display daemon state instead of client-local
  fallbacks.
- Route core slash-command mutations through a shared state service abstraction.
- Add scoped multi-context support without rewriting the entire conversation
  hot path.
- Improve smoke coverage for local and attach-mode state transitions.

## Delivered Changes

### Attach-Mode State Display

- Updated status widgets to prefer remote state when available.
- Prevented attach clients from registering local state services that could point
  commands at empty local manager instances.
- Delayed launch-flag draining until the remote event reader was active so RPC
  replies could be received.

### State Service RPC Surface

Added state service methods for:

- agent selection and clearing
- skill activation and deactivation
- system prompt updates
- session restart
- MCP enable, disable, test, and tool listing
- session and project permission operations
- one-shot conversation resume by ID
- status modal snapshots

Commands migrated through this surface return clear errors when the state service
is unavailable instead of silently falling back to direct local manager access.

### Multi-Context Daemon Support

- Added `ConversationContext` as the context data transfer object.
- Added `ContextRegistry` for context creation, listing, attachment, archival,
  and active-context lookup.
- Added the `--context` launch flag.
- Preserved `conversation_history` list identity during context switches by
  replacing list contents in place with `clear()` and `extend()`. This avoids
  breaking cached references held by queue processing, session management, and
  hub coordination code.

### Command Migration and Audit

- Migrated core command paths for agent, skill, restart, MCP, permissions,
  status, and resume behavior.
- Added an audit of plugin command surfaces that still needed attach-aware state
  or streaming transport.
- Added low-risk read-only RPCs for hub status, identity, and work summaries.

### Thin-Client Attach Path

- Ensured attach mode registers `RemoteStateService` before command execution.
- Skipped local-only skill injection during attach startup.
- Kept profile, agent, and LLM service construction in attach mode where command
  registration and status fallbacks still depended on them.

## Architecture Impact

- State mutation in attach mode moved from local object access to RPC-backed
  service calls.
- The attach client became closer to a thin client, although complete removal of
  local manager construction was deferred.
- Multi-context support introduced a scoped registry model while preserving
  compatibility with existing cached conversation-history references.
- Command implementations became more explicit about whether daemon state is
  required.

## Validation

Validation included unit tests and tmux smoke tests for both local-mode and
attach-mode behavior:

- `ContextRegistry` unit coverage
- local-mode JSON smoke specs for status, permissions, MCP, and restart
- attach-mode shell smoke scripts for launch flags and context behavior
- focused regression coverage for conversation-history list identity
- plugin command migration audit with prioritized follow-up items

## Known Limitations

- Full thin-client mode was deferred; attach mode still constructs some local
  managers for command registration and fallback display paths.
- Several `/hub`, `/terminal`, `/sub`, and `/resume` subpaths required later
  attach-aware transport or UI work.
- MCP hot reload on config change remained a restart-oriented workflow.
- A full hot-path rewrite for context isolation was intentionally deferred.

## Follow-Up Work

- Complete the thin-client refactor by moving more command registration metadata
  to the daemon handshake.
- Add attach-aware streaming transport for terminal and hub views.
- Finish the deferred `/resume` modal, search, branch, and filter paths.
- Expand attach-mode smoke coverage for hub messaging and terminal streaming.
- Revisit the larger multi-context hot-path rewrite if the scoped registry model
  becomes insufficient.

## Related Documents

- [Attach-mode rendering reference](../../reference/attach-mode-rendering-pipeline-reference.md)
- [Plugin command migration audit](../audits/AUDIT-2026-04-10-plugin-command-migration-phase-4-5-step-8.md)
- [Context service RFC](../../rfcs/RFC-2026-04-11-context-service.md)
