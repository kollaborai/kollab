# b-lone-device (issue #121, 0.11.0 guided setup: a device alone on a network)

## Commit
- One commit (git log -1, it carries this report): a lone device counts as not set up in the guided setup.

## What changed
- Lone = `RelayState.is_alone()` (relay_state.py): no approvals, inviter, peer_devices, config_recipients or links.
- Notice: `_connect_has_network` (plugin.py) now means "shares a network with another device". Attached window: the daemon snapshot has no remote_agents, offline_devices or config_from. A daemon too old for a snapshot keeps the status-text path.
- Start: `_guided_new_network` targets the device's own domain (kollabor.ai when it has none). `RelayClient.connect` on the same origin keeps room, workspace_id and approvals and names the network if it has no name and no inviter (existing code), so no second network; the Connect screen opens with the box.
- Join: the only refusal was `enroll_device` (enrollment_client.py): a state with an origin gave `{'error': 'conflict'}`. A lone state now goes through `client.leave()` (disconnect, origin cleared, fresh room, name forgotten) and the join continues as on a fresh device. Any other device on the network still gives `conflict`, state untouched. Same join path, no second mechanism.
- Docs: constitution Story 1 bullets, docs/guides/connect.md, one CHANGELOG line (CHANGELOG.md and kollabor/updates/CHANGELOG.md, cmp identical).

## Tests
- tests/unit/test_connect_lone_device.py, 15 tests: the exact 0.10.7 state.json (keys origin, enabled, room, workspace_id, approvals, inviter; mode 0600) and a named lone network get the two choices; each of approvals / inviter / peer_devices / config_recipients / links, and three attached-snapshot variants, keep the Connect screen; Start targets the device's own domain and a real `RelayClient.connect` keeps room and workspace and names it; a join from a lone state replaces it, a join with an approved device still conflicts.
- Seen failing first: 6 of 15 (both notice cases, attached lone snapshot, Start target, Start `is_alone`, join `conflict`). The other 9 are guard rails that held before and after. The `connect` room-kept check already held (existing behavior); only `is_alone` was missing.
- Full unit suite: 5294 passed, 9 skipped, 1 failed. The failure, test_connect_command_fixes.py line 439, was already red at e56f779 (`_open_connect_screen` now also gets `guide=False`); fixed in this commit with the one-line assertion change.

## Left
- Not driven live (no ssh): a real 0.10.7 to 0.11.0 upgrade on a lone device: notice, two choices, Join with a code from a code issued elsewhere, then Start with the box.
- A join that is declined or expires after `leave()` leaves the device on no network (enabled false, new room). Decide whether launch should recreate the lone network.
- An attached window that cannot read the shared state file has an empty `_relay_network_domain()`, so Start targets kollabor.ai; a lone device on another directory would get "could not start a network".
- Bare `/connect` on a lone device still opens the Connect screen (as specified: only the guided notice treats lone as not set up).
