---
title: "Kollab Stability Review"
doc_type: audit-record
created: 2026-07-03
modified: 2026-07-03
status: active
---
# Kollab Stability Review

This report captures the July 3, 2026 read-only stability review of Kollab.
The review focused on program stability rather than feature correctness:
startup, shutdown, plugin lifecycle, event hooks, terminal state, MCP policy,
config reload, hub background work, subprocess cleanup, and missing contracts.

No code was changed during the review. The working tree already had local
modifications in:

- `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py`
- `plugins/context_compaction_plugin.py`

Targeted baseline validation run during the review:

```bash
python -m pytest tests/unit/test_event_bus.py \
  tests/unit/test_hub_stop_lifecycle.py \
  tests/unit/test_config_hooks.py -q
```

Result:

```text
46 passed in 2.53s
```

## Shape Of The Codebase

The indexed code graph showed a small top-level core with most high-risk
runtime behavior concentrated in plugins and extracted workspace packages.

Key stability surfaces:

- `kollabor/application.py` owns application startup, deferred startup,
  shutdown, core service wiring, plugin initialization, and terminal cleanup.
- `plugins/hub/plugin.py` is the largest runtime surface. It owns hub identity,
  sockets, presence, task routing, cron, bridge polling, agent spawn/stop,
  vault state, DNS/trust state, and several background loops.
- `plugins/hub/messenger.py` owns local and remote socket protocol handling.
- `packages/kollabor-events` owns hook registration, ordered event processing,
  timeout/retry behavior, and event cancellation.
- `packages/kollabor-agent` owns MCP integration, process management, tool
  execution, queue processing, permissions, and native tool routing.
- `packages/kollabor-tui` owns raw terminal state, render loop, fullscreen and
  alternate-buffer behavior, status widgets, and attach display state.
- `packages/kollabor-config` owns merged config, path helpers, file watching,
  reload callbacks, and hot reload.

The main pattern: core packages are fairly modular, but the most fragile
behavior is cross-cutting. A failure in one layer can break another layer:
plugin startup affects app readiness, config reload callbacks can touch render
state, MCP hooks depend on event cancellation being honored, and terminal
cleanup depends on unrelated shutdown awaits not raising first.

## Highest Priority Findings

### 1. Plugin Startup Is Not Isolated Per Plugin

Severity: high

Evidence:

- `kollabor/application.py:2256`
- `kollabor/application.py:2291`
- `kollabor/application.py:2298`
- `kollabor/application.py:1095`
- `kollabor/application.py:1101`
- `packages/kollabor-plugins/README.md:5`
- `packages/kollabor-plugins/README.md:16`

Normal plugin initialization calls each plugin's `initialize()` and
`register_hooks()` without a per-plugin failure boundary. One exception can
abort the rest of the initialization loop. Deferred startup catches the broad
exception, logs it, and then always sets startup ready in `finally`.

Why it destabilizes the program:

- Plugin A can fail and prevent Plugin B from initializing.
- The application can accept input while core plugin services are missing.
- The docs claim the plugin framework collects startup/status information
  without crashing the host app, but normal startup does not enforce that
  contract.

Investigation and fix area:

- Add per-plugin try/except around initialize and register_hooks.
- Track failed plugin state in a registry or startup report.
- Keep initializing unrelated plugins after one plugin fails.
- Surface degraded startup to the user before normal input is accepted.
- Add a test where plugin A fails but plugin B still initializes.

### 2. Terminal Restoration Happens Too Late In App Shutdown

Severity: high

Evidence:

- `kollabor/application.py:3108`
- `kollabor/application.py:3112`
- `kollabor/application.py:3115`
- `kollabor/application.py:3126`
- `kollabor/application.py:3135`
- `docs/architecture/terminal-rendering-architecture.md:206`
- `CLAUDE.md:117`

Application shutdown awaits script refresh shutdown, input shutdown, LLM
shutdown, conversation logger shutdown, MCP shutdown, version check shutdown,
and plugin shutdown before clearing the active area, exiting raw mode, and
showing the cursor.

Why it destabilizes the program:

- Any unhandled exception before terminal cleanup can leave raw mode active.
- Cursor visibility can stay hidden.
- The user's terminal can be left corrupted even when the app itself exits.

Investigation and fix area:

- Move terminal restoration into an early, best-effort `finally`.
- Wrap each service shutdown independently.
- Make terminal cleanup idempotent.
- Add a regression where one service shutdown raises and raw mode is still
  restored.

### 3. Fullscreen Cleanup Can Skip Alternate-Buffer Restoration

Severity: high

Evidence:

- `packages/kollabor-tui/src/kollabor_tui/fullscreen/renderer.py:98`
- `packages/kollabor-tui/src/kollabor_tui/fullscreen/session.py:425`
- `packages/kollabor-tui/src/kollabor_tui/fullscreen/session.py:428`
- `docs/architecture/reference/altview-framework-reference.md:60`
- `docs/architecture/reference/altview-framework-reference.md:69`
- `docs/architecture/terminal-rendering-architecture.md:206`
- `CLAUDE.md:127`

The fullscreen renderer enters the alternate buffer during setup. Cleanup
awaits `plugin.on_stop()` before calling `restore_terminal()`, and the whole
cleanup body is inside one broad try block.

Why it destabilizes the program:

- A plugin `on_stop()` exception can skip terminal restoration.
- Hook unregister and plugin cleanup can also be skipped.
- The documented fullscreen/alternate-buffer contract says restoration belongs
  in a `finally`.

Investigation and fix area:

- Restore terminal in a protected finally that cannot be blocked by plugin
  callbacks.
- Run hook unregister and plugin cleanup in independent guarded sections.
- Add a test with a throwing fullscreen plugin and assert the alternate buffer
  exits.

### 4. Config File Watcher Runs Reload Work On The Wrong Thread

Severity: high

Evidence:

- `packages/kollabor-config/src/kollabor_config/service.py:55`
- `packages/kollabor-config/src/kollabor_config/service.py:62`
- `packages/kollabor-config/src/kollabor_config/service.py:67`
- `packages/kollabor-config/src/kollabor_config/service.py:581`
- `packages/kollabor-config/README.md:18`
- `packages/kollabor-config/README.md:51`

The watchdog callback tries `asyncio.get_running_loop()` from the observer
thread. In that context it usually fails, then falls back to synchronous reload.
Reload callbacks can touch LLM, render, or service state from the watcher
thread.

Why it destabilizes the program:

