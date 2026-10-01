# connect-polish-2: where I stopped

Worktree /Users/malmazan/dev/kollab/.claude/worktrees/agent-a4715d24e2e745ccf, branch `connect-polish-2` (started at 0fdb73f, which contains 7fba955). Not pushed. No attribution lines.

## Done (3 commits)

1. e301659 "Open the read-only Connect screen in an attached window whose daemon lost the relay to another window"
   - `ConnectSnapshot.read_only` (optional on the wire, an older daemon's snapshot still loads). The daemon's `_connect_snapshot` returns a read-only snapshot from the shared state when it lost the workspace lock; the attached window opens the same read-only screen and one line as a second window. `/connect code` in that window says the same line (one snapshot fetch, 1.5 s cap so a slow relay never delays it).
   - `test_attached_bare_connect_falls_back_when_the_daemon_is_not_the_owner` rewritten; old-daemon status-text fallback kept and tested.
   - New tmux spec `tests/tmux/specs/connect_attached_follower.json` (default launch = daemon + attached window, 80 and 120 cols); passes, and fails (12 assertions) with the fix off. Its teardown stops the daemon with the hub CLI run from the workspace (without the `cd` it leaked daemons; I killed the ones it left).
2. 1e6402b "Say in the docs and the help text that /connect code shows the code on a private screen"
   - Constitution section 6 row and section 8, docs/reference/commands.md, docs/specs/hub-remote-endpoint.md, CHANGELOG pair, and the palette text (`Show a join code on a private screen`) plus the two palette spec assertions. Grepped docs/, README, bundles: nothing else says a code is printed. Drive-by: removed the `/connect status [keys]` line from commands.md (that argument does not exist and section 13 forbids it).
3. d1a3a23 "Name a network after the device that starts it, <first device name>-net, ..."
   - It did NOT match: the network name was always the directory domain (no field existed). Now `RelayState.network_name`, set by `RelayClient.connect` on the first device (no inviter), kept by `rotate`, cleared by `leave`, in `_adopt_bridge_fields`; the issuer sends it in the signed decision and the joiner binds it (`bind_network_name`, first name wins). Screens, status, accept line, joined line and hub context show it.
   - Constitution: rule moved into section 4, stories use `mac-kollab-net`, the Story 1 pending line now says `request sent to kollabor.ai` (a joiner cannot know the name yet), section 15 item replaced by one narrower open item: no command edits the name (needs Marco: a network form of `/connect name`, or fixed).

## Test counts
- Full `tests/unit/ -q` (run once, after item 3, before the item 4 commit, same code as committed): 4931 passed, 7 skipped, 203 subtests passed.
- ruff clean on every file I touched in the three commits. CHANGELOG pair asserted byte-identical by my edit scripts each time.
- tmux passing on the tree at the time of each item: connect_attached_follower, connect_palette_subcommands (11), connect_help_surface (12), connect_request_names (now also checks the `mac-kollab-net` row and notice).
- NOT re-run after items 1 and 4: connect_second_window, connect_dashboard, connect_code_guard, connect_knock_usage. Item 1 changed `_connect_home`, so run connect_second_window first.

## Not done: item 2 (manual-trust numbers), one uncommitted edit left in the tree
- Uncommitted, untested: `plugins/hub/relay_conversations.py` got a `numbers` table plus `ConversationStore.number(room, kind, ref)` and `resolve_number(room, kind, n)`. It references two constants that are NOT defined yet, so do not commit it as is.
- Exact next steps:
  1. In relay_conversations.py near `MAX_OUTBOX` (line ~31) add `NUMBER_KINDS = frozenset({"request", "question"})` and `NUMBER_KEEP_SECONDS = 7 * 86400`.
  2. relay_agent.py: add `request_number(id)`, `question_number(id)` and `_numbered(kind, text)` (digits only, else `no request numbered N on this network`), then change `application_command`: `authorize` prints `communication authorized: request N; expires at ...; recipient agent@device`; `send` prints `sent request N to agent@device: <state>`; `withdraw <number>`, `answer <number> <text>`, `task|cancel <agent@device> <number>` resolve the number to the grant/event/message id (a request's grant id, message id and thread id are the same value); usage strings say `<number>`; `task`/`cancel` print `request N on agent@device: <state>` instead of JSON.
  3. plugin.py: tool result at ~line 2935 (`remote task {receipt['id']}` -> `remote request N`), `CONNECT_ADVANCED` args (`<grant-id>`, `<event-id>`, `<id>` -> `<number>`), and in `_display_hub_message` for `relay_event == "question"` append `(answer with /connect answer N <text>)`. Known leftover in the same class: manual-trust relay events show `relay:...` as the sender label in that box; map it to `agent@device`.
  4. Docs: constitution section 6 rows (364-365) and Story 7, docs/guides/connect.md, docs/reference/commands.md (lines ~207-210), bundles/agents/system/hub-collaboration.md line 54 and bundles/skills/kollabor-harness/SKILL.md ~208-213, CHANGELOG pair.
  5. Tests: update the two usage asserts in tests/unit/test_relay_agent_bridge.py (~2479, 2481); add store tests (stable, never reused, per-room, resolves) and command tests with the `bridges` fixture; scan the six commands' outputs with `LEAK` from tests/unit/test_connect_no_keys_on_screen.py. The model-facing manual-trust text (hex ids in the exact `hub_msg` arguments, relay event context) is pinned by tests and section 7 keeps it; leave it.
