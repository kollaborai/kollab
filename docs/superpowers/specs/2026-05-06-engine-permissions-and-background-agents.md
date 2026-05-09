# Engine Permissions and Background Agents Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:writing-plans before implementation. Keep implementation slices narrow, preserve unrelated user work, and validate each API contract with tests before moving to runtime probes.

## Goal

Make the engine API a complete, explicit runtime surface for permission approvals, multi-tool partial approvals, background worker sessions, and hub-agent lifecycle control without pretending the TUI/daemon runtime and engine runtime are the same layer.

## Current State

The local TUI runtime runs `TerminalLLMChat` and `LLMService` in-process. Attach mode connects a TUI client to a daemon over socket/RPC. The engine runtime is a separate FastAPI HTTP/SSE server built around `EngineSession`, `TurnRunner`, session routes, permission routes, and hub bridge routes.

The engine already supports:

- `POST /sessions` to create one `EngineSession`.
- `POST /sessions/{session_id}/message` to stream one active turn over SSE.
- `permission_request`, `permission_granted`, and `permission_denied` SSE events.
- `POST /sessions/{session_id}/permission` to approve or deny a pending tool request.
- `GET /sessions/{session_id}/permissions` to list pending permission ids.
- `POST /sessions/{session_id}/permissions/mode` to change approval mode.
- `/hub/*` routes that observe and message already-running hub agents.

The engine does not yet provide a complete client contract for permission UX or first-class API control of hub-agent spawning.

## Problems To Fix

### P1: Permission UX Contract Is Underdocumented And Too Implicit

Clients need to know that permission requests are pushed over the active message SSE stream, not returned as a polling token. Today that is only inferable from `EngineSession._permission_callback()` and `routes/permissions.py`.

Fix:

- Add a formal engine permission contract document.
- Define the exact flow:
  1. Client creates a session.
  2. Client opens `POST /sessions/{session_id}/message` SSE stream.
  3. Engine emits `permission_request` when a tool needs confirmation.
  4. Client renders approval UI from the SSE payload.
  5. Client posts `POST /sessions/{session_id}/permission` on a second HTTP request.
  6. Engine emits `permission_granted` or `permission_denied`.
  7. Engine resumes the turn and emits `tool_result` or denial output.
- Make it explicit that the message stream must stay open while approvals are posted.

Implementation targets:

- Create `docs/engine/permissions.md`.
- Link it from `packages/kollabor-engine/README.md` if that README exists, otherwise from top-level `README.md` engine section.
- Add OpenAPI examples to `routes/permissions.py` and `routes/messages.py`.

Acceptance:

- A new client engineer can implement the full approval UI without reading engine internals.
- The docs say "no token polling" directly.
- The docs say "approval response is side-channel HTTP while SSE stays open" directly.

### P2: Pending Permission Endpoint Loses Prompt Details

`GET /sessions/{session_id}/permissions` currently returns only pending tool ids. If a client reconnects, opens a new tab, or loses the SSE event, it cannot reconstruct the approval prompt.

Fix:

- Store full pending permission details per `tool_id`.
- Return those details from `GET /sessions/{session_id}/permissions`.
- Keep the existing `pending` id list for backward compatibility.
- Register the pending `asyncio.Event` and pending details before emitting `permission_request` to SSE. A fast client can post approval immediately after receiving the SSE event, so the HTTP response path must not race with registration.

Data shape:

```json
{
  "session_id": "sess_123",
  "approval_mode": "confirm_all",
  "pending": ["tool_1"],
  "pending_details": [
    {
      "tool_id": "tool_1",
      "tool_name": "terminal",
      "tool_type": "terminal",
      "input": {"cmd": "rg -n \"EngineSession\" packages"},
      "risk_level": "medium",
      "risk_reason": "terminal command requires confirmation",
      "created_at": 1778100000000
    }
  ],
  "stats": {}
}
```

Implementation targets:

- Modify `packages/kollabor-engine/src/kollabor_engine/session.py`.
- Add `_pending_permission_details: Dict[str, Dict[str, Any]]`.
- Populate `_pending_permissions` and `_pending_permission_details` before emitting `permission_request`.
- Clear it in the same `finally` block that clears `_pending_permissions`.
- Modify `packages/kollabor-engine/src/kollabor_engine/routes/permissions.py`.
- Add focused tests in `tests/unit/test_engine_permissions.py`.

Acceptance:

- `GET /sessions/{id}/permissions` can rebuild every visible approval prompt.
- Reconnect/reload no longer strands a user behind invisible pending ids.
- Existing clients reading `pending` still work.
- Immediate approval after `permission_request` does not return 404.