- UI/runtime state can be mutated off the main event loop.
- Race conditions may appear only during live config saves.
- Hot reload behavior differs depending on watchdog availability and thread
  timing.

Investigation and fix area:

- Capture the application loop when `ConfigService` starts file watching.
- Schedule reload and callback notification onto that loop.
- Define sync callback behavior explicitly.
- Add a test proving reload callbacks run on the app loop, not the watchdog
  thread.

### 5. Config Watcher Is Not Shut Down By Application Shutdown

Severity: high

Evidence:

- `packages/kollabor-config/src/kollabor_config/service.py:605`
- `kollabor/application.py:3086`

`ConfigService` has a `shutdown()` method that stops the observer, but
application shutdown does not call `self.config.shutdown()`.

Why it destabilizes the program:

- Watchdog observer threads can outlive the app.
- File-change callbacks may fire during or after teardown.
- Tests and repeated local runs can inherit leaked watcher state.

Investigation and fix area:

- Call config shutdown during app shutdown.
- Guard it independently from other shutdown steps.
- Add a leak test that no watchdog observer thread remains after cleanup.

### 6. MCP Pre-Hook Cancellation Is Ignored

Severity: high

Evidence:

- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:1027`
- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:1059`
- `docs/features/config-hooks.md:39`
- `docs/features/config-hooks.md:175`
- `docs/features/config-hooks.md:199`
- `docs/features/permissions.md:13`
- `docs/mcp/MCP_SETUP.md:404`

`call_mcp_tool()` emits `MCP_TOOL_CALL_PRE`, but ignores the returned event
result and proceeds to `connection.call_tool()`. Config hooks can deny by exit
code `2` or stdout JSON, but MCP execution still continues.

Why it destabilizes the program:

- Security and policy hooks can appear to block but fail open for MCP tools.
- A denied flaky or destructive MCP call can still hit the server.
- The docs say MCP tools respect the permission system and config hooks can
  block events.

Investigation and fix area:

- Capture `emit_with_hooks()` result before MCP execution.
- If cancelled, return a blocked result without touching the server.
- Apply transformed event data if hooks change parameters.
- Add tests for exit code `2`, `continue: false`, and `decision: deny`.

## Medium Priority Findings

### 7. Hub Startup Task Is Fire-And-Forget And Unowned

Severity: medium

Evidence:

- `plugins/hub/plugin.py:3545`
- `plugins/hub/plugin.py:3551`
- `plugins/hub/plugin.py:3751`
- `plugins/hub/plugin.py:3904`
- `plugins/hub/plugin.py:4168`

`register_hooks()` schedules `_start_hub()` in a background task after startup.
The task is not stored. Shutdown cancels the loops started after successful
startup, but not an in-flight startup task.

Why it destabilizes the program:

- Shutdown can race startup.
- Startup can publish presence, bind sockets, or register services during
  teardown.
- Partial startup failures can leave stale hub state.

Investigation and fix area:

- Store the hub startup task.
- Cancel and await it during shutdown.
- Add a shutdown guard checked inside `_start_hub()`.
- Add a test where shutdown happens while `_start_hub()` is in progress.

### 8. Config Hook Timeout Can Leak Descendant Processes

Severity: medium

Evidence:

- `kollabor/config_hooks.py:453`
- `kollabor/config_hooks.py:459`
- `kollabor/config_hooks.py:466`
- `docs/features/config-hooks.md:84`
- `docs/features/config-hooks.md:338`

Config hooks start commands with `start_new_session=True`, but timeout handling
only calls `proc.kill()` on the direct child.

Why it destabilizes the program:

- Hook scripts that spawn child processes can leak descendants after timeout.
- Repeated hook timeouts can leave stray processes consuming resources.
- The docs imply clean timeout cleanup.

Investigation and fix area:

- Kill the whole process group on timeout.
- Preserve current direct-child behavior as fallback.
- Add a test with a hook that starts a sleeping child.

### 9. Event Bus Has No Event-Level Backpressure Contract

Severity: medium

Evidence:

- `packages/kollabor-events/src/kollabor_events/bus.py:101`
- `packages/kollabor-events/src/kollabor_events/processor.py:171`
- `packages/kollabor-events/src/kollabor_events/executor.py:155`
- `packages/kollabor-events/src/kollabor_events/executor.py:236`
- `docs/architecture/event-system-architecture.md:251`
- `packages/kollabor-events/README.md:77`

Hooks run serially in event processing, with inline retries and exponential
backoff. The docs say hooks should not block, but there is no event-level
budget, queue, observer-vs-blocking contract, or metrics requirement.

Why it destabilizes the program:

- One slow hook can stall user input, render, or tool events.
- Retries can multiply latency in the foreground path.
- There is no standard way to know whether a hook is allowed to block.

Investigation and fix area:

- Define hook classes: blocking policy hooks versus observer hooks.
- Add per-event budgets and diagnostics.
- Consider bounded queues for observer hooks.
- Add tests or probes for slow hook behavior.

### 10. Shutdown Can Call The Same Plugin Instance Twice

Severity: medium

Evidence:

- `packages/kollabor-plugins/src/kollabor_plugins/factory.py:67`
- `kollabor/application.py:2253`
- `kollabor/application.py:3126`

Plugin factory aliases can point to the same plugin instance. Initialization
dedupes by instance id, but shutdown iterates `plugin_instances.items()` without
deduping.

Why it destabilizes the program:

- Plugin shutdown may run twice.
- Non-idempotent cleanup can double-close sockets, tasks, or files.
- Errors from the second shutdown can mask the real state.

Investigation and fix area:

- Dedupe shutdown by `id(plugin_instance)`.
- Preserve plugin-name logging for aliases.
- Add a regression for duplicate aliases.

### 11. Agent Orchestrator Spawns Trusted Children With Raw Environment

Severity: medium

Evidence:

- `plugins/agent_orchestrator/orchestrator.py:559`
- `plugins/agent_orchestrator/orchestrator.py:565`
- `plugins/agent_orchestrator/orchestrator.py:598`
- `plugins/agent_orchestrator/orchestrator.py:605`

The legacy agent orchestrator copies the full parent environment into child
agents and launches them with `--permissions trust`.

Why it destabilizes the program:

- Secrets and local env state leak into trusted child subprocesses.
- Behavior can vary based on unrelated parent env variables.
- The newer process manager path appears to have tighter env filtering, so
  behavior diverges by spawn path.

Investigation and fix area:

- Centralize child environment filtering.
- Explicitly allow only required `KOLLAB_*` and profile/runtime variables.
- Add tests matching the process-manager env-filter coverage.

