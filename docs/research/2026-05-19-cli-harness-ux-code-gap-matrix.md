# Kollab CLI Harness UX: Code-Grounded Gap Matrix

Date: 2026-05-19
Branch: `codex/mcp-correlation-timeout`

## Why this exists

The UX research deck is useful product direction, but it was not originally
grounded slide-by-slide in the live Kollab code. This matrix maps the deck
pillars to current implementation, existing tests, remaining gaps, and the
next slices that should become real work.

## Current-State Summary

The live code is farther along than the deck implies in a few important places:

- Attach mode already has an in-app proxy path with RPC-backed state, permission
  prompt routing, launch flag draining, and daemon-death shutdown behavior.
- Widget status state already has a typed `WidgetState` merge path, freshness
  metadata, and render invalidation after successful refresh.
- Hub delivery hardening has coverage for wake classification, identity mailbox,
  DNS liveness, delivery policy, tracing, remote trust, and pending replies.
- The stabilization gate already groups the relevant contract tests.

The biggest remaining UX gap is not "invent all architecture." It is making the
existing architecture visible, explainable, and provably reliable in the user
surface: cockpit status truth, attach dashboard, context control, tool timeline,
and a broader fresh-daemon runtime proof.

Update from the first implementation slice:

- `/doctor` now exists as a first-run readiness/proof command.
- `python main.py --doctor` exits cleanly and reports ready/degraded/blocked
  checks without requiring a model call.
- `tests/tmux/fresh_daemon_doctor_smoke.sh` proves `/doctor` renders through a
  fresh daemon-backed attach client with an isolated HOME.
- Runtime proof now includes read proof, XML/native/mock-MCP normalization,
  real MCPIntegration mock connection execution, and fresh daemon attach smoke.

## Code Ownership Map

| UX pillar | Current owner files | Current tests | Current state |
| --- | --- | --- | --- |
| First-run under 90 seconds | `kollabor/cli.py`, `main.py`, `kollabor/application.py`, `kollabor/commands/system_commands/handlers/system.py`, profile/login/MCP commands | `tests/unit/commands/test_all_command_handlers.py`, `tests/unit/mcp/test_mcp_integration.py`, `tests/tmux/fresh_daemon_doctor_smoke.sh` | `/doctor` proof exists with read/XML/native/mock-MCP checks |
| Cockpit status bar | `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py`, `kollabor/state/refresher.py`, `kollabor/state/widget_state.py` | `tests/unit/tui/test_status_widgets_remote_state.py`, `tests/unit/test_widget_state.py`, `tests/unit/test_widget_state_refresher.py` | Existing status widget now exposes mode/freshness/source/age |
| Context management | `plugins/context_compaction_plugin.py`, `kollabor/state/context*.py`, `kollabor/state/local.py`, `kollabor/state/remote.py` | `tests/unit/test_compaction_dual_gate.py`, `tests/unit/test_context_compaction_tool_boundaries.py` | `/compact preview` now exposes preserved/removed/pinned counts |
| Tool reliability timeline | `packages/kollabor-agent/src/kollabor_agent/*`, `kollabor/llm/llm_coordinator.py`, MCP integration/manager | `tests/unit/test_tool_call_contract_golden.py`, `tests/unit/test_tool_timeline.py`, `tests/unit/mcp/test_mcp_integration.py`, tool registry tests | Timeline event contract exists; UI rendering still missing |
| Expert controls | command handlers under `kollabor/commands/`, permission manager, profile/agent/skill commands | `tests/unit/commands/test_all_command_handlers.py`, permission tests | Many controls exist; needs discoverable palette/modes/status feedback |
| Attach mode | `kollabor/application.py`, `kollabor/attach_client.py`, `kollabor/state/remote.py`, `kollabor/llm/permissions/attach_bridge.py` | `tests/unit/test_attach_client.py`, `tests/unit/test_attach_permission_bridge.py`, `tests/unit/test_attach_startup_order.py`, `tests/unit/commands/test_all_command_handlers.py` | `/status` now exposes attach runtime, heartbeat, profile, agent, permissions, pending RPC |
| Hub/multi-agent ownership | `plugins/hub/plugin.py`, `plugins/hub/delivery.py`, `plugins/hub/task_ledger.py`, `plugins/hub/dns/*`, `plugins/hub/remote_envelope.py` | `tests/unit/test_hub_*`, `tests/test_hub_rpc_integration.py` | Hub status now includes read-only cockpit counts; richer dashboard still pending |
| Regression gate | `scripts/stabilization-gate.sh`, `tests/tmux/fresh_daemon_doctor_smoke.sh` | gate invokes 22 targeted test files; fresh-daemon smoke currently separate | Good focused gate; still needs MCP/tool smoke folded in |

## Pillar Matrix

### 1. First-run proof

Deck claim:
New users should install, authenticate, run one useful tool, and understand
degraded state in under 90 seconds.

Current behavior:
Kollab has profile/login/MCP/agent command surfaces and a rich app startup path.
The first implementation slice adds canonical `/doctor` and CLI `--doctor`
coverage for account/profile-ish runtime state, git/cwd, permissions, MCP
configuration, hub/agent/daemon state, core services, and one harmless proof
read.