### P3: Permission Scope Is Accepted But Not Enforced For Future Requests

`POST /sessions/{session_id}/permission` accepts `scope`, and engine emits that scope in `permission_granted`, but the current callback path only unblocks the one pending request. The scope is not clearly translated into session/project/tool trust behavior.

Fix:

- Define allowed scopes in code, not only comments:
  - `once`
  - `session`
  - `project`
  - `trust_tool`
  - `always_edits`
- Reject unknown scopes with HTTP 400.
- Keep full pending tool data/details so approved scopes can be applied after `resolve_permission()`.
- Map approved scopes to the existing `PermissionManager` approval model where supported:
  - `once`: approve only the current pending request; do not record future approval.
  - `session`: call `PermissionManager._record_approval(...)` with the pending tool data.
  - `project`: call `PermissionManager._record_project_approval(...)` only after project approval storage is workspace-scoped to the `EngineSession.workspace_path`.
  - `trust_tool`: return HTTP 501 until a safe exact mapping to trusted-tool config exists.
  - `always_edits`: return HTTP 501 until file write/edit-only approval mode is exposed safely through engine.
- Return `applied_scope` and `persisted` in the permission response.
- Document exact behavior per scope.

Implementation targets:

- Modify `routes/permissions.py` to validate `scope`.
- Add an `apply_permission_scope()` method on `EngineSession` or a small helper module if the logic grows.
- Use existing permission manager methods where available instead of adding parallel state.
- Fix project approval storage before enabling `project`: `PermissionManager._get_project_data_dir()` currently resolves from process cwd, while `EngineSession` can run against `workspace_path`. Add either a workspace override to `PermissionManager` or an engine wrapper that writes to the session workspace's project data dir.
- Add tests for approve once, session scope, two-workspace project scope, invalid scope, and unsupported scope.

Acceptance:

- Client cannot send a scope that is ignored silently.
- The response says whether the approval affected only one tool or future requests.
- Tests prove invalid scopes fail.
- Project-scoped approvals are stored under the session workspace, not the engine server cwd.

### P4: Partial Approval Semantics Need To Be First-Class

If the model requests three tools, current behavior effectively prompts per tool while the turn is paused on each tool. That is valid, but clients need an explicit contract for "approve two, deny one" and how the engine continues.

Fix:

- Document that permission decisions are per tool call.
- Add tests proving mixed decisions in one turn:
  - first tool approved and executed.
  - second tool denied and returned as denial/error tool result.
  - third tool approved and executed if the turn loop reaches it.
- Include SSE ordering expectations:
  - `tool_start`
  - `permission_request`
  - `permission_granted` or `permission_denied`
  - `tool_result`
  - next tool's events
- Treat this as a contract/test slice first. Current code is expected to emit a failed `tool_result` for denied tools through `ToolExecutor`; only change `TurnRunner` if tests prove the visible event sequence or tool-result metadata is wrong.

Implementation targets:

- Add tests in `tests/unit/test_engine_permissions.py`.
- If current `TurnRunner` does not emit a denial `tool_result`, update `TurnRunner` to make denial visible to clients and the model.

Acceptance:

- Client implementers know there is no all-or-nothing batch approval.
- A denied tool does not hang the turn.
- Approved sibling tools can still run.
- A denied tool emits `tool_result` with `success=false` and permission-denied metadata.

### P5: Foreground And Background Disconnect Semantics Are Not Explicit

`TurnRunner.run()` wires the SSE queue to `session._sse_queue` and owns an active task. Current foreground `/message` behavior already cancels the producer task when the SSE generator closes, but that behavior is not documented or queryable. Background run event subscribers must behave differently: disconnecting from `/runs/{run_id}/events` must detach the subscriber without canceling the run.

Fix:

- Document v1 route-specific behavior:
  - Foreground `POST /sessions/{session_id}/message`: client disconnect cancels the active turn.
  - Background `GET /sessions/{session_id}/runs/{run_id}/events`: client disconnect detaches only that subscriber.
  - Background `POST /sessions/{session_id}/runs/{run_id}/cancel`: explicit cancel endpoint for the run itself.
- Emit/record a terminal turn state that clients can query afterward.
- Clear pending permissions when cancellation happens.
- Return 404 or 409 from stale permission responses after cancellation.
- Document this as v1 behavior.

Implementation targets:

- Add state fields to `EngineSession`:
  - `active_turn_id`
  - `last_turn_status`
  - `last_turn_error`
