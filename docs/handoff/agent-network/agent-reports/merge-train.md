merge-train handoff

branch: worktree-agent-a02a0dca0838e4635 (worktree /Users/malmazan/dev/kollab/.claude/worktrees/agent-a02a0dca0838e4635), final sha 3add5b5, based on 34464e0 (issue-121-network-simple-flow), 19 commits on top. Not pushed.

merged, all five, in order (branch tip after each):
- reply threads d53a8a1 -> dee43fc (90 tests green)
- M2 sealed config 2319b6f cacfe5b c12454d 61958bd -> 9d059bf (156 green)
- Story 5 260af09 f80246b 75b11db -> 880ec82 (84 green, 2 skipped)
- M4 self-host, all 7 of 1111cbd..fd1c0ea -> 04bb354 (40 green)
- M3 mesh e7ce3a9 0e7dc42 61ea79b -> feac607 (18 green)
did not merge: nothing. No branch aborted, no revert.

fix commits: none (only conflict resolutions inside the picks).

conflict resolutions:
- CHANGELOG.md + kollabor/updates/CHANGELOG.md: kept both sides, cmp identical. The relay serve line existed twice (old text + a8a79ad correction): kept the corrected one only.
- relay_client.py: _adopt_bridge_fields copies device_name, network_name, trust, peer_devices, peer_trust, config_recipients, links; leave/rotate clears config_recipients and links.
- relay_commands.py: read_only and config_from both kept (dataclass, to_wire, from_wire); the leave path keeps the inviter capture plus sync_links(force=True, keys=[]).
- relay_state.py: both config_recipients and links fields.
- peer_transport.py refresh(): M3 locator-only peers added, strangers (state.links) excluded from both lists.

full suite (tests/unit/, once, at the end): 5186 passed, 9 skipped, 203 subtests passed, 0 failed, 108s.
ruff on every .py file the merges touched (AM vs 34464e0): All checks passed.
no conflict markers left in the tree; the 3 protected files are untouched (not in the diff).
HANDOFF.md rows updated to "merged <sha>, not live-proven" (commit 3add5b5). M2 and M3 rows keep their still-not-done items (M2: docs, tests/live/m2; M3: missing tests, tests/live/m3, docs).

next step: from /Users/malmazan/dev/kollab run `git merge --ff-only worktree-agent-a02a0dca0838e4635` on issue-121-network-simple-flow (the dirty files there are not in this diff). Then the live runs one at a time: M1 re-proof, Story 5 (redeploy relay first), M2, M3, M4.
