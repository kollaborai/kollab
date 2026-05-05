---
title: "Daemon-First Architecture Implementation Record"
doc_type: implementation-record
created: 2026-04-08
modified: 2026-05-04
status: historical
---
# Daemon-First Architecture Implementation Record

This record summarizes the daemon-first runtime rollout that changed interactive
`kollab` sessions from single-process terminal sessions into attach clients
connected to a background agent daemon.

## Summary

Kollabor now starts interactive sessions through a daemon-first architecture. By
default, the CLI forks a background daemon, waits for the hub socket to become
ready, and then attaches the terminal UI as a client. The agent can continue
running after the terminal detaches, while explicit shutdown still terminates the
owned daemon.

## User-Facing Behavior

- `kollab` starts a daemon-backed interactive session by default.
- `Ctrl+Z` detaches the terminal client while leaving the daemon running.
- `Ctrl+C` exits the owner session and terminates the owned daemon.
- `kollab --attach <identity>` reconnects to a running agent.
- `kollab --no-daemon` preserves the legacy single-process mode for debugging.

## Implementation Scope

Key files involved in the rollout:

- `kollabor/daemon.py` - daemon fork and readiness signaling
- `kollabor/cli.py` - daemon launch flow and `--no-daemon` handling
- `kollabor/application.py` - attach-client behavior and detach/kill semantics
- `plugins/hub/plugin.py` - daemon readiness, socket identity, and attach support
- `plugins/hub/messenger.py` - attach acknowledgment payloads
- `packages/kollabor-tui/config_widgets.py` - configuration modal visibility fixes
- `docs/specs/daemon-first-architecture.md` - detailed design spec
- `docs/specs/hub-coordinator-routing.md` - coordinator routing spec
- `docs/specs/secrets-management.md` - related token/key management design

## Architecture Notes

The daemon startup path follows this sequence:

1. The CLI starts from `kollab`.
2. The parent process creates a readiness pipe and forks.
3. The child process starts the full application headlessly.
4. The hub plugin starts its socket server and writes the socket path to the
   readiness pipe.
5. The parent resolves the daemon identity from the socket path and enters attach
   mode.
6. The attach client renders locally while forwarding input to the daemon.
7. Detach clears the owner daemon marker and exits the client.
8. Owner shutdown sends a clean termination signal to the daemon.

## Related Work

The rollout also included:

- untagged response routing from non-coordinator agents to the coordinator
- configuration modal fixes for plugin settings visibility
- display queue audit notes for streaming and alternate-buffer behavior

## Follow-Up Verification

Recommended verification for future daemon/runtime changes:

- start `kollab` and confirm a daemon-backed TUI appears
- detach with `Ctrl+Z`, reattach, and confirm output catch-up
- terminate with `Ctrl+C` from the owner session and confirm clean daemon exit
- run `kollab --no-daemon` and confirm legacy mode still works
- verify hub identity/status widgets in attach mode
- verify `/config` includes hub and related plugin settings