### 12. MCP Discovery Is Serial And Slow-Failure Prone

Severity: medium

Evidence:

- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:63`
- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:603`
- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:665`
- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:776`
- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:861`
- `docs/features/tools.md:234`
- `docs/features/tools.md:498`

MCP discovery walks configured servers serially. A bad server can sit for a
long request timeout and block discovery for every later server.

Why it destabilizes the program:

- One hung MCP server can degrade startup or reload.
- Healthy servers later in the list are delayed by unrelated failures.
- User-facing "MCP is broken" may actually be "one server is wedged".

Investigation and fix area:

- Use shorter discovery/list-tools timeouts than runtime call timeouts.
- Discover servers concurrently with a bounded limit.
- Preserve partial results.
- Add a test with one hung server and one healthy server.

### 13. Process Manager Strategy Lookup Can Drift After Strategy Change

Severity: medium

Evidence:

- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:542`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:550`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:592`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:599`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:767`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:780`

Managed processes appear to store the strategy name rather than the strategy
instance. If the current strategy changes later, kill/send/snapshot operations
can route to a different strategy than the one that created the process.

Why it destabilizes the program:

- Existing process handles may be managed by the wrong backend.
- Kill or send behavior can fail after strategy changes.
- The code contract described by comments can diverge from runtime behavior.

Investigation and fix area:

- Store the strategy instance or stable backend handle on `ManagedProcess`.
- Add a kill-after-strategy-swap test.

### 14. Active MCP Calls Are Not Cancelled Promptly

Severity: medium

Evidence:

- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:313`
- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:321`
- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:494`
- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:515`
- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:864`
- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:871`

Cancellation checks happen before and between tool calls, but not necessarily
inside a currently active MCP call.

Why it destabilizes the program:

- User stop can wait until the full MCP timeout.
- A stuck MCP server can make the UI feel unresponsive.
- Tool cancellation semantics are weaker for MCP than for the surrounding tool
  queue.

Investigation and fix area:

- Track the active MCP task and cancel it when the user cancels.
- Close or reset the connection after cancellation.
- Add a test where cancel during a slow MCP call returns quickly.

### 15. Fire-And-Forget Renders Can Race The Main Render Loop

Severity: medium

Evidence:

- `packages/kollabor-tui/src/kollabor_tui/message_coordinator.py:449`
- `packages/kollabor-tui/src/kollabor_tui/message_coordinator.py:563`

Message display paths create background `render_active_area()` tasks directly.
Those task exceptions are not observed by the main render loop exception fence,
and direct renders can overlap normal loop renders.

Why it destabilizes the program:

- Render exceptions can be lost.
- UI state can be rendered concurrently.
- Terminal flicker or stale active area state may appear under load.

Investigation and fix area:

- Prefer `render_loop.request_render()` for render scheduling.
- If direct tasks remain, attach done callbacks that log exceptions.
- Add serialization around active-area renders.

### 16. Deferred Startup Marks Ready After Partial Failure

Severity: medium

Evidence:

- `kollabor/application.py:1095`
- `kollabor/application.py:1101`
- `docs/reference/conversation-pipeline.md:143`
- `docs/one-pager.md:24`

Deferred startup catches broad exceptions and then always sets the startup
event to avoid deadlocks.

Why it destabilizes the program:

- Input can proceed with missing services.
- Startup can appear healthy even when important wiring failed.
- A "ready" app may be degraded without a clear user-visible signal.

Investigation and fix area:

- Separate fatal startup failure from degraded-but-usable startup.
- Store and display startup error state.
- Gate normal input if required core services failed.

## Lower Priority Findings

### 17. Status Widget Render Output Is Not Type-Coerced At The Boundary

Severity: low

Evidence:

- `packages/kollabor-tui/src/kollabor_tui/status/widget_registry.py:117`
- `packages/kollabor-tui/src/kollabor_tui/status/layout_renderer.py:397`

Widget exceptions are caught, but a render function can return a truthy
non-string. Layout measurement can then call ANSI stripping on a non-string.

Why it destabilizes the program:

- A bad plugin widget can poison status row rendering.
- The failure is avoidable at the registry boundary.

Investigation and fix area:

- Coerce widget render output to `str`.
- Treat `None` as empty string.
- Log invalid return types once per widget id.

### 18. PluginFactory Async Lifecycle Helpers Are Misleading

Severity: low

Evidence:

- `packages/kollabor-plugins/src/kollabor_plugins/factory.py:162`
- `packages/kollabor-plugins/src/kollabor_plugins/factory.py:165`
- `packages/kollabor-plugins/src/kollabor_plugins/factory.py:212`
- `packages/kollabor-plugins/src/kollabor_plugins/factory.py:215`

Factory lifecycle helpers call async plugin methods through sync safe-execute
helpers and can treat the returned coroutine object as success. The app does
not appear to use these paths, but they are public and tests can miss the real
async behavior.

Why it destabilizes the program:

- Future callers may believe plugin initialize/shutdown actually ran.
- Tests that mock safe-execute can pass while real async behavior is broken.

Investigation and fix area:

- Make helpers async and await plugin methods.
- Or delete/deprecate them if the application owns lifecycle directly.

## Contract Coverage

Not every finding has a clear docs contract.

Existing contracts that implementation appears to violate:

- Plugin lifecycle should be safe and should not crash the host app.
  Source: `packages/kollabor-plugins/README.md`.
- Config hooks can block events through exit code `2`, `continue: false`, or
  `decision: deny`.
  Source: `docs/features/config-hooks.md`.
- MCP tools respect the permission system.
  Source: `docs/features/permissions.md` and `docs/mcp/MCP_SETUP.md`.
- Fullscreen/alternate-buffer code must restore terminal state in `finally`.
  Source: `CLAUDE.md` and `docs/architecture/terminal-rendering-architecture.md`.

Partial contracts that need tightening:

- Application terminal shutdown order is implied by terminal lifecycle docs but
  not explicitly specified.
- Config hot reload exists, but loop ownership and shutdown semantics are not
  specified.
- Hub project scoping has an RFC, but early layered-config behavior is not
  explicit.
- Config hook timeouts are documented, but descendant process-group cleanup is
  not explicit.
- MCP discovery is documented, but slow-failure isolation is not specified.

Missing contracts:

- Hub startup task ownership and cancellation during shutdown.
- Event-level backpressure, hook budget, and observer-vs-blocking behavior.
- Agent child-process environment allowlist for trusted spawned agents.
- Render scheduling ownership for message coordinator versus main render loop.

## Recommended Stabilization Order

1. Make shutdown terminal-safe.
   - Move terminal restoration into a protected early/final cleanup.
   - Guard each service shutdown independently.

2. Isolate plugin startup and shutdown.
   - Per-plugin try/except.
   - Failed plugin tracking.
   - Dedupe shutdown by plugin instance id.

3. Fix config watcher ownership.
   - Capture the app loop.
   - Schedule reload callbacks on the app loop.
   - Call `ConfigService.shutdown()` from app shutdown.

4. Enforce MCP pre-hook cancellation.
   - Honor `emit_with_hooks()` cancellation before MCP execution.
   - Apply transformed params if hooks mutate event data.

5. Own hub startup task lifecycle.
   - Store, cancel, and await `_start_hub()`.
   - Add a shutdown flag and partial-start cleanup.

6. Close subprocess stability gaps.
   - Kill config hook process groups on timeout.
   - Filter env for trusted child agents.

7. Add event/render stability contracts.
   - Define blocking versus observer hooks.
   - Add event-level timing diagnostics.
   - Centralize active-area render scheduling.

8. Harden MCP slow-failure behavior.
   - Separate discovery timeout from runtime MCP timeout.
   - Preserve partial discovery.
   - Add in-flight cancellation.

## Missing Regression Tests

High-value tests to add:

- Plugin A initialize fails; Plugin B still initializes.
- Plugin A register_hooks fails; Plugin B still registers.
- App shutdown restores terminal even if one service shutdown raises.
- Fullscreen plugin `on_stop()` raises; alternate buffer still exits.
- Config watcher reload callback runs on the app loop.
- App shutdown stops the ConfigService watchdog observer.
- MCP_TOOL_CALL_PRE exit code `2` blocks MCP execution.
- MCP_TOOL_CALL_PRE JSON denial blocks MCP execution.
- MCP hook-transformed params are the params sent to the server.
- Hub shutdown during `_start_hub()` leaves no presence, socket, or task.
- Config hook timeout kills process descendants.
- Duplicate plugin aliases are shut down once.
- Agent orchestrator spawn filters parent env.
- Slow MCP discovery server does not block healthy server discovery.
- Process manager kill still works after strategy swap.
- Cancel during active MCP call returns before full timeout.
- Status widget non-string return does not break layout.

## Runtime Verification Probes

After fixes, run both unit tests and real runtime probes:

- Start and immediately stop Kollab; verify terminal mode and cursor restore.
- Force a plugin initialization failure; verify the app reports degraded startup
  and other plugins remain usable.
- Run a throwing fullscreen plugin; verify alternate buffer exits.
- Touch config while app is running; verify reload callback executes on the app
  loop.
- Configure an MCP denial hook; verify the MCP server does not receive the call.
- Start hub and interrupt during startup; inspect presence files and socket dirs.
- Run a timed-out config hook that spawns a child; verify no descendant remains.

## Suggested Documentation Work

Create or update contracts for:

- Application shutdown ordering and terminal cleanup invariants.
- Config watcher loop ownership and shutdown.
- Plugin lifecycle isolation and degraded startup reporting.
- Hub background-task ownership.
- Event hook backpressure and blocking/observer classes.
- Trusted child-process env filtering.
- MCP discovery timeout and partial-result behavior.
- Message coordinator render scheduling.

## Acceptance Gates

These are the gates I would use before calling the stabilization pass complete.

Gate 1: terminal and shutdown safety

- App shutdown restores cursor, raw mode, and alternate buffer even when one
  service shutdown raises.
- Fullscreen cleanup restores the terminal even when a fullscreen plugin
  `on_stop()` raises.
- Every service shutdown branch is guarded independently and reports the
  failing service name.
- Targeted tests cover both app shutdown and fullscreen cleanup failure paths.

Gate 2: plugin lifecycle isolation

- One plugin failing `initialize()` cannot prevent unrelated plugins from
  initializing.
- One plugin failing `register_hooks()` cannot prevent unrelated plugins from
  registering hooks.
- Startup records plugin state as `ready`, `disabled`, or `failed`.
- Deferred startup can report degraded startup rather than silently marking the
  app ready.
- Duplicate plugin aliases are shut down once per instance.

Gate 3: config reload ownership

- Watchdog callbacks schedule reload work onto the app event loop.
- Reload callbacks never mutate runtime/UI state from the observer thread.
- Application shutdown calls `ConfigService.shutdown()`.
- Tests prove the observer stops and no reload callback fires after shutdown.

Gate 4: MCP policy enforcement

- `MCP_TOOL_CALL_PRE` cancellation stops before the MCP server is called.
- Exit code `2`, `continue: false`, and `decision: deny` all block execution.
- Hook-transformed MCP parameters are the parameters sent to the server.
- Slow or cancelled MCP calls return promptly and do not wedge the tool queue.

Gate 5: hub lifecycle ownership

- The `_start_hub()` task is stored, observed, cancelled, and awaited.
- Shutdown during startup leaves no new presence publish, socket bind, service
  registration, or background task after shutdown begins.
- Hub task cancellation is factored through one helper and covers every task.
- Hub startup failures produce a visible degraded state instead of quiet
  partial mesh state.

Gate 6: hub remote trust

- Remote transport-authenticated identity is bound to the `HubMessage`
  sender before display, vault logging, task creation, or LLM wake.
- Unknown or unapproved remote senders are quarantined at inbound receive time,
  not only during outbound route decisions.
- `RemoteEnvelopeVerifier` either becomes real cryptographic verification and
  is wired into the receive path, or is renamed/documented as a stub contract.
- `/hub dns connect` reports the TOFU boundary and stale-key behavior clearly.
- Tests cover authenticated remote sender mismatch, unknown remote inbound
  quarantine, stale remote key refresh, and plaintext endpoint warnings.

Gate 7: runtime proof

- Baseline unit tests pass.
- Hub-focused tests pass.
- A start/stop probe leaves terminal state normal and hub dirs clean.
- A remote endpoint loopback probe proves TLS/handshake/message delivery for
  the supported path and rejection for an unregistered peer.

## Fix Backlog

P0: must fix before trusting normal interactive runs

- Move terminal restore into an early protected cleanup path.
- Make fullscreen alternate-buffer restoration impossible to skip.
- Honor MCP pre-hook cancellation before network/tool execution.
- Store and cancel the hub startup task.
- Add inbound remote sender binding before a remote message can wake the LLM.

P1: should fix in the same stabilization wave

- Isolate plugin `initialize()` and `register_hooks()` per plugin.
- Track degraded startup state and expose it in startup/status output.
- Capture the app loop in `ConfigService` and run reload callbacks there.
- Call `ConfigService.shutdown()` from app shutdown.
- Kill config-hook process groups on timeout.
- Dedupe plugin shutdown by instance id.

P2: important hardening after the lifecycle fixes land

- Add hook budgets and observer-vs-blocking hook classes.
- Filter child-agent environment in every trusted spawn path.
- Split MCP discovery timeout from runtime tool-call timeout.
- Track active MCP calls so cancellation interrupts a stuck call.
- Store process-manager strategy instances on managed process records.
- Centralize message coordinator render scheduling.

P3: cleanup and maintainability

- Break `HubPlugin` into lifecycle, transport, delivery, dns, commands, and
  state modules.
- Deprecate or fix unused async lifecycle helpers in `PluginFactory`.
- Add docs for the process boundaries that are currently implicit.

## Contract Delta

Contracts to add or tighten:

- App shutdown contract:
  terminal state must be restored before any best-effort service cleanup can
  block or raise.
- Plugin lifecycle contract:
  plugin failures are isolated, recorded, and visible; aliases cannot cause
  duplicate shutdown.
- Config reload contract:
  reload callbacks run on the app loop, and config watching has an explicit
  shutdown owner.
- MCP hook contract:
  `MCP_TOOL_CALL_PRE` has the same blocking semantics as documented config
  hooks and permissions.
- Hub lifecycle contract:
  hub startup, socket ownership, presence publication, background loops, and
  shutdown are a single owned lifecycle.
- Hub remote trust contract:
  remote transport identity, message sender, DNS approval state, and LLM wake
  eligibility must be evaluated before an inbound message mutates local state.
- Event hook contract:
  hooks declare whether they are blocking policy hooks or non-blocking
  observers, with budgets and diagnostics for foreground events.
- Spawn environment contract:
  trusted child agents inherit only an explicit allowlist of environment
  values.

Contracts already present but not yet fully enforced:

- `docs/specs/hub-remote-endpoint.md` documents endpoint fail-closed TLS
  behavior and TOFU federation, but the receive path still needs an explicit
  sender-binding contract.
- `docs/features/config-hooks.md` documents hook denial, but MCP call
  execution currently needs to consume that result.
- `CLAUDE.md` and terminal architecture docs describe terminal cleanup rules,
  but app and fullscreen shutdown need stronger code fences.

## Repro Probes

Run these as targeted probes after the corresponding fixes. Some are tests to
add first; the current suite does not cover all of them.

Terminal cleanup probe:

```bash
python -m pytest tests/unit/test_app_shutdown_terminal_restore.py -q
python -m pytest tests/unit/test_fullscreen_terminal_restore.py -q
```

Plugin isolation probe:

```bash
python -m pytest tests/unit/test_plugin_lifecycle_isolation.py -q
```

Config watcher probe:

```bash
python -m pytest tests/unit/test_config_reload_loop_ownership.py -q
python -m pytest tests/unit/test_config_service_shutdown.py -q
```

MCP policy probe:

```bash
python -m pytest tests/unit/test_mcp_pre_hook_cancellation.py -q
python -m pytest tests/unit/test_tool_executor_cancellation.py -q
```

Hub lifecycle probe:

```bash
python -m pytest tests/unit/test_hub_stop_lifecycle.py \
  tests/unit/test_hub_stop_restart.py \
  tests/unit/test_hub_spawn_guard.py \
  tests/unit/test_hub_start_shutdown_race.py -q
