# Kollab Stability Plan

status: active
created: 2026-05-15

## Summary

Stabilize Kollab through narrow contract slices, not a rewrite. The goal is to
make tool calls, hub behavior, attach mode, and TUI state updates resilient when
nearby code changes.

## Phase 0 - Clean Baseline

- Commit or isolate existing dirty work before each slice.
- Run the focused regression set for the slice before editing.
- Treat any pre-existing failure as the first target, not background noise.

## Phase 1 - Tool-Call Contract Spine

- Shared native tool-call normalization lives in `kollabor_agent`.
- Engine and TUI native tool paths must use the same normalization contract.
- Advertised registry built-ins route to their executor type, not `mcp_tool`.
- Registered MCP tools still route as `mcp_tool`.
- Plugin handlers still win through their registered handler key.

Acceptance:

- Native `file_read` dispatches as `file_read`.
- Native `wait_for_user` dispatches as `wait_for_user`.
- Native MCP tools preserve `type=mcp_tool` and `arguments`.
- Focused tool gate passes.

## Phase 2 - MCP Request Correlation

- Add JSON-RPC request correlation by `id` inside MCP stdio connections.
- Use one reader task per connection with an `id -> future` response map.
- Ignore or log notifications without stealing request responses.
- Serialize writes with a connection-level lock.

Acceptance:

- Concurrent or interleaved MCP responses resolve to the right request.
- Notifications do not satisfy pending calls.
- Existing MCP integration tests stay green.

## Phase 3 - MCP Timeout Recovery

- Make one layer own MCP call timeout behavior.
- On timeout, close the connection and reconnect before future tool calls.
- Prevent late responses from poisoning the next request.

Acceptance:

- A timed-out MCP call returns a clear timeout result.
- The following MCP call cannot consume the late timed-out response.
- Reconnect path is covered by focused tests.

## Phase 4 - Dynamic Native Schema Refresh

- Refresh native tool schemas when MCP tools are granted, revoked, reloaded, or
  connected after session start.
- Keep prompt scope, executor scope, and native schemas in sync.

Acceptance:

- Mid-session MCP connect makes the new tool available to native calling.
- Revoked tools are removed from executor scope and native schemas.

## Phase 5 - Widget-State Ownership

- Make `WidgetStateRefresher` the canonical widget-state producer.
- Keep legacy hub `state_snapshot` as merge-only fallback during rollout.
- Add agent and skills keys to the refresher so refresh ticks do not erase hub
  snapshot fields.
- Preserve old keys on partial refresh failure.
- Request render after successful state assignment.

Acceptance:

- A hub `state_snapshot` cannot be erased by the next refresh tick.
- Local and attach mode expose the same widget-state keys.
- Stale/degraded state is visible when refresh fails repeatedly.

## Phase 6 - Attach Canonicalization

- Pick one attach event consumer contract.
- Do not build new behavior on top of the legacy attach client until its event
  contract matches application attach.
- Fix display-lock drops before claiming attach rendering stability.

Acceptance:

- Profile switching, state reads, permission bridge, hub messages, and rendered
  output share one attach behavior.
- Attach tests cover message/state/permission/render events.

## Phase 7 - Hub Runtime Boundary

- Split hub lifecycle/runtime from hub command and tool handlers.
- Keep public hub commands unchanged.
- Move socket, presence, RPC, heartbeat, coordinator startup, and daemon-ready
  ordering behind a runtime boundary.

Acceptance:

- Startup invariant test proves socket, RPC handlers, presence, state handlers,
  and daemon-ready signaling happen in the correct order.
- Hub command behavior remains compatible.

## Phase 8 - Docs And Regression Gate

- Mark stale architecture/RFC docs superseded or update them to match live code.
- Add a focused stabilization test command for tool, MCP, state, attach, hub,
  and rendering contracts.
- Require that command before future hub/tool/state changes merge.

Acceptance:

- Docs no longer describe dead attach or hub behavior as current.
- Stabilization gate is documented and runnable from a clean checkout.
