# WebUI Trajectory Tab Verification

spec: `docs/specs/webui-trajectory-tab.md`
phase: Phase 1, Phase 2, and the Phase 3 paging increment
date: 2026-08-24
verified_by: spec-verify workflow

## Results

| Requirement | Status | Evidence |
|---|---|---|
| Chat/Trajectory switcher stays inside `RuntimeShell` | PASS | Live browser DOM: `chat-tab`/`trajectory-tab` `aria-pressed` changed; `trajectory-view` and `trajectory-ledger` rendered. |
| History projection covers system/user/assistant/tool/tool-batch | PASS | Browser-loaded projector assertion returned all five kinds, native result pairing, XML batch rows, and an unmatched trimmed result. |
| Restored chat history keeps native tool calls visible | PASS | The engine mirror now preserves `MessageDto` metadata; browser fixture rendered a grouped `2 tool calls` control, individual `shell`/`read_file` calls, and the paired result after expansion. |
| Stable identity and null-safe timing | PASS | Browser assertion kept the assistant ID stable after prepend and returned `null` for missing timing. |
| Native/XML tool details and thinking reach the inspector contract | PASS | Projector assertion returned joined output and assistant thinking; inspector renders Input/Output/Thinking tabs from the selected record. |
| XML tool batches stay out of Chat | PASS | Browser-loaded `buildInitialState` assertion returned only the real user and assistant messages, excluding the batch user message. |
| Refresh boundary and transport preservation | PASS | `TrajectoryView` subscribes to `/events`, refreshes only on `turn_complete`/`error`, reconnects after the endpoint's terminal `turn_complete`, and is mounted inside `EngineRuntimeProvider`. |
| Phase 2 usage metadata on native and XML writes | PASS | Shared `_assistant_history_usage_metadata` is used by both queue-processor branches; `tests/unit/test_engine_history_usage_metadata.py` passes. |
| Phase 2 token/timing projection | PASS | Browser assertion returned `inputTokens=10`, `outputTokens=20`, and `durationSeconds=1.75` from `metadata.usage`. |
| Phase 3 load-earlier paging | PASS | `EngineApi.getHistory(limit)`, stable keys, and the ledger's `Load earlier` control are implemented and included in the source/build gate. |
| Trajectory ledger scrolling with many records | PASS | Live desktop DOM at `1211x800`: document scroll height stayed `800px`, ledger viewport was `613px`, and row content was `2021px`; setting the viewport to its maximum scroll reached the last record. |
| Phase 3 timeline/virtualization/deep links | DEFERRED | Explicitly independent polish items in the spec; not required for the Phase 1/2 acceptance gate. |
| Build and regression gates | PASS | Frontend typecheck/build, auth wiring regression, usage contract tests, and Python compilation pass. |

## Reproducible verifier

```text
tests/tmux/verify_webui_trajectory_tab.sh
```

The script uses a dynamic tmux socket/session and runs the source, typecheck, build, auth,
usage, and compilation gates without touching the user's existing tmux state.

## Remaining verification gap

The preview browser's snapshot and resize operations were unavailable/timed out during this
run. Desktop DOM proof was completed at `1280x800`; narrow-layout behavior is source-verified
through the responsive sheet classes but was not live-resized in the browser. A real provider
turn was not submitted to avoid an unapproved external model/quota call; native/XML behavior
was verified through the browser-loaded projector contract instead.
