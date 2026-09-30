# fix-connect-ui report

Worktree: /Users/malmazan/dev/kollab/.claude/worktrees/agent-a051fd5db54462f75
Branch: worktree-agent-a051fd5db54462f75 (fast-forwarded to issue-121-network-simple-flow a4fcab8 first; confirmed)
Commit: 7b652aa "Open the Connect screen in an attached window, show the join wait, retire used codes, refs #121"
Not pushed. No PR, no issue. .venv link removed before commit; tree clean after.

## Bug A: Connect screen unreachable in the default launch

Cause: `_connect_home` (plugins/hub/plugin.py) returned the daemon's status text for an attached window; the screen's load/decide closures used `_relay_commands` / `_relay_agent`, which only the daemon has.

Changed
- plugins/hub/relay_commands.py: `ConnectSnapshot.to_wire()` / `ConnectSnapshot.from_wire()` (plain JSON types out; strict shape, bounded printable text, trust checked, request id must be a safe id, row caps on the way in).
- kollabor/state/interface.py, local.py, remote.py, handlers.py: new RPCs `state.hub_connect_snapshot`, `state.hub_connect_decide`, `state.hub_enroll_status`, following the hub_enroll / hub_enrollment_offer pattern (strict params, exceptions swallowed to fixed text). A shared `enrollment_result()` validator in interface.py replaces three copies of the same status check.
- plugins/hub/plugin.py: `_attached()`, `_attached_connect_snapshot()`, `_connect_snapshot()`, `_decide_join_request()`; `_connect_home` opens the screen when attached (snapshot.domain empty -> code form); `_open_connect_screen` load/decide go to the daemon when attached. A daemon that cannot send a snapshot (old, or not the relay owner) still gets the old status-text fallback.
- The request id (enrollment_id) crosses the local RPC for the decision and is never rendered (a test asserts it is absent from screen text).
- Second symptom (`joined kollabor.ai` only): the daemon now builds the joined line (`_joined_line`) and sends it as `detail` in the approved result; the window uses it, with the old window-side line only as fallback for an old daemon.

Second-window branch (`_relay_commands is None`, not attached): NOT changed. There is no state-service RPC to the owner from a non-attached window (only the daemon socket serves state.*; the owner exposes relay.* RPCs for send/enroll/offer, none for snapshot/decide). Adding relay.connect_snapshot / relay.enrollment_decide would work but is a separate change. It still prints the owner's status text.

Verified: unit tests below, including a rig that runs window plugin -> RemoteStateService -> RpcServer handlers -> LocalStateService -> daemon plugin with JSON both ways. NOT verified live: I was told to start kollab only with --no-daemon, so a real daemon + attached window was not run. The live proof should re-run the default-launch flow.

## Bug B: no feedback after Enter on the join form

Cause: `hub_enroll` was one blocking RPC (90 s) that returned only after the other device decided; `enroll_device` never returned pending; the form had no waiting state. Also found: the daemon's attach loop awaits each RPC in sequence (plugins/hub/messenger.py `_recv_input`), so a long blocking hub_enroll stalled that connection.

Changed
- plugins/hub/enrollment_client.py: `enroll_device(..., on_submitted=None)` and `_drive_destination_enrollment(..., on_submitted=None)`; the callback fires right after the relay accepted the request, before the wait (unit test: fires once, after the `request` post, before any poll).
- plugins/hub/relay_agent.py: `enroll_device` / `_rpc_enroll_device` pass `on_submitted` (owner path only; a non-owner window has one blocking RPC to the owner and never fires it).
- plugins/hub/plugin.py: `_run_connect_enrollment` now starts the enrollment in a task and returns `pending` + an opaque receipt as soon as the request is submitted (or the final result if it failed first); `_connect_enrollment_status(receipt)` reads it without waiting (pending / approved+detail / rejected / failed; an unknown receipt raises). Finished joins stay readable 15 min. The `_open_connect_altview` closures share one `outcome_of` mapping.
- plugins/altview/connect_altview.py `ConnectAltView`: new `on_wait` callback; after a pending submit the stage is "waiting", showing `request sent to <domain>; waiting for approval on another device` at once and polling every 2 s until joined / rejected. A poll that raises or returns junk shows `still waiting for approval on another device; check with /connect status` and keeps polling; it never becomes an error. A failure after submit reads `the join request did not complete; get a new code and try again`; `could not submit the join request` is now only for a failure before the request was sent. Esc closes and stops watching; the request keeps waiting daemon-side. Without on_wait the old behavior is unchanged.
- The same code serves the attached path and the `--no-daemon` path (both call the plugin methods; attached goes through state.hub_enroll / hub_enroll_status).

Known limit: a non-owner second window still uses the old blocking 60 s owner RPC, so it can still show `could not submit` if nobody answers within 60 s. Not changed (same reason as above).

## Bug C: used code stays on the accepting device's screen

- plugins/altview/connect_altview.py: after a decision succeeds while the code is active, the code is wiped and the line reads `used   press c for a new code` (expired-line style); `c` makes a new one. Applies to accept and reject (both end the offer). A refused decision keeps the code.

## Docs
- docs/guides/connect.md: removed the "attached window prints status text" sentence; step 2 describes the waiting line and Esc; used-code line added.
- docs/reference/commands.md: attached window opens the same screen; second-window caveat; waiting line; used line.
- CHANGELOG.md and kollabor/updates/CHANGELOG.md: one Fixed entry each, byte-identical (cmp checked).

## Tests
Added tests/unit/test_connect_attached_and_waiting.py (50 tests): snapshot JSON round trip and 15 off-shape rejections; attached bare /connect opens the screen from the daemon; rows render without the request id; attached accept/reject by request id, reason passthrough, RPC param validation; old-daemon and non-owner-daemon fallback; hostile snapshot reply dropped; attached join shows waiting then the full joined line, rejected, failed-after-submit wording, failed-before-submit wording; a wait that cannot be polled says still waiting + /connect status; Esc stops polling; 80-column fit; daemon start/status (pending, final, failed, unknown receipt, code-bearing exception swallowed); `--no-daemon` form flow; used-code line (retire, stay retired across refresh, new code on c, refused accept keeps code, 80 cols).
Also: tests/unit/test_enrollment_client.py (+1 test: on_submitted fires once after the request post; `_run_enrollment` got a `submitted_probe` arg); tests/unit/test_hub_connect_enrollment.py (renamed the old "attached prints status text" test; it now documents the old-daemon fallback).

Commands and results
- `KOLLAB_NO_KEYRING=1 .venv/bin/python -m pytest tests/unit -q -p no:cacheprovider`: 4664 passed, 7 skipped, 0 failed (203 subtests passed).
- tests/tmux/lib/test_runner.sh, one at a time, all exit 0 with 0 [FAIL]: connect_code_guard (7 PASS lines), connect_dashboard (11), connect_help_surface (12), connect_knock_usage (4), connect_palette_subcommands (11). Counts include the final "[PASS] <spec>" line.
- `ruff check` on every changed .py file: clean. `py_compile`: clean.
- black: NOT applied to the shared files. plugins/hub/*.py, connect_altview.py and test_enrollment_client.py already have unrelated black diffs at HEAD (79 hunks in enrollment_client.py alone), so I hand-formatted my additions and ran black only on the new test file.

## Not done / caveats
- No live default-launch (daemon + attached window) run; the rule was --no-daemon only. Live proof of A and B in that mode is still needed.
- No new tmux spec for B or C: they need a real second device and a relay, and I was told not to touch remote hosts.
- Second-window (non-attached, non-owner) branch left as is; see Bug A and Bug B.