```

Hub remote trust probe:

```bash
python -m pytest tests/unit/test_hub_endpoint.py \
  tests/unit/test_hub_delivery_policy.py \
  tests/unit/test_hub_remote_trust.py \
  tests/unit/test_hub_remote_receive_policy.py -q
```

Runtime smoke probe:

```bash
python main.py --help
python -m pytest tests/unit/test_event_bus.py \
  tests/unit/test_config_hooks.py \
  tests/unit/test_hub_stop_lifecycle.py -q
```

Manual runtime checks:

- Start Kollab, interrupt during startup, then inspect that the hub presence
  file and socket for that identity are gone or not republished.
- Start with `plugins.hub.endpoint_enabled=true` and missing cert/key; verify
  the remote listener does not bind unless `endpoint_allow_insecure=true`.
- Import a remote well-known record, then attempt a message with mismatched
  authenticated identity and `from_identity`; verify it is rejected or
  quarantined before LLM wake.
- Configure a hook that denies an MCP call; verify the MCP server never sees
  the request.

## Stability Scorecard

Startup readiness: weak

- Deferred startup can report ready after partial failure.
- Hub startup is deferred into an unowned background task.
- Plugin startup is not isolated per plugin.

Shutdown safety: weak

- App terminal restoration happens after many awaited shutdown branches.
- Fullscreen cleanup can skip terminal restore when plugin cleanup raises.
- Hub shutdown cancels known loops, but not the startup task that creates them.

Hot reload: weak

- Config reload can run callbacks on the watchdog thread.
- Config watcher has a shutdown method, but app shutdown does not call it.

Policy enforcement: mixed

- Config-hook denial is documented.
- MCP pre-hook denial currently needs enforcement before server calls.
- Hub delivery policy exists, but inbound remote receive policy is incomplete.

Hub transport: mixed

- Endpoint defaults are disabled.
- TLS setup fails closed unless insecure mode is explicitly allowed.
- Loopback endpoint tests cover handshake, delivery, idle timeout, failed bind,
  and unregistered-client rejection.
- Remote key expiry and inbound sender binding are still gaps.

Observability: mixed

- Hub delivery trace exists.
- Endpoint bind/setup errors are surfaced.
- Plugin/deferred startup degraded state is not structured enough.
- Event-hook latency and queue pressure are not measured as a contract.

Test coverage: mixed

- Event bus, config hooks, hub stop, hub endpoint, hub delivery, and spawn guard
  have useful tests.
- Missing tests are concentrated around failure boundaries, races, and
  policy-denial paths.

Maintainability: weak around hub

- The hub plugin is a 9,910-line class owning too many independent lifecycles.
- Supporting modules exist, but the orchestration center still couples
  transport, trust, persistence, commands, tasks, cron, UI, and process control.

## Non-Findings

These areas looked intentionally handled and should not be treated as current
bugs without new evidence:

- Hub endpoint is disabled by default.
- Endpoint startup refuses an unencrypted public listener when TLS cert/key are
  missing and `endpoint_allow_insecure=false`.
- TCP endpoint bind failure is non-fatal to the local unix socket path.
- Remote off-box connections have a 30-second idle read timeout.
- Durable inboxes have size and age bounds.
- Stop-peer behavior waits for graceful exit, then escalates and verifies exit.
- Spawn guard publishes preliminary presence to reduce duplicate identity races.
- Delivery policy has a quarantine mode for unknown remote senders.
- Baseline event bus, hub stop lifecycle, and config-hook tests passed during
  the review.

## Dependency Order

Fix order should avoid masking root causes:

1. Terminal cleanup first.
   This protects the user's shell while every other stabilization change is
   still being tested.

2. Plugin lifecycle isolation second.
   Hub and config fixes are easier to reason about once one plugin cannot
   prevent unrelated plugins from registering.

3. Config watcher ownership third.
   It removes off-loop mutations that can make later runtime probes flaky.

4. MCP pre-hook enforcement fourth.
   It closes the clearest docs-contract violation and gives permission tests a
   stable boundary.

5. Hub startup/shutdown ownership fifth.
   Once plugin lifecycle is isolated, hub can own its startup task and teardown
   without depending on undefined app behavior.

6. Hub remote receive policy sixth.
   This should land after hub lifecycle ownership so remote trust tests do not
   fight startup/shutdown races.

7. Subprocess/env, MCP slow-failure, event budgets, and render scheduling last.
   They are important, but they depend less directly on the highest-risk
   terminal/startup/shutdown paths.

## Deep Hub Plugin Review

Hub shape:

- `plugins/hub/plugin.py` is 9,910 lines.
- `HubPlugin.__init__()` alone owns identity, presence, socket server, endpoint
  state, RPC, vaults, crystals, task ledger, change feed, session state,
  scratchpad, nudge engine, eight background task handles, bridge polling,
  DNS/trust registries, activity state, loop metrics, message dedup, and a
  history lock.
- `_do_initialize()` wires project scoping, config reload callbacks, identity,
  presence startup scan, stale vault archival, display tap, coordinator
  election, work queue, commands, and pipeline tools.
- `register_hooks()` registers six hooks, then schedules `_start_hub()` with
  `asyncio.ensure_future()` through a `call_soon()` lambda.
- `_start_hub()` owns identity assignment, preliminary presence, agent bundle
  reconciliation, socket creation, optional A2A endpoint setup, RPC setup,
  vault/crystal setup, DNS/trust setup, change feed, rebirth context injection,
  final presence, service registration, background loop creation, bridge
  forwarding, peer announce, org launch, and final `_started = True`.
- `shutdown()` saves vault/session state, releases claims, cancels each known
  loop, broadcasts departure, stops the socket server, removes presence,
  releases election, deletes the local message directory, and may `os._exit(0)`
  for self-stop.

Hub-specific findings:

1. Hub startup has no owned task handle.

Evidence:

- `plugins/hub/plugin.py:3545`
- `plugins/hub/plugin.py:3551`
- `plugins/hub/plugin.py:3612`
- `plugins/hub/plugin.py:4168`
- `plugins/hub/plugin.py:4304`
- `plugins/hub/plugin.py:9714`

`register_hooks()` starts `_start_hub()` as an untracked task. Shutdown cancels
tasks that were created after successful startup, but it cannot cancel or await
the startup task itself. The riskiest window is after preliminary presence at
`plugins/hub/plugin.py:3751` and before final `_started = True` at
`plugins/hub/plugin.py:4304`.

Fix direction:

- Add `self._startup_task`.
- Set a shutdown flag before any teardown work.
- Check the flag inside `_start_hub()` after each external side effect.
- On startup failure or cancellation, clean up partial presence/socket/RPC
  state in a local finally.
- Add a race test where shutdown starts while `_start_hub()` is between
  preliminary presence and socket start.

2. Hub shutdown is too manual and easy to drift.

Evidence:

- `plugins/hub/plugin.py:9783`
- `plugins/hub/plugin.py:9790`
- `plugins/hub/plugin.py:9797`
- `plugins/hub/plugin.py:9804`
- `plugins/hub/plugin.py:9811`
- `plugins/hub/plugin.py:9818`
- `plugins/hub/plugin.py:9825`
- `plugins/hub/plugin.py:9832`

Every hub background task gets its own cancel/await block. That makes it easy
to forget new tasks and hard to enforce consistent logging, timeouts, and
exception handling.

Fix direction:

- Replace repeated blocks with `_cancel_task(name, task, timeout=...)`.
- Include startup task, self-stop watchdog task, and any future bridge/cron
  child tasks in one task registry.
- Reset task handles to `None` after cancellation.
- Add a test that every registered task is cancelled exactly once.

3. Inbound remote identity is authenticated but not bound to message sender.

Evidence:

- `plugins/hub/messenger.py:492`
- `plugins/hub/messenger.py:501`
- `plugins/hub/messenger.py:534`
- `plugins/hub/messenger.py:535`
- `plugins/hub/plugin.py:5631`
- `plugins/hub/plugin.py:5771`
- `plugins/hub/plugin.py:5847`
- `plugins/hub/plugin.py:6044`

The socket server authenticates the remote stream as `authenticated_as`, logs
it, then parses `HubMessage.from_dict(msg_data)` and passes the message through
without binding `authenticated_as` to `message.from_identity` or marking the
message as remote. `_on_message_received()` can display the message, create a
task card, append conversation history, and trigger `TRIGGER_LLM_CONTINUE`.

This means the strongest remote trust boundary is the transport handshake, but
the received message's claimed sender is still payload-controlled unless a
later path validates it.

Fix direction:

- Pass connection context into `_on_message`.
- For `require_auth=True`, require `message.from_identity == authenticated_as`
  or overwrite the displayed sender with the authenticated identity.
- Set metadata such as `remote=true`, `authenticated_as`, and
  `remote_authority`.
- Run delivery/receive policy before vault logging, UI display, task creation,
  bridge forwarding, or LLM wake.
- Add tests for sender mismatch, unknown remote, approved remote, and rejected
  remote.

4. The remote envelope verifier is a contract shell, not enforcement.

Evidence:

- `plugins/hub/remote_envelope.py:1`
- `plugins/hub/remote_envelope.py:4`
- `plugins/hub/remote_envelope.py:36`
- `plugins/hub/remote_envelope.py:39`
- `tests/unit/test_hub_remote_trust.py:4`
- `tests/unit/test_hub_remote_trust.py:22`
- `tests/unit/test_hub_remote_trust.py:41`

`RemoteEnvelopeVerifier` accepts any non-empty signature for an approved sender.
Search results only found it in its module, tests, and planning docs; it does
not appear wired into the receive path. The module comment explicitly says
stricter signature verification is future work.

Fix direction:

- Decide whether remote trust is enforced only by the Ed25519 stream handshake
  or also by signed message envelopes.
- If envelope signing stays, make it verify signature over canonical
  `(sender, authority, message_id, timestamp, body_hash)` and wire it before
  receive-side mutation.
- If the stream handshake is the only intended boundary, remove or rename the
  verifier so tests do not create false confidence.

5. `/hub dns connect` is operationally useful but TOFU-heavy.

Evidence:

- `plugins/hub/dns/endpoint.py:194`
- `plugins/hub/dns/endpoint.py:221`
- `plugins/hub/dns/endpoint.py:229`
- `plugins/hub/plugin.py:7615`
- `plugins/hub/plugin.py:7626`
- `plugins/hub/plugin.py:7629`
- `docs/specs/hub-remote-endpoint.md:81`
- `docs/specs/hub-remote-endpoint.md:100`
- `docs/specs/hub-remote-endpoint.md:131`

`register_well_known()` imports a remote coordinator as
`approval_state="approved"`. The docs correctly call the model TOFU and note
that remote key
refresh/expiry is not automated. That is acceptable if it is treated as an
explicit trust decision, but it should not look like a normal health check.

Fix direction:

- Make `/hub dns connect` print a concise trust warning and the exact
  authority/designation/key fingerprint imported.
- Store first-seen and last-verified timestamps for remote imports.
- Add stale-key reporting to `/hub dns endpoint` or `/hub dns resolve`.
- Consider `pending` rather than `approved` for new remote authorities unless a
  config flag opts into automatic TOFU approval.

6. Hub delivery policy is real but applied in the wrong place for remote
inbound messages.

Evidence:

- `plugins/hub/delivery.py:34`
- `plugins/hub/delivery.py:55`
- `plugins/hub/plugin.py:6816`
- `plugins/hub/plugin.py:6909`
- `tests/unit/test_hub_delivery_policy.py:66`

`DeliveryPolicy` correctly quarantines unknown remote senders. But
`_decide_sender_delivery()` is called from `_route_message()` for messages this
local process is routing out. Inbound messages received from the socket enter
`_on_message_received()` directly, where display and wake decisions happen.

Fix direction:

- Add an inbound receive policy seam separate from outbound route policy.
- Feed it authenticated connection context, DNS approval state, project scope,
  and force flag.
- Quarantine before display or LLM wake.

7. Hub has useful focused tests, but the missing tests are around lifecycle
and trust boundaries.

Existing useful coverage:

- `tests/unit/test_hub_stop_lifecycle.py` covers stop escalation and concurrent
  stop-all behavior.
- `tests/unit/test_hub_spawn_guard.py` covers duplicate identity guard and
  preliminary presence.
- `tests/unit/test_hub_endpoint.py` covers endpoint URI helpers, TLS context,
  well-known import, bind failure, idle timeout, off-box delivery,
  unregistered-client rejection, and TLS round trip.
- `tests/unit/test_hub_delivery_policy.py` covers local unknown sender,
  rejected sender, and unknown remote quarantine at policy level.

Missing hub tests:

- Shutdown while `_start_hub()` is in flight.
- Startup failure after preliminary presence but before `_started`.
- Remote authenticated identity mismatch against `from_identity`.
- Inbound remote message quarantine before task ledger, display, bridge, and
  LLM wake.
- Remote key stale/rotated behavior after `/hub dns connect`.
- Task registry cancellation coverage for every hub background task.

Hub refactor boundary:

Do not rewrite the hub in one pass. Stabilize first, then split:

1. `hub/lifecycle.py`: startup task, shutdown, task registry, partial cleanup.
2. `hub/transport.py`: socket server, endpoint, authenticated connection
   context.
3. `hub/trust.py`: DNS approval, remote receive policy, envelope/handshake
   sender binding.
4. `hub/routing.py`: route, deliver, durable inbox, delivery trace.
5. `hub/commands.py`: `/hub` command parsing and handlers.
6. `hub/state.py`: vault, scratchpad, task ledger, change feed, session state.

The key is to add contracts and tests before moving code. Otherwise a split
will just spread the same lifecycle uncertainty across more files.

## Additional Findings From Continued Pass

### 19. Context Compaction Can Fail When Everything Old Is Preserved

Severity: medium

Evidence:

- `plugins/context_compaction_plugin.py:1071`
- `plugins/context_compaction_plugin.py:1129`
- `plugins/context_compaction_plugin.py:1140`
- `plugins/context_compaction_plugin.py:1194`
- `plugins/context_compaction_plugin.py:1217`
- `plugins/context_compaction_plugin.py:1229`

`_run_compaction()` only sets `summary_text` when `summarizable` is non-empty.
If the old messages are all ledger-handled or task-preserved, compaction can
still build a compacted history with `summary_text or ""`, write a checkpoint,
and then call `_log_compaction_event(summary_length=len(summary_text))`.
`summary_text` is still `None`, so that path raises after doing checkpoint
work and before staging the compaction.

Why it destabilizes the program:

- A valid "nothing to summarize, only preserve" compaction shape is treated as
  a failure.
- Consecutive failure tracking can disable compaction even though the input was
  safe.
- The checkpoint side effect can happen without the matching staged compaction
  event.

Investigation and fix area:

- Use `len(summary_text or "")`.
- Avoid injecting an empty summary message when no summary was generated.
- Add a regression where all compacted messages are preserved tasks or
  ledger-handled messages.

### 20. Staged Compaction Can Overwrite Concurrent History Mutation

Severity: medium

Evidence:

- `plugins/context_compaction_plugin.py:1076`
- `plugins/context_compaction_plugin.py:1077`
- `plugins/context_compaction_plugin.py:808`
- `plugins/context_compaction_plugin.py:832`
- `plugins/context_compaction_plugin.py:838`

Compaction snapshots `history` in a background task, then later applies the
pending compacted history during `LLM_REQUEST_PRE`. The apply step checks
session id and whether the current history length is shorter than the original
snapshot length. It does not check that the original prefix is still the same
messages.

Why it destabilizes the program:

- Another component can edit, reorder, trim, or replace old history while
  preserving length.
- The apply step can overwrite those mutations with the stale compacted
  snapshot plus appended messages.
- These failures are hard to diagnose because the final history length looks
  plausible.

Investigation and fix area:

- Store a lightweight fingerprint of the snapshotted prefix.
- Apply only if the current prefix still matches.
- Or route all history mutation through a versioned state service.
- Add a test where history is mutated but length stays the same before apply.

### 21. Crystal Store Whole-File Writes Are Not Atomic Enough

Severity: medium

Evidence:

- `plugins/hub/crystal_store.py:108`
- `plugins/hub/crystal_store.py:166`
- `plugins/hub/crystal_store.py:169`
- `plugins/hub/crystal_store.py:170`
- `plugins/hub/crystal_store.py:217`
- `plugins/hub/crystal_store.py:265`

`CrystalStore` lazily loads and writes a whole `crystallized.md` file. The
write path builds the serialized content before acquiring the lock, then writes
directly with `Path.write_text()`. The lock only protects the write call inside
one process; it does not protect the in-memory snapshot construction, and it
does not coordinate across multiple agent processes.

Why it destabilizes the program:

- Two agents writing the same crystal file can clobber each other.
- A crash or interruption during `write_text()` can leave a truncated file.
- Concurrent first-load/write paths can work from stale in-memory state.

Investigation and fix area:

- Serialize under the same lock used for the write.
- Use temp-file plus `os.replace()` like the DNS storage path.
- Add a process-level file lock if multiple agents can share the same vault.
- Add a concurrent add/update regression.

### 22. DNS Merge-Save Does Not Hold The Lock Across Read-Modify-Write

Severity: medium

Evidence:

- `plugins/hub/dns/storage.py:38`
- `plugins/hub/dns/storage.py:45`
- `plugins/hub/dns/storage.py:128`
- `plugins/hub/dns/storage.py:133`
- `plugins/hub/dns/storage.py:134`
- `plugins/hub/dns/storage.py:138`

`_locked_atomic_write()` protects the final write. But `merge_save_registry()`
loads the existing registry before that write lock is acquired, merges in
memory, then saves. Two processes can both read the same old registry, merge
different records, and whichever saves last wins.

Why it destabilizes the program:

- The comment says concurrent agents should not clobber trust state, but the
  read-modify-write window can still lose updates.
- Remote imports, liveness refreshes, and approvals can race.
- A missing trust record can look like a remote auth problem later.

Investigation and fix area:

- Move load, merge, and replace under the same file lock.
- Keep `deleted_keys` handling inside that locked transaction.
- Add a two-writer test that proves both updates survive.

### 23. Mailbox Filenames Include Unsanitized Sender Identity

Severity: medium

Evidence:

- `plugins/hub/messenger.py:1374`
- `plugins/hub/messenger.py:1380`
- `plugins/hub/messenger.py:1384`
- `plugins/hub/messenger.py:1386`
- `plugins/hub/presence.py:67`

`send_to_file()` embeds `message.from_identity` directly in the JSON filename.
Normal pool identities are safe, but remote or malformed payloads can contain
slashes or unusual path characters unless sender identity is validated before
the mailbox fallback path.

Why it destabilizes the program:

- A malformed sender can make fallback delivery fail because the derived path
  points through non-existent subdirectories.
- If sender identity is payload-controlled, this becomes another reason remote
  sender binding must happen before durable inbox write.
- Operators may see socket delivery fail and mailbox fallback silently not
  preserve the message.

Investigation and fix area:

- Sanitize `from_identity` before using it in any filename.
- Consider removing sender identity from the filename and keeping it only in
  JSON content.
- Add a test for sender identities containing `/`, `..`, spaces, and unicode.

### 24. Status Git Refresh Can Leave Timed-Out Git Processes Running

Severity: low

Evidence:

- `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:827`
- `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:841`
- `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:842`
- `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:858`
- `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:859`

The git status widget starts async subprocesses and wraps
`proc.communicate()` in `asyncio.wait_for(..., timeout=2.0)`. On timeout the
communicate await is cancelled, but the subprocess is not explicitly killed and
awaited.

Why it destabilizes the program:

- A wedged git command can outlive the status refresh.
- Repeated status refreshes in a bad repo can accumulate child processes.
- The cache shows unavailable git state while the actual subprocess cleanup is
  undefined.

Investigation and fix area:

- On timeout, call `proc.kill()` or `proc.terminate()`, then await
  `proc.wait()` with a short timeout.
- Add a test with a fake git process that sleeps past the timeout.

### 25. Managed Process Kill Does Not Own Child Process Groups

Severity: medium

Evidence:

- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:327`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:356`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:364`
- `packages/kollabor-agent/src/kollabor_agent/process_manager.py:370`
- `plugins/agent_orchestrator/orchestrator.py:598`
- `plugins/agent_orchestrator/orchestrator.py:710`
- `plugins/agent_orchestrator/orchestrator.py:715`