Evidence:
- CLI entrypoints: `main.py`, `kollabor_cli_main.py`, `kollabor/cli.py`
- Profile and login command tests exist through `tests/unit/commands/test_all_command_handlers.py`
- `tests/tmux/fresh_daemon_doctor_smoke.sh` launches `python main.py --daemon`
  with an isolated HOME, routes `/doctor` through attach, and asserts report
  rendering, proof read rendering, no traceback, and clean daemon startup.

Gap:
The first-run story now has a guided proof path, but the proof is still local
readiness only. It does not yet execute XML/native/MCP tool calls in one fresh
runtime.

Next slice:
Expand `/doctor` into a deeper proof mode that can run one XML tool, one native
tool, and one mock MCP tool when a mock MCP server is provided.

Acceptance:
Fresh install can run one command and see `ready`, `degraded`, or `blocked`
with exact next action. Current command meets this for local readiness; the
mock-MCP/tool proof remains open.

### 2. Cockpit status bar

Deck claim:
The status bar should show operational truth, not decoration.

Current behavior:
This is already partly implemented. `WidgetStateRefresher` pulls snapshots from
`StateService`, merges into `ctx.remote_state`, adds freshness metadata, and
requests render after updates. Widgets prefer remote state for profile/model,
endpoint, processing, MCP, agent, skills, hub, tmux, and session.

Evidence:
- `kollabor/state/refresher.py`
- `kollabor/state/widget_state.py`
- `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py`
- `tests/unit/test_widget_state.py`
- `tests/unit/test_widget_state_refresher.py`
- `tests/unit/tui/test_status_widgets_remote_state.py`

Gap:
The state pipe is good. The UX vocabulary is still raw: users need clearer
stale/degraded signals, last-updated timing, and mode changes surfaced as
intentional state.

Next slice:
Add a compact "state health" segment:
`fresh 1.8s`, `stale`, `degraded`, `daemon`, `attach`, `local`, with source.

Acceptance:
In local and attach mode, a state update visibly invalidates render and stale
state cannot look identical to healthy state.

### 3. Context control

Deck claim:
Context management is now UX, not internals.

Current behavior:
There is context compaction logic and a context registry/RPC surface. The widget
refresher exposes token counts and context-ish stats. The compaction plugin can
read `remote_state` for token estimates when prompt tokens are unavailable.

Evidence:
- `plugins/context_compaction_plugin.py`
- `kollabor/state/context.py`
- `kollabor/state/context_registry.py`
- `kollabor/state/local.py`
- `kollabor/state/remote.py`
- `tests/unit/test_compaction_dual_gate.py`
- `tests/unit/test_context_compaction_tool_boundaries.py`

Gap:
The user-facing preview is not obvious from the code pass: what stays, drops,
and pins should be visible before manual compaction.

Next slice:
Add `/compact preview` with preserved/removed/pinned buckets and token delta.

Acceptance:
Manual compact can be inspected without mutating history, then applied with a
second explicit action.

### 4. Tool reliability timeline

Deck claim:
Tool calls should render as a timeline: schema, permission, call, timeout,
reconnect, result, history.

Current behavior:
Tool-call contracts and MCP hardening already have targeted tests. The current
branch specifically contains MCP timeout/recovery work. The stabilization gate
includes tool golden tests, native tools, MCP integration, permission metadata,
and hub RPC integration.

Evidence:
- `packages/kollabor-agent/src/kollabor_agent/`
- `kollabor/llm/llm_coordinator.py`
- `tests/unit/test_tool_call_contract_golden.py`
- `tests/unit/llm/test_native_tools_handler.py`
- `tests/unit/mcp/test_mcp_integration.py`
- `tests/unit/test_permission_tool_metadata.py`
- `scripts/stabilization-gate.sh`

Gap:
Users still need a visible timeline/log view that explains retries, timeouts,
schema refresh, and recovery without reading debug logs.

Next slice:
Introduce a tool event model for the TUI:
`registered`, `permission`, `started`, `stdout`, `timeout`, `reconnect`,
`result`, `history_appended`.

Acceptance:
One XML tool, one native tool, and one MCP tool each produce a replayable trace
in the UI and in saved conversation history.

### 5. Expert controls

Deck claim:
Power users reward controls that become muscle memory.

Current behavior:
Kollab already has slash commands for profile, permissions, MCP, agent, skill,
resume, save, login, and status. Attach mode drains launch flags over RPC so
profile/agent/skill/system-prompt flags can affect the daemon.

Evidence:
- `kollabor/commands/`
- `kollabor/application.py::_drain_attach_pending_flags`
- `tests/unit/commands/test_all_command_handlers.py`
- `tests/test_hub_rpc_integration.py`

Gap:
The controls are command-rich but not yet command-palette-rich. Discoverability
and mode feedback are still scattered.

Next slice:
Add a palette/help surface that groups commands by job:
model/profile, tools/MCP, permissions, context, attach, agents, history.

Acceptance:
Every mode switch prints the new mode, what changed, and how to undo or inspect.

