# b-polish (issue #121, 0.11.0 polish)
Worktree = issue-121-network-simple-flow (e5cbb77) + 2 commits below. Not pushed.

## Done
- 89b0af9 Gap 3: what config sync last told the human lives in the network state
  (`RelayState.config_told_skipped` = digest of skipped MCP names, `config_told_refused`),
  via `RelayClient.remember_config_told` -> `ConfigSyncService(told=, remember_told=)`. A restart is
  quiet until the set changes; the refusal speaks again only after a bundle applied in between.
- 3853762 Gaps 1+2: `connect_guide.JoinLine` (the name as soon as known, the stand-in only after
  30 s, said once) starts on approval, so the main pane gets it via `show_network_notice` even with
  the form closed. `_primary_name` reads `peer_devices[inviter]` (the join binds it) before the
  managed record; a key label is not a name. CHANGELOG pair: one line (cmp identical).
- Found: `enrollment_result` dropped `note`, so an attached window always showed the stand-in.
  It now passes a bounded `note` ("" = daemon still waiting for the name).

## Tests (first, seen failing; ruff clean)
- 11 new: JoinLine (3), sync memory (3, one on a real client state file), join line (5). The
  attached-window form test was added after the fix (its RPC twin was seen failing).
- Two old assertions changed on purpose: status note is "" while the name is unknown.
- Full unit suite: 5264 passed, 9 skipped, 203 subtests passed (about 5330 expected; bases differ).

## Left
- Not seen live. The form never refreshes: a name that arrives after approval reaches the main pane only.
- Name known at approval: form and main pane each show it once. A restart inside the 30 s drops it.

## Next step
- Driver live run: join by code, check the main pane names the primary; restart the secondary on a
  bundle that skips an MCP server and check it stays quiet.
