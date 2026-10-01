# m-knock-announce (#121, 0.11.0 small bugs 1 and 2)

Two commits on a worktree branch off issue-121-network-simple-flow (f737492). Not pushed.

## Done
- 4cd0a0e Bug 1, knock leftovers
  - `knocks` ({key: epoch seconds}) in RelayState; the client adopts it (`_adopt_bridge_fields`), `rotate_room` resets it
  - relay_agent.py: `_bind_knocked_peer` records the time (a repeat knock restarts the week); `expire_knocks()` runs first in `_refresh_directory` (every 15 s); `_clear_knock` = `client.revoke` + `store.revoke` + drop the record; `_receive` drops the record when the peer reaches us
  - Never expired: key in `client.peers()` (link live both ways) or it reached us. A key no longer in `links` (joined by code) is not revoked. Offline = no judgement.
  - Rejection: the directory tells the knocker nothing (decisions are recipient-only: contact_requests.decide, relay_service contact_decision_handler, no sender-side read). A rejection is silence and expires at 7 days; no separate rejected path exists to hook.
  - Tests tests/unit/test_knock_expiry.py (8): time recorded; 7d+1s cleared; 6d kept; offline kept; live link kept; reached-us kept; re-knock restarts the week; joined-by-code not revoked. Clock: `bridge._wall_clock`.
  - Docs: one paragraph in Story 5 of the constitution; CHANGELOG Fixed line (both copies byte-identical)
- ba5f56a Bug 2, announced ids
  - `announced` list in RelayState (cap 128), `RelayClient.remember_announced`; RelayCommands loads it at init, saves after each `new_arrivals`
  - Pruned to ids still pending. A kind that could not be read keeps its ids (issuer raising/unready, offline, or no successful knock fetch yet), so a cold restart does not wipe them.
  - Tests tests/unit/test_announced_persist.py (3): restart announces only new; pruned to pending; unreadable kept
- Full unit suite: 5311 passed, 9 skipped, 0 failed.

## Left
- Nothing for the two bugs.
- Not built: rejection-driven clearing on the knocking side. It needs a directory change (a sender-readable decision); only if wanted after 0.11.0.
- Edge: a knock accepted while this device was never online at the same time as the peer, then first seen after 7 days, is expired like silence; the person knocks again.

## Next step
- Cherry-pick 4cd0a0e and ba5f56a onto issue-121-network-simple-flow, then continue the 0.11.0 goal plan.
