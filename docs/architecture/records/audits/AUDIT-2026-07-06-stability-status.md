---
title: "Kollab Stability Status For 0.5.21"
doc_type: audit-record
created: 2026-07-06
status: active
---

# Kollab Stability Status For 0.5.21

Current checkout reviewed on 2026-07-06:

- branch: `main`
- version: `0.5.20`
- local position before commit split: ahead of `origin/main` with dirty stability,
  log-retention, space-shooter, test, and smoke-script work
- prior audit source: `AUDIT-2026-07-03-stability-review.md`

## Verification Snapshot

Fresh checks run during this pass:

```bash
uv run python -m pytest tests/unit -q
```

Result:

```text
2399 passed, 60 skipped, 20 subtests passed in 19.26s
```

```bash
tests/tmux/lib/test_runner.sh tests/tmux/specs/space_shooter_playable.json
```

Result:

```text
[PASS] space-shooter-playable
```

Long-tool watchdog runtime probe:

```text
progress elapsed=300s mode=None is_processing=True turn_completed=False kicks=0
tool_complete elapsed=310s tool_executing=False
post_check elapsed=315s mode=stuck_busy is_processing=False turn_completed=True kicks=0
PASS no watchdog reset during 310s tool execution; reset occurred only after tool flag cleared
```

## July 3 Finding Status

1. Plugin startup isolation: open.
   `TerminalLLMChat._initialize_plugins()` still awaits plugin `initialize()` and
   `register_hooks()` inline; one plugin exception can still abort later plugin
   startup. Evidence: `kollabor/application.py:2251`.

2. Terminal restoration too late in app shutdown: open.
   Terminal cleanup still runs after script widget, input, LLM, logger, MCP,
   version, and plugin shutdown awaits. Evidence: `kollabor/application.py:3086`.

3. Fullscreen cleanup can skip alternate-buffer restoration: open.
   `FullScreenSession._cleanup()` still awaits `plugin.on_stop()` before
   `renderer.restore_terminal()` inside one broad try body. Evidence:
   `packages/kollabor-tui/src/kollabor_tui/fullscreen/session.py:417`.

4. Config file watcher reloads on wrong thread: open.
   The watchdog thread still calls `asyncio.get_running_loop()` from the observer
   callback and falls back to sync reload when no loop is present. Evidence:
   `packages/kollabor-config/src/kollabor_config/service.py:55`.

5. Config watcher not shut down by application shutdown: open.
   `ConfigService.shutdown()` exists, but application shutdown still does not call
   `self.config.shutdown()`. Evidence: `kollabor/application.py:3086`,
   `packages/kollabor-config/src/kollabor_config/service.py:605`.

6. MCP pre-hook cancellation ignored: open.
   `MCPIntegration.call_mcp_tool()` emits `MCP_TOOL_CALL_PRE` but ignores the
   returned hook result before executing the tool. Evidence:
   `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py:1027`.

7. Hub startup task fire-and-forget: open.
   `HubPlugin.register_hooks()` still schedules `_start_hub()` with
   `asyncio.create_task()` and does not retain the task for shutdown. Evidence:
   `plugins/hub/plugin.py:3546`.

8. Config hook timeout leaks descendant processes: open.
   Hook commands start with `start_new_session=True`, but timeout handling kills
   only the direct child. Evidence: `kollabor/config_hooks.py:453`.

9. Event bus lacks event-level backpressure contract: open.
   Hooks still execute through the serial event pipeline without event-level
   budgets or observer-vs-blocking separation. Evidence:
   `packages/kollabor-events/src/kollabor_events/bus.py:101`.

10. Shutdown can call the same plugin instance twice: open.
    Plugin initialization dedupes by instance id, but shutdown still iterates
    `plugin_instances.items()` directly. Evidence: `kollabor/application.py:2253`,
    `kollabor/application.py:3126`.

11. Legacy agent orchestrator raw environment/trust spawn: open.
    The legacy orchestrator still has a separate spawn path from the filtered
    process manager path. Needs a dedicated pass before release confidence.

12. MCP discovery serial and slow-failure prone: open.
    Discovery still walks servers serially; no bounded concurrent discovery
    contract was found in this pass.

