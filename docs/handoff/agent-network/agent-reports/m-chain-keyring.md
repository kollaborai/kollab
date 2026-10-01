# m-chain-keyring: chain death ends the request failed; keyring retry per reconnect (#121)

Worktree branched from issue-121-network-simple-flow at 4b82429 (fast-forward). Not pushed.
## Done
- cf486c2 "End a remote request failed when its chain dies ..." (bug 1)
- e4bcc18 "Read the keyring again for unresolved sealed-config keys once per reconnect ..." (bug 2)
- Full unit suite once at the end: 5317 passed, 9 skipped, 203 subtests. Ruff clean on touched files.
- CHANGELOG.md and kollabor/updates/CHANGELOG.md: one Fixed line per bug, cmp identical.
## Bug 1: what changed
- Root cause: `note_chain_end` only reported when `turn_completed`, cancel or `last_turn_error` was set, so a chain that
  died with none of them (a pre-request hook denial raising CancelledError before the turn's try, an exception out of
  `_hub_continue`'s first call) left a started turn open for ever (the sweep only drops never-started turns), which also
  blocks relay delivery (`network_turn_open`). The goal driver and the watchdog heal never called it at all.
- One guard, in `QueueProcessor` (packages/kollabor-agent/.../queue_processor.py): `is_processing` is now a property;
  its falling edge calls `note_chain_end()`, so every driver that lowers it reports (drain, hub continue, goal driver,
  watchdog). The two explicit calls (`_drain_queue` finally, message_handler.py) were deleted.
- `note_chain_end` reports on every exit with an empty queue and a free turn lock; failed = `last_turn_error`, or the chain
  left without `turn_completed`, or an exception is in flight (`sys.exc_info()`), unless the user cancelled (ESC stays not failed).
- `_execute_llm_turn` records `last_turn_error` for any exception leaving a turn (non-cancel), so a driver that swallows it
  (hub continue's inner loop, goal driver) still ends failed.
- Sweep (plugins/hub/plugin.py `settle_network_turn`): a never-started turn at 600 s now sends the failed end frame.
- Tests (tests/unit/test_hub_network_turns.py): test_a_queue_drain_cancelled_before_it_completes_ends_the_request_failed,
  test_a_continuation_turn_that_raises_ends_the_hub_continue_chain_failed,
  test_a_driver_that_only_lowers_is_processing_still_ends_the_chain, test_a_request_no_turn_ever_handled_ends_failed_at_the_wait_ceiling
  (renamed, it used to pin the silent drop), and the note_chain_end table row `completed=False` now expects failed.

## Bug 2: what changed
- "Reconnect" = `ConfigSyncService.tick` sees a (peer key, relay session) pair the previous tick did not: a peer on a new
  relay session, or this device's own relay link coming back (`_config_online_peers` returns empty while not online).
- `SnapshotBuilder.retry_unresolved()` (plugins/hub/config_sync.py) re-reads only the keyring names the last pass could not
  read, once, on the next build; resolved names keep their value. One keyring read per name per pass.
- Tests (tests/unit/test_config_sync.py): test_a_keyring_that_unlocks_after_launch_is_read_again_once_per_reconnect,
  test_the_service_retries_the_keyring_once_per_reconnect.

## Left / next step
- Watchdog heal (kollabor/llm/turn_watchdog.py) lowers `is_processing` before it sets `turn_completed`; a wedge with a stale
  `turn_completed=True` ends the request as finished, not failed. Fix: set `qp.last_turn_error` first (one line + test).
- Nothing proven on a live host (no ssh). Next: cherry-pick cf486c2 and e4bcc18 onto issue-121-network-simple-flow.
