# m3 c4 debug (refs #121)

**Root cause: the product, not the proof.** `stop_ws` worked (it printed `stopped`, no C process left). A kept listing C because a failed directory refresh never evicts the cached rows.
- `_rpc_directory` (plugins/hub/relay_agent.py) serves `/connect status` from `_cache`. The 15 s refresh that fails only logs a warning and `continue`s; a row is pruned only when C's key leaves `valid_keys`, and the mesh (`known_peer_keys`) keeps the key until its signed record/route expires.
- Evidence (dev5, r13, C = kollab-m3-c9, `M3_C4_WAIT=420`): C stopped 11:23:16. A logged 11 x `remote directory for peer <C label> unavailable: secure conversation transport failed`, 11:23:26 to 11:25:57, and still listed peridot@...-c9 until 11:26:07 = 171 s (the proof gave up at 84 s). The warnings stop when the key leaves the mesh list (inferred).

**Fixes**
- `deb229f` (product): drop a peer's cached rows when its refresh fails and they are older than `DIRECTORY_STALE_SECONDS` (45 s; beat 15 s), so a stopped device leaves the list in about a minute. `test_directory_drops_a_peer_that_stopped_answering` fails with the drop disabled, passes with it; test_relay_agent_bridge + test_mesh_network + test_relay_network_trust: 115 passed.
- `93d7d4a` (proof): c4 waits `M3_C4_WAIT` s (default 150), records the process list right after the stop and A's status on failure.
- The fix is proven by unit test only, NOT live: this run was the unfixed dev5 build, c4 passed because the wait was widened.

**Final m3 table** (dev5, m1 proof 20/20 PASS first): pre c1 c2 c3 PASS | c4 PASS (A dropped C at 171 s) | c5 PASS (A listed C 23 s after its relay-less restart) | **c6 FAIL** | c7 PASS | c8 PASS | z1 z2-mac z2-srv z2-c PASS.

**c6 (new, not fixed, not investigated further)**: C ran the request (terminal `uname -n` 11:27:11, hub_msg tool SUCCESS 11:27:32; C's pane shows the reply word) but A's `kollab --hub msg` exited 0 at 11:27:47 printing `peridot@...-c9 finished without a reply`. A's log has no reply/finish/warn line at 11:27. The answer existed on C and did not reach the waiting CLI over the routed path (m1's direct path returns replies): reply correlation/forwarding on the mesh, cli-reply-threads / reply-forward-fix territory.
Also: C logs `ERROR Failed to execute command 'hostname'` (prompt_renderer.py:974) every turn, no `hostname` binary on alzan-prod; the z2 scan does not flag it.

**Rebuild: yes** for `deb229f` (client side, A needs it; relay untouched). Rerun m3 with a NEW C name (kollab-m3-c10) after the c6 fix, default c4 window.
Cleanup: m1-mac, m1-srv, m3-c stopped by name, `m3/teardown.sh` run (no pkill, no m4-serve), m4-serve still up. `m1/teardown.sh` not run.