Both the newer `SubprocessStrategy` and the older agent orchestrator terminate
the direct child process. They do not start children in a new process group and
do not kill a process group on shutdown.

Why it destabilizes the program:

- If an agent spawns helper processes, killing the agent can leave descendants
  running.
- `kill_all()` and hub stop can report success while child work continues.
- It makes repeated local runs harder to reason about after crashy sessions.

Investigation and fix area:

- Start managed subprocesses in a new session/process group where supported.
- Kill the process group on timeout, with direct-child fallback.
- Align legacy orchestrator behavior with the process manager behavior.
- Add a regression with a child process that ignores parent exit.

## Continued Pass Regression Tests

Add these to the missing-test list:

- Context compaction with only preserved/ledger-handled messages does not fail.
- Staged compaction refuses to apply after same-length history mutation.
- Crystal store concurrent writes preserve both updates and never truncate the
  file.
- DNS `merge_save_registry()` concurrent writers preserve both records.
- Mailbox fallback sanitizes sender identity before deriving a filename.
- Status git refresh kills and awaits timed-out subprocesses.
- Process manager kill cleans up child process groups.

## Summary

The highest-confidence fixes are not broad rewrites. They are mostly lifecycle
boundaries:

- always restore terminal state, even when shutdown fails
- keep one plugin failure from breaking unrelated plugins
- keep config reload work on the app loop
- stop config watcher threads on shutdown
- make MCP hook denial actually deny MCP execution
- own background startup tasks so shutdown cannot race startup

These changes would reduce the number of "looks ready but is not really ready"
states and make failures visible, contained, and testable.