13. Process manager strategy lookup drift: mitigated / needs regression.
    The setter comment says existing processes should keep their spawning
    strategy, but `ManagedProcess` stores `strategy_name` rather than the
    strategy instance. Needs a kill-after-strategy-swap regression before this
    can be marked fixed. Evidence:
    `packages/kollabor-agent/src/kollabor_agent/process_manager.py:592`.

14. Active MCP calls not cancelled promptly: open.
    Tool execution now exposes `tool_executing` for the watchdog, but active MCP
    call cancellation still needs a prompt-cancel contract and test.

15. Fire-and-forget renders can race main render loop: open.
    No current-pass evidence showed a render serialization fix.

16. Deferred startup marks ready after partial failure: open.
    Deferred startup still sets `_startup_ready` in `finally` after broad
    startup exceptions. Evidence: `kollabor/application.py:1095`.

17. Status widget output type boundary: not rechecked in depth.
    No current-pass change targeted this. Keep open until a focused status-widget
    pass confirms coercion at the renderer boundary.

18. PluginFactory async lifecycle helpers misleading: not rechecked in depth.
    No current-pass change targeted this. Keep open until plugin lifecycle docs
    and helper behavior are reconciled.

19. Context compaction empty-summary failure: open.
    Current pass did not land the `len(summary_text or "")` fix. Keep open.

20. Staged compaction can overwrite concurrent history mutation: open.
    No prefix fingerprint or history-version contract was found in this pass.

21. CrystalStore whole-file writes not atomic enough: open.
    The test fixture was fixed to avoid accidental dedup, but persistence still
    uses the existing whole-file write model and needs atomic write/file-lock work.

22. DNS merge-save read-modify-write race: open.
    `merge_save_registry()` still loads outside the final write lock. Evidence:
    `plugins/hub/dns/storage.py:128`.

23. Mailbox filenames include unsanitized sender identity: open.
    `send_to_file()` still embeds `message.from_identity` directly in the
    filename. Evidence: `plugins/hub/messenger.py:1374`.

24. Status git refresh timed-out subprocess cleanup: open.
    The git cache still waits on `proc.communicate()` with a timeout and does not
    explicitly kill/await the timed-out subprocess in the exception path.
    Evidence: `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:827`.

25. Managed process kill does not own child process groups: open.
    `SubprocessStrategy.spawn()` does not start a new process group, and `kill()`
    still terminates only the direct child. Evidence:
    `packages/kollabor-agent/src/kollabor_agent/process_manager.py:327`.

## Current-Pass Fixes And Mitigations

- fixed: full unit suite regressions found by this pass.
  - synced `kollabor/updates/CHANGELOG.md` with root `CHANGELOG.md`
  - made CrystalStore retrieval tests use non-deduping fixture data
  - made hub-console background assertion accept RGB or 256-color ANSI
- fixed: tmux `/space` playable smoke passes.
- fixed: phase 4.5 smoke now uses project-scoped hub presence/socket paths.
- mitigated: long-running foreground tools are not misclassified as wedged by
  `TurnWatchdog`; proved with a 310-second runtime probe.
- mitigated: current stability patch raises continuation backstops and adds log
  retention caps, but this does not close the July lifecycle backlog.

## Blocking Before 0.5.21 Release

- Full unit suite: green.
- Changed tmux `/space` smoke: green.
- Long-tool watchdog runtime probe: green.
- Phase 4.5 attach/profile smoke: still red after project-scoped path repair.
  Daemon discovery works, but attach capture is empty and profile-drain
  assertions fail. Treat as an open regression or stale smoke until investigated.
- July 3 lifecycle findings: mostly open. Do not claim 0.5.21 is a general
  stability release unless scope is narrowed to queue/watchdog/log-retention and
  space-shooter work.

## Release Scope Recommendation

Ship `0.5.21` only as a narrow patch if the release note is honest:

- long-running queue/continuation stability
- watchdog protection for silent-but-alive sessions
- raw/app log disk caps
- playable `/space` update
- packaging/test hygiene fixes

Do not market it as closing the broader July 3 stability audit.