- Ensure cancellation clears pending permission details.
- Add a route field in `GET /sessions/{session_id}`.
- Add tests for disconnect/cancel cleanup.

Acceptance:

- No pending permission remains after disconnect cancellation.
- A late approval returns a clear error.
- Clients can show "turn canceled because stream disconnected."
- Background runs continue when an event subscriber disconnects.

### P6: Engine Background Workers Need An API-Native Contract

The engine can run multiple sessions concurrently, but there is no named background job model for clients that want "run these two or three agents in the background and check status later."

Fix:

- Add a background turn API on top of `EngineSession`:
  - `POST /sessions/{session_id}/runs`
  - `GET /sessions/{session_id}/runs`
  - `GET /sessions/{session_id}/runs/{run_id}`
  - `POST /sessions/{session_id}/runs/{run_id}/cancel`
  - `GET /sessions/{session_id}/runs/{run_id}/events`
- Store run state in memory for v1:
  - `queued`
  - `running`
  - `waiting_for_permission`
  - `completed`
  - `failed`
  - `canceled`
- Keep one active run per session for v1.
- For multiple workers, clients create multiple sessions, one per worker.
- Do not make background runs depend on the `TurnRunner.run()` SSE generator owning the producer task. Extract or add a lower-level turn executor with a pluggable event sink:
  - foreground `/message` adapter owns the stream and cancels on stream close.
  - background `/runs` adapter owns the task in `run_store`.
  - `/runs/{run_id}/events` subscribes to stored/replayed events and does not own task lifetime.
- Store ordered engine SSE events for replay with a retention limit, not only a vague summary. V1 retention: keep the last 1,000 events per run and the final result/error indefinitely while the process lives.

Implementation targets:

- Create `packages/kollabor-engine/src/kollabor_engine/routes/runs.py`.
- Create `packages/kollabor-engine/src/kollabor_engine/run_store.py`.
- Register the router in `server.py`.
- Refactor `TurnRunner` so LLM/tool execution is reusable by foreground and background adapters without coupling task lifetime to one SSE response.
- Add tests in `tests/unit/test_engine_runs.py`.

Acceptance:

- A client can start three sessions/runs and return later for status.
- A run waiting on permission exposes pending permission details.
- Completed runs retain final response, final error if any, and ordered event replay.
- Disconnecting from run events does not cancel the run.

### P7: Engine Lacks First-Class Hub Spawn/Stop/Capture Control

Engine hub routes can list, inspect, message, and stream already-running hub agents. They do not expose `/hub/spawn`, `/hub/stop`, or `/hub/capture` as API routes. That means engine clients cannot fully manage the autonomous hub mesh.

Fix:

- Add engine hub lifecycle routes:
  - `POST /hub/agents` to spawn an agent.
  - `POST /hub/identities/{identity}/stop` to stop one identity.
  - `GET /hub/identities/{identity}/capture` as alias of output with clearer naming.
  - Keep existing `GET /hub/agents/{agent_id}` style routes as agent-id aliases where useful; do not overload one path parameter with both identity and agent id.
- Extract `HubLifecycleService` from `plugins/hub/plugin.py` spawn/stop logic before adding engine write routes. The service must own pool identity selection, online checks, reservation, skill/profile resolution, orchestrator registration, DNS attestation, stop signaling, pid verification, and stale presence cleanup.
- Use `process_manager.py` or `mentiko_adapter.py` only as spawn backends. They are not sufficient as the lifecycle contract owner because they do not implement hub pool/reservation/attestation semantics.
- Require stronger authorization for lifecycle routes:
  - bearer auth remains required by engine middleware.
  - spawn/stop additionally requires `manage_hub=true` on the request or a configured engine capability.
  - stop requires `confirmation_phrase: "stop <identity>"`.
  - workspace must resolve through the same safe workspace rules as `EngineSession`.

Spawn request:

```json
{
  "name": "coder",
  "identity": "lapis",
  "type": "research",
  "task": "inspect engine permissions contract",
  "workspace": "/Users/malmazan/dev/kollab",
  "metadata": {"source": "engine-api"}
}
```

Spawn response:

```json
{
  "ok": true,
  "identity": "lapis",
  "agent_type": "research",
  "status": "booting",
  "agent_id": "abc123",
  "pid": 12345,
  "poll_url": "/hub/identities/lapis"
}
```

Implementation targets:

- Extract `HubLifecycleService` from `plugins/hub/plugin.py`.
- Keep plugin `/hub spawn` and `/hub stop` using the extracted service.
- Modify `packages/kollabor-engine/src/kollabor_engine/routes/hub.py`.
- Add tests in `tests/unit/test_engine_hub_routes.py`.

