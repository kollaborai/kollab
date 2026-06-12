# Hub Runtime Split PRD

## Objective

Separate hub lifecycle/runtime concerns from command/tool handlers without
changing public behavior.

## User Impact

Future hub changes should stop requiring archaeology through one giant plugin
file, while existing commands keep working.

## Scope

- Work in small extraction steps under `plugins/hub/`.
- Split socket, RPC, presence, heartbeat, coordinator startup, and daemon-ready
  signaling from command handlers and XML/native tool handlers.
- Preserve public command behavior.
- Keep the split behind tests; do not rewrite the hub.
- Do not suppress agent join/idle visibility.

## Acceptance Criteria

- Startup invariant proves socket, RPC handlers, presence, state handlers, and
  daemon-ready signaling happen in correct order.
- Extracted modules have focused ownership and are imported by the existing
  plugin.
- Existing hub regression suite remains green.
- Public `/hub` commands behave unchanged.

## Validation

- Run hub unit tests, hub RPC integration, and stabilization gate.
- Include a before/after note listing moved responsibilities.

## Deliverable

Open a PR from `codex/hub-runtime-split` to `main`. If the full split is too
large, open the smallest safe extraction PR and document the next slice.
