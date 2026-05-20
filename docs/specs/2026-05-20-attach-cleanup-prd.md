# Attach Cleanup PRD

## Objective

Make the in-app attach proxy the clearly canonical attach implementation and
deprecate or remove alternate attach paths only after coverage exists.

## User Impact

Attach mode should feel inspectable and boring: same commands, same status,
clear detach/interrupt behavior, no mystery state owners.

## Scope

- Inventory attach entrypoints and clients.
- Identify canonical path in `TerminalLLMChat` attach proxy.
- Add tests for profile switching, state reads, permission bridge behavior, hub
  messages, render output, Ctrl+Z detach, and Ctrl+C semantics where practical.
- Deprecate alternate path with warning or remove it only if no live references
  remain.
- Do not change hub join visibility.

## Acceptance Criteria

- There is a documented canonical attach path.
- Alternate attach path is deprecated/removed with coverage.
- Tests prove profile/model, agent, permission, heartbeat, pending RPC, hub
  messages, and status rendering in attach mode.
- Existing `/status` attach view remains correct.

## Validation

- Run attach startup, permission bridge, status command, and RPC tests.
- Run a daemon attach smoke if implementation touches runtime behavior.

## Deliverable

Open a PR from `codex/attach-cleanup` to `main` with a short migration note for
future agents.
