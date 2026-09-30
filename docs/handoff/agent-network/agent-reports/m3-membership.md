# M3 membership: every device on a network approves every other

Branch `worktree-agent-a5201877ee891a111` (from 3aa0d05). Code commit 12a9948; the tip is the commit that adds this note. Not pushed, no PR, no live run.

## What changed
- New `plugins/hub/network_members.py`: a signed member list (`network_members` secure method, Ed25519 over voucher, recipient, network binding, members, revoked) plus `MembershipSync`, a loop like config sync that sends each online member its list and hears theirs. Started next to config sync in `relay_agent.py`; the method is allowed in `secure_conversation.py` and turned away from accepted strangers.
- `relay_client.py`: `members()` (approved minus strangers, the one set both paths use), `membership()`, `accept_membership()`, `network_id()`; `revoke(key, announce=True)` drops the device, tombstones it, and cascades to devices whose only vouch came from it. `/connect revoke` now announces. State gains `vouched_by` and `revoked` (`relay_state.py`, validated, reset by rotate/leave).
- A list names first-hand approvals only (a person here accepted the device, or joined through it). That keeps support rooted in a human's accept: two members can never keep a revoked device alive by naming it to each other.
- Names travel with the vouch; a taken or invalid name falls back to the short key label. A vouch approves and nothing else: no grants, no trust entry, no config recipient.
- Review fix (`peer_transport.py` `endpoint_key_for`): a designation goes to the first approved member whose live locator claims it; strangers never claim; a second key cannot block the first device.
- Docs: constitution sections 4 (Members) and 10 (item 5), HANDOFF board and Decisions, CHANGELOG pair (`cmp` clean).

## Tests
`tests/unit/test_network_members.py`, 9 tests on real bridges over the relay wire: 3-device chain approves every pair; relay and mesh agree on members; vouch is not a grant; revoke drops on every member; revoked stays out until a person re-accepts; cascade across 4 devices; stranger never vouched or vouching; forged, tampered, misdirected and cross-network lists refused; endpoint claim binding.
Full suite: 5204 passed, 9 skipped (203 s). Ruff clean on all touched files.

## Decisions for Marco (veto welcome)
- A revoked device rejoins on a member only when a person on that member accepts it again by code (a received vouch never lifts a revocation; fail closed). Other members keep refusing it until they do, or until `/connect rotate`.
- Revoking B in A-B-C also drops C on A (its only vouch was B). A then has no member left to tell, so C keeps its own approvals until it is re-invited.
- One list holds 64 members (about 11 KB of the 24 KB frame); paging is the upgrade past that.

## Next
- `tests/live/m3/` can drop its B<->C approval seeding: approvals spread once B and C are online (about 10 s per hop; tick is 10 s).
- Live run on Mac and alzan-prod (three devices, then `/connect revoke`), then the remaining M3 items in `m3-mesh.md` "Not done".