### 6. Attach dashboard

Deck claim:
Attach mode should feel like reconnecting to the same session brain.

Current behavior:
The canonical attach path is now in-app proxy mode in `TerminalLLMChat`.
The old `kollabor/attach_client.py` explicitly marks itself legacy. In-app
attach has RPC, remote state, permission routing, launch-flag drain, daemon
death handling, hub status info, and Ctrl+Z detach semantics.

Evidence:
- `kollabor/application.py::_initialize_attach_proxy`
- `kollabor/attach_client.py` deprecation docstring
- `kollabor/state/remote.py`
- `kollabor/llm/permissions/attach_bridge.py`
- `tests/unit/test_attach_startup_order.py`
- `tests/unit/test_attach_permission_bridge.py`

Gap:
The architecture is in place, but the dashboard itself is still implied by
status widgets and messages. Users need one inspectable attach view: daemon,
socket, profile, agent, permissions, pending RPCs, last heartbeat, recent
messages, and degraded state.

Next slice:
Add `/attach status` or a status modal tab backed by `RemoteStateService`.

Acceptance:
Attach mode can explain what it is connected to, what is stale, and what will
happen on Ctrl+Z vs Ctrl+C.

### 7. Hub and multi-agent ownership

Deck claim:
Multi-agent work only feels safe when ownership is visible.

Current behavior:
Hub reliability has real code and tests: durable identity delivery, DNS
liveness/trust separation, delivery policy, tracing, pending replies, wake
classification, and remote envelope contract. The large remaining problem is
that much lifecycle/runtime behavior still lives inside `plugins/hub/plugin.py`.

Evidence:
- `plugins/hub/delivery.py`
- `plugins/hub/task_ledger.py`
- `plugins/hub/dns/*`
- `plugins/hub/remote_envelope.py`
- `plugins/hub/plugin.py`
- `tests/unit/test_hub_delivery_policy.py`
- `tests/unit/test_hub_delivery_trace.py`
- `tests/unit/test_hub_pending_replies.py`
- `tests/unit/test_hub_wake_order.py`
- `tests/unit/test_hub_dns_liveness.py`
- `tests/unit/test_hub_remote_trust.py`

Gap:
Users need visible roster, expected replies, delivery trace, and assignment
state. Developers need hub runtime split so startup/lifecycle is testable
without spelunking a very large plugin file.

Next slice:
Add a hub cockpit view before deeper refactor:
roster, expected replies, last route decision, quarantined messages, stale
endpoints, and active coordinator.

Acceptance:
When a final report is delivered, queued, rejected, or quarantined, the UI and
trace explain which happened and why.

## Verification Run

Command:

```bash
scripts/stabilization-gate.sh
```

Result:
Passed.

Evidence:

```text
157 passed in 18.05s
```

Coverage from the gate:

- tool-call contract golden tests
- native tool handler tests
- MCP integration tests
- permission metadata tests
- attach permission bridge tests
- attach startup ordering test
- widget state and refresher tests
- remote-state status widget tests
- agent HUD/wake tests
- hub message parsing, delivery, DNS liveness, trust, pending replies, and RPC
  integration tests

What this does not prove:

- a completely fresh daemon startup from the user install path
- a full TUI visual smoke with real attach/reconnect/offline transitions
- first-run onboarding success from an empty config

## Recommended Implementation Slices

1. First-run doctor/proof command
   - Owner: CLI/application/commands
   - Why first: biggest UX gap for new users and easiest to verify
   - Tests: command unit test plus clean-runtime smoke with mock MCP

2. Status health segment
   - Owner: TUI status widgets + WidgetState
   - Why second: the state layer already exists; this turns reliability into
     visible UX
   - Tests: local + attach widget state tests

3. Attach status view
   - Owner: application attach proxy + RemoteStateService + TUI modal/status
   - Why third: attach architecture is real but invisible
   - Tests: attach startup order, remote state reads, permission bridge, daemon
     disconnect behavior

4. Compact preview
   - Owner: context compaction plugin + context state
   - Why fourth: high-value power-user control, lower blast radius than hub split
   - Tests: preview is non-mutating; apply mutates; pin/drop buckets stable

5. Tool timeline
   - Owner: kollabor-agent tool execution + TUI message/render path
   - Why fifth: builds on current contract hardening and makes failures legible
   - Tests: XML/native/MCP golden history and rendered timeline snapshots

6. Hub cockpit view
   - Owner: hub delivery/task ledger + TUI/status modal
   - Why sixth: leverages recent delivery hardening before splitting lifecycle
   - Tests: pending replies, delivery trace, quarantine/reject, wake passive/active

7. Hub runtime split
   - Owner: `plugins/hub/`
   - Why last: high blast radius; do it after cockpit and tests make behavior
     observable
   - Tests: startup invariant plus existing hub regression suite

## Deck Update Needed

The PowerPoint should get a short appendix:

- "Current implementation already present"
- "UX gaps left"
- "Recommended build order"
- "What must be proven with a fresh daemon smoke"

This will make the deck honest: it becomes a product roadmap grounded in the
repo, not just competitor-inspired direction.
