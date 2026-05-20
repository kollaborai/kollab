# Kollab CLI Harness UX: Implementation Slices

Date: 2026-05-19

## Principle

Build UX in reliability-backed slices. Each slice should expose existing runtime
truth before adding new behavior. No rewrite.

## Slice 1: First-Run Doctor And Proof Task

Goal:
Make a new user prove Kollab works in one command.

Status:
Initial implementation exists. `/doctor` and `python main.py --doctor` report a
ready/degraded/blocked verdict, core runtime checks, and one harmless proof
read. A fresh daemon-backed attach smoke exists at
`tests/tmux/fresh_daemon_doctor_smoke.sh`.
Current implementation also verifies XML normalization, native tool
normalization, mock-MCP normalization, and a real `MCPIntegration.call_mcp_tool`
mock connection path.

Build:
- Add a `/doctor` command.
- Check account/login status, active profile/model, provider endpoint, shell,
  git repo status, permission mode, MCP server count, native tool availability,
  hub identity, attach readiness, and config paths.
- Run one harmless proof action, preferably a read-only native tool or mock MCP
  call when configured.
- Print exact degraded/blocking next actions.

Why now:
The deck's "under 90 seconds" promise is currently aspirational. The repo has
the pieces, but not the guided success path.

Tests:
- command unit test with fake managers: done
- fresh daemon attach smoke with isolated HOME: done
- mock MCP normalization proof: done
- real MCPIntegration mock connection proof: done

Done means:
Fresh user can run one command and know whether Kollab is ready, degraded, or
blocked without reading docs.

## Slice 2: Status Health Segment

Goal:
Turn `WidgetState` freshness into visible cockpit truth.

Build:
- Add status labels for `local`, `attach`, `daemon`, `fresh`, `stale`,
  `degraded`, and `source`.
- Surface last-updated age from `_updated_at`.
- Ensure stale/degraded state cannot look healthy.

Status:
Implemented in the existing `status` widget rather than adding a new widget.
`WidgetState` carries `runtime_mode`; the refresher marks partial snapshot
failures as degraded and preserves source/update metadata.

Why now:
The typed state layer already exists and passed tests. This is a high-leverage
UX improvement with low architectural risk.

Tests:
- widget-state runtime mode round trip: done
- stale/degraded rendering: done
- attach-mode remote-state preference tests: done
- render invalidation assertion stays covered

Done means:
The status bar tells the user whether it is showing fresh daemon truth or old
local fallback.

## Slice 3: Attach Status View

Goal:
Make attach mode inspectable.

Build:
- Extend `/status` with attach/runtime sections.
- Show daemon pid/uptime, socket identity, connected profile/model, active
  agent, skills, permission mode, hub peers, pending RPC state, last heartbeat,
  and Ctrl+Z/Ctrl+C semantics.
- Include degraded reasons when RPC/state refresh fails.

Why now:
The canonical in-app attach path is already real. The user just cannot inspect
it as one coherent surface.

Tests:
- `/status` modal runtime-state test: done
- RPC pending count test: done
- existing attach startup/permission bridge coverage remains in the gate

Done means:
Attach no longer feels magical. It can explain what it is connected to and what
will happen if the user detaches.

## Slice 4: Compact Preview

Goal:
Make context compaction understandable before mutation.

Build:
- Add `/compact preview`.
- Show preserved, removed, pinned, and estimated token delta.
- Add apply/confirm path that reuses the preview result or reruns with a clear
  timestamp.

Why now:
Users praised auto-compact in competitor tools but hate hidden context loss.
Kollab can win by making context control explicit.

Tests:
- preview is non-mutating: done
- apply mutates expected history
- pin/drop buckets stable

Done means:
Manual compaction is no longer a black box.

## Slice 5: Tool Timeline

Goal:
Make tool execution failures readable and replayable.

Build:
- Define a tool event shape for registration, permission, start, stdout/stderr,
  timeout, reconnect, result, and history append.
- Render compact timeline entries in TUI.
- Preserve enough trace in conversation history for debugging.

Why now:
The contract tests and MCP recovery work are strong. The UX still needs to show
what happened without log spelunking.

Tests:
- tool timeline event contract golden: done
- replayable event sequence golden: done
- XML tool timeline golden
- native tool timeline golden
- MCP timeout/reconnect timeline golden
- conversation history snapshot

Done means:
Users can answer "did the tool run, fail, retry, or get recorded?" from the UI.

## Slice 6: Hub Cockpit

Goal:
Make multi-agent ownership visible.

Build:
- Add a hub cockpit view with roster, coordinator, expected replies, stale
  endpoints, last delivery decisions, quarantined/rejected messages, and current
  assignments.
- Pull from existing delivery trace and pending reply data.

Status:
Initial read-only cockpit is added to hub status. It exposes pending replies and
the delivery trace path without changing routing behavior.

Why now:
Recent hub hardening made delivery more reliable. Now make the reliability
visible so missed-final-report bugs are obvious.

Tests:
- pending replies: done
- hub status cockpit counts: done
- delivery trace
- reject/quarantine paths
- wake active/passive classification

Done means:
When a final report is delivered, queued, rejected, or quarantined, the UI says
which and why.

## Slice 7: Hub Runtime Split

Goal:
Separate hub runtime/lifecycle from command/tool handlers without changing
public behavior.

Build:
- Extract socket/RPC/presence/heartbeat/coordinator startup into dedicated
  runtime modules.
- Keep command handlers and XML/native tool handlers separate.
- Preserve public hub command behavior.

Why last:
This is high blast radius. Do it after the cockpit and tests give us visibility.

Tests:
- startup invariant proving socket, RPC, presence, state handlers, and daemon
  ready signal order
- existing hub regression suite
- stabilization gate

Done means:
Future hub changes stop requiring archaeology through one giant plugin file.

## Runtime Proof Status

Fresh daemon proof now exists for the first-run doctor path:

1. Start with an isolated temp HOME.
2. Launch `python main.py --daemon` inside an isolated tmux socket.
3. Route `/doctor` through the daemon-backed attach client.
4. Confirm the doctor report renders.
5. Confirm the proof read renders.
6. Confirm no traceback and no daemon startup failure.

Latest local result:

- command: `tests/tmux/fresh_daemon_doctor_smoke.sh`
- result: 7/7 passed

The broader runtime proof still needs:

1. Start from an empty temp Kollab home/config.
2. Launch CLI with mock MCP server.
3. Execute one XML tool, one native tool, and one MCP tool.
4. Launch daemon/attach.
5. Switch profile/agent/permission mode through attach.
6. Kill/restart/reconnect daemon path without losing visible state.
7. Confirm status, hub roster, delivery trace, and render output stay coherent.

This should become the UX regression gate once stable.
