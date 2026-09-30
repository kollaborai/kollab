# Review of M3 (mesh) and M4 (`relay serve --domain`)

Branch `worktree-agent-a3a2a755963a72ee3`, from 704a7f6. Last fix commit a2a78c1; the tip is the commit that adds this note. Not pushed, no PR.
Read: `git diff 04bb354..feac607` (messenger, peer_transport, peer_discovery, secure_conversation, relay_agent, plugin defaults) and `880ec82..04bb354` (all of `relay_selfhost.py`, CLI dispatch, printed nginx/Caddy/systemd, `repoint_vhost.py`, `inspect_old.py`). Not read: `tests/live/m4/*.sh`, the test files.

## Fixed (one commit each, test fails before, passes after)
- b1b5ebf Direct-to-relay fallback. `_send_forward_to_peer` and `PeerMeshRuntime.request` fell back only on `TransientPeerDeliveryError/OSError/TimeoutError`. `do_remote_client_handshake` returns False on a refusal AND on a timeout or bad greeting, and `_direct_request` turns that into `PeerRouteError` (a ValueError, not caught), so a peer with a stale or one-sided locator (the far side does not know our endpoint) lost delivery although the relay path worked. Also covers a garbled reply (`json`/`readline` ValueError). Now one shared `_DIRECT_FAILED` tuple. Tests: `test_a_refused_direct_endpoint_falls_back_to_the_relay`, `test_a_refused_direct_secure_record_still_takes_the_routed_path`.
- a2a78c1 Stranger guard on the direct endpoint. `relay_agent.py:1625` refuses `peer.forward` and `peer.exchange` from an accepted stranger (`state.links`); `handle_direct_forward` had no such check, and M3 lets any approved relay key (strangers too) reach it through a signed locator with no human endpoint approval. Now refused. Test: `test_the_direct_endpoint_turns_an_accepted_stranger_away_from_forwarding` (fails before on the wrong exception, so it proves the missing check, not an exploit; a stranger normally has no link or record, so real reach was limited).

## Not fixed
- `peer_transport.py` `endpoint_key_for` (line 1745): a designation claimed by two approved devices with different keys admits neither, so an accepted stranger can claim a not-yet-registered endpoint name and block that device's direct link (denial only, no impersonation: the caller maps to the signer's own relay key). Fix: bind the claim to the first signer, or put the signer's relay key in the endpoint auth. Design call for Marco.
- `relay_selfhost.py` `serve()` finally block: SIGTERM does not republish an identity-only document, so the key file keeps naming the relay for up to 300 s after it stops (doc says it names the relay only while ready). Fix: after `stop.wait()`, `await asyncio.to_thread(publish, origin, state_dir, key_file, relay_control=None)`. Minor.
- Already on the board, still open: M3 members do not approve each other after a join by code.

## Checked, no defect found
- messenger `_remote_peer_identity`: approved registry key wins, locator only fills unknown names and never overrides a rejected or different key, locator peers reach `peer_forward`/`peer_secure` only.
- Locators must be signed by a relay key in `approvals`; transit limits (`_admit_transit`), `ensure_session`, session bindings, defaults readers (only two, both True).
- `relay serve`: lock before key creation, atomic publish, argument validation (odd domains, binds, state dirs: no traceback), printed configs, `python -m kollabor_cli_main` has a main guard, `--origin` dispatch to the bare worker, vhost rewriter fails safe.

## Counts
- Full unit suite: 5189 passed, 9 skipped (was 5186 at e44ab6d; +3 tests). Ruff clean on `plugins/hub/peer_transport.py` and `tests/unit/test_peer_transport.py`. No CHANGELOG edit.

## Next
- Read `tests/live/m4/*.sh` (never reviewed) before the M4 live run; decide the two open design items above.
