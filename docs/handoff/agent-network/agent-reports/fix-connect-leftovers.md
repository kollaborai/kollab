# fix-connect-leftovers: report

- Worktree: /Users/malmazan/dev/kollab/.claude/worktrees/agent-a4715d24e2e745ccf
- Branch: worktree-agent-a4715d24e2e745ccf, one commit dc9391b on top of 564a63d (coordinator confirmed 564a63d as the base after my first stop). Not pushed, no PR, no issue.
- Message: "Open the Connect screen read-only in a second window and show device names whole unless the row is narrower, refs #121" (no attribution lines).

## Task 1: second window printed status text

Reproduced first (throwaway HOME, workspace on a seeded offline network `relay.invalid`, no traffic; two `--no-daemon` windows in tmux, 80x30). Second window `/connect` printed the status block (`network relay.invalid  trust: open`, `contact route ...`, `online lapis ..., koordinator ...`, `join code run /connect code`, `reconnect on launch disabled`). First window showed the screen.

Root cause, `plugins/hub/plugin.py` `_connect_home`, non-attached branch:
- Only the window that wins the workspace flock builds `RelayCommands`; the other has `_relay_commands is None`.
- That window asked the owner for `/connect status` over RPC and did `return status`, so it never reached `_open_connect_screen`. With no network it opened the code form instead.
- Neither screen could work there: `relay.enroll_device` and `relay.enrollment_offer` are not in `RELAY_METHODS` (`plugins/hub/relay_owner.py`), so `local_relay_rpc` rejects them before connecting (checked by calling it); requests and decisions live in the owner's in-memory issuer.

Fix (smallest that is correct): the screen opens in every window; a window that does not own the relay opens it read-only.
- `_relay_owned_elsewhere()` = not attached, `_relay_commands is None`, and the workspace lock probe (`owner.owner()`) finds another owner. Probe errors mean "not a second window".
- `_connect_home` and `_open_connect_screen` pass `CONNECT_OWNED_ELSEWHERE` ("another window in this workspace runs the network; use /connect there") and a snapshot built from the shared state file (network, trust, this device).
- `ConnectScreenAltView(snapshot=, note=)`: no code created, no polling, no decisions; c/a/r ignored; Esc/Enter close. `connect_screen_lines` with a note shows title, network, this device, the one line, ` esc close`; no join code, requests, knocks, online rows. No network: `network      none`. `/connect code` shows the same line, no code row.
- Owner window unchanged (probe never runs when `_relay_commands` is set). Attached path unchanged (`_attached()` guard; the existing attached tests are untouched and green).
- Live: when the first window quits, the second is promoted and shows the full screen on the next open.

## Task 2: device names clipped at 20

- Max device-name length: 63 (`NAME_RE` `[a-z0-9][a-z0-9-]{0,62}`; `slug` and `default_device_name` cap at 63). `NAME_DISPLAY_MAX` was 20 and `display_name()` applied it whatever room the row had.
- `NAME_DISPLAY_MAX = 63` (never cuts a valid name). `_request_rows` gives `display_name` the row's room minus the selection marker and " wants to join", so the name is cut with an ellipsis (by display width, existing `clip_display`) only when the row is wider than the terminal, and the words stay. 80 cols with a 63-char name: 51 chars + `…` + ` wants to join` (row exactly 80). 120 cols: whole name.
- Sweep of every row that shows a device name or agent@device:
  - request row: fixed.
  - accept/reject notice on the Connect screen: was capped at 20 via the same default; now whole, clipped by `_fit` with `…` only when wider than the terminal.
  - knock review row: was capped at 20; now whole, `request_row` clips the head by width.
  - knock review confirmation: would now need more than 3 wrapped lines with two full names at narrow widths and `[:3]` dropped the rest silently; it now ends the third line with `…`.
  - joined line and every other line on the code form: `ConnectAltView._write_line` sliced by characters with no ellipsis; now `clip_display`.
  - Connect screen network / this device / online rows: already `clip_display` by width, no 20 cap.
  - `/connect status`, model roster block, `hub_status`, `hub_agents`, `kollab --hub status`: text, never clipped (checked, no change).

## Files (16)

- plugins/hub/plugin.py, plugins/altview/connect_altview.py, plugins/altview/contact_altview.py, plugins/hub/device_names.py
- tests/unit/test_connect_second_window.py (new), test_request_rows.py, test_connect_command_fixes.py (two old follower tests rewritten to the new behavior), test_connect_no_keys_on_screen.py (read-only screen added to the scan)
- tests/tmux/specs/connect_second_window.json (new), connect_request_names.json (new), tests/tmux/lib/connect_canned_requests.py (new, test-only launcher)
- docs/specs/agent-network-simple-flow.md (one paragraph after Story 1), docs/guides/connect.md, docs/reference/commands.md, CHANGELOG.md + kollabor/updates/CHANGELOG.md (byte-identical, checked with cmp)

## Results

- Unit, before (this tree at 564a63d): 4731 passed, 7 skipped. After, on the committed tree: `KOLLAB_NO_KEYRING=1 /Users/malmazan/dev/kollab/.venv/bin/python -m pytest tests/unit/ -q` = 4762 passed, 7 skipped, 203 subtests passed (+31 tests).
- ruff (`/Users/malmazan/dev/kollab/.venv/bin/ruff check` on every touched .py): clean. py_compile ok. black run on the new test file only.
- tmux, `KOLLAB_NO_KEYRING=1 tests/tmux/lib/test_runner.sh tests/tmux/specs/<spec>.json`, all seven connect_*.json pass: code_guard 7, dashboard 11, help_surface 12, knock_usage 4, palette_subcommands 11 (all 5 also passed before my change), request_names 18 (new), second_window 29 (new).
  - second_window: second window at 80 and 120 columns (title bar exactly as wide as the terminal), no join code/requests/online/accept text, c/a/r do nothing, `/connect code` same line, no-network second window says `network none` not the code form, first window still has join code / requests / online / `c new code`.
  - request_names: canned requests (63-char name twice, ana-laptop): 80 cols cut with `…` and `wants to join` kept, no row wraps; 120 cols whole name; accept notice cut at 80, whole at 120.
- Mutation checks: with the fix off, second_window fails 17 assertions; with the cap back at 20, request_names fails 6 and 14 unit tests fail.
- Cleanup: every tmux server, kollab process and throwaway HOME from my runs is gone.

## Not verified / flags

- No real relay or second machine: the network is seeded offline and join requests are canned by `tests/tmux/lib/connect_canned_requests.py` (patches `RelayCommands._pending_rows` and `RelayAgentBridge.decide_enrollment_request`). The joined line and the knock review widths are unit-tested only (no tmux path without a relay).
- Same bug class left as briefed: an attached window whose daemon is not the relay owner still prints status text (existing test `test_attached_bare_connect_falls_back_when_the_daemon_is_not_the_owner`).
- Read from code, not run live: `/connect accept` typed in a second window is refused by the owner's existing guard ("only the active local issuer agent can review requests").
- `docs/specs/agent-network-simple-flow.md` is the constitution: I added one paragraph (second window is read-only). Marco should confirm or drop that hunk.
- docs/handoff/agent-network-m1/HANDOFF.md still lists both items as known limits; not edited.
- The worktree has a git-ignored `.venv` symlink to /Users/malmazan/dev/kollab/.venv so the specs' `$R/.venv/bin/python` resolves.
- The two new specs reach the runner's tmux socket through `$PPID` (`kollab-test-<pid>-<spec name>`) to resize to 120 columns, and sleep 3 s after the resize because the app only picks up the new size on the next screen open.