Acceptance:

- Engine clients can start 2-3 autonomous hub agents without using TUI attach mode.
- Spawned agents appear in `GET /hub/agents?refresh=true`.
- Engine can send messages to spawned agents via `POST /hub/messages`.
- Immediate spawn response does not promise `socket_path`; clients poll until presence/socket appears.
- Stop returns success only after presence cleanup and process exit are verified. If a stale presence file was cleaned without a live process, the response reports `status: stale_cleaned`, not `stopped`.
- Observe/message routes work with normal engine auth; spawn/stop fail without hub management authority or the stop confirmation phrase.

### P8: Attach Mode And Engine Mode Need Clear Naming

Attach mode is useful because it lets the TUI mirror a background daemon. It is not the engine HTTP API. The naming and docs need to prevent clients from confusing them.

Fix:

- Document three runtime modes:
  - local TUI: in-process `LLMService`.
  - daemon attach: TUI client over socket/RPC to background daemon.
  - engine API: HTTP/SSE server with `EngineSession`.
- Add a short decision matrix:
  - use local TUI for interactive terminal work.
  - use daemon attach for autonomous TUI/hub work that can be reattached.
  - use engine API for external clients, web apps, mobile apps, and service orchestration.

Implementation targets:

- Create `docs/engine/runtime-modes.md`.
- Link it from `docs/engine/permissions.md`.
- Link it from hub lifecycle docs after P7 lands.

Acceptance:

- Nobody has to infer from code that attach is not engine.
- The background-agent recommendation names both choices and tradeoffs.

## Recommended Implementation Order

1. P1 and P8 docs first, because they stop design drift immediately.
2. P2 pending details, because it fixes reconnect and missing UI payloads.
3. P3 scope validation, because silent scope ignore is dangerous.
4. P4 mixed approval tests and any TurnRunner behavior fixes.
5. P5 and P6 together, because foreground disconnect and background subscriber disconnect must be designed as one task ownership model.
6. P6 engine background runs, because it creates API-native autonomous work.
7. P7 hub lifecycle bridge, because it is the largest extraction risk.

## Non-Goals

- Do not merge TUI and engine runtimes.
- Do not make engine depend on terminal rendering or TUI widgets.
- Do not make the web client parse TUI attach/RPC frames.
- Do not hide permission approvals behind an opaque polling token.
- Do not add destructive hub stop behavior without permission checks.

## Test Plan

- Unit tests for permission detail storage and cleanup.
- Unit tests for invalid/unsupported scope handling.
- Unit tests for mixed approve/deny tool flow.
- Unit tests for stale permission response after cancel/disconnect.
- Unit tests for background run lifecycle.
- Unit tests for hub spawn/stop route validation and delegation.
- Regression test for immediate approval after `permission_request`.
- Regression test for project approval storage across two different session workspaces.
- Regression test that `/runs/{run_id}/events` disconnect does not cancel a run.
- Regression test that hub stop verifies process exit and reports stale cleanup separately.
- Runtime probe:
  1. Start `python -m kollabor_engine serve --port 5050`.
  2. Create a session with `approval_mode=confirm_all`.
  3. Send a message that triggers file/terminal tools.
  4. Confirm `permission_request` appears on SSE.
  5. Approve one request and deny another.
  6. Confirm the stream completes and pending permissions are empty.
  7. Spawn two background runs or hub agents depending on implementation slice.

## Decisions

- `once` and `session` permission scopes ship first.
- `project` scope ships only after project approval storage is bound to `EngineSession.workspace_path`.
- `trust_tool` and `always_edits` return HTTP 501 until they have exact safe mappings.
- Foreground `/message` disconnect cancels the turn.
- Background run event disconnect does not cancel the run.
- Background run events are retained in memory for v1 with ordered replay.
- Hub lifecycle routes require hub management authority.
- Hub stop requires `confirmation_phrase: "stop <identity>"`.
- Hub lifecycle API is identity-first; agent-id routes remain aliases for inspection.

## Definition Of Done

- Permission UX is implementable from docs alone.
- Pending permission details survive client reconnect while the turn is active.
- Unsupported scopes cannot silently succeed.
- Mixed approve/deny behavior is tested.
- SSE disconnect behavior is explicit and tested.
- API-native background worker sessions exist.
- Engine can spawn, message, inspect, and stop hub agents through documented routes.
- Runtime mode docs make local TUI, daemon attach, and engine API impossible to confuse.
