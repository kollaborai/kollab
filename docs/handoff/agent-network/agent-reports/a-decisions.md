# a-decisions

Worktree branch = issue-121-network-simple-flow (f737492) + 5 commits below. Not pushed.

## Done (all four items, each with a test written first and seen failing)
- 5d3b0e9 Decision 6: deleted the stale Codex verifier script and its unit test; scrubbed the live references (HANDOFF, M1-HANDOFF, three agent-reports, goal-prompts, working-notes, goal WBS); CHANGELOG "Removed" line.
- 6e68251 Remote cron fire: `_fire_cron_job` calls `show_network_notice("cron <id> -> agent@device")`, one dim info row through the message coordinator, no box. Local targets and failures draw nothing, as before. Tests: `test_firing_to_an_agent_at_device_goes_through_the_network_send` (updated), `test_every_fire_to_a_device_is_one_dim_line_never_a_box`. HANDOFF "Small follow-ups" section removed (it was the last item).
- bd3af21 Decision 3, MCP skip: `_mcp_missing` (`shutil.which`) in `plugins/hub/config_sync.py`, `Applied.skipped_mcp`, `ConfigSyncService(notice=...)` with one line per distinct set, `RelayAgentBridge._config_notice` -> `plugin.show_network_notice`. A skipped server is not written, a local one of the same name stays, a skipped name is only kept in the managed record if it was already synced. Tests: `test_mcp_servers_whose_command_is_not_installed_here_are_skipped`, `test_a_synced_server_whose_new_command_is_missing_keeps_its_last_good_definition`, `test_skipped_mcp_servers_are_named_once_per_distinct_set`. Existing fixtures use `sys.executable` instead of `node`.
- 5e9b735 Decision 5: the service says "Settings sync is off in this workspace: another workspace on this machine joined a different network last." once on `other_primary`. Test: `test_a_workspace_that_lost_the_machines_record_to_a_later_join_says_so_once` (real `RelayClient._forget_stale_primary`, two services on one machine record).
- f01d392 `test_the_relay_agent_shows_config_sync_notices_in_the_main_pane` (bridge-level wiring of the notice).
- Docs: constitution section 9 has one bullet each for items 1 and 2; CHANGELOG pair has one line per item (byte-identical). Story 3 is the OS crontab path and never described the box, so unchanged.

## Results
- Full unit suite (before f01d392): `5210 passed, 9 skipped, 203 subtests passed`. `test_config_sync_service.py` after f01d392: 18 passed. Ruff clean on every touched file.

## Left / notes
- Not seen live: no ssh, no screen run. The three lines are unit-tested only.
- "Once" is per run (in memory), so each launch shows the line again on its first bundle.
- `shutil.which` reads the PATH of the process that applies the bundle (the daemon).
- Left on purpose (dated history, not a pointer): the released-version CHANGELOG note (line 187, both copies) and docs/operations/network-release-review.md still name the deleted script.
- tests/live/story7 is not written here (another agent).

## Next step
- Fast-forward issue-121-network-simple-flow to this branch, then continue the work order (guided setup, then live checks for story7 and the guided setup only).
