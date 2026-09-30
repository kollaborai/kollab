# Review of 9fe266f (knock route port) — punch list for the fixer

Reviewed at commit 9fe266f. "Ran" = the reviewer reproduced it. Line numbers are at that commit.

1. HIGH (ran). plugins/hub/relay_client.py:351 (connect) and :363 (close(disable)); ordering at relay_agent.py:183-186.
   A knock-accepted stranger silently ends up with `open` trust: after accept, disk has peer_trust={key:'agents'}; then
   client.connect() or client.close(disable=True) (so `/connect leave` or `/connect <domain>`) saves the client's STALE copy
   of RelayState, peer_trust becomes {} and effective_trust(key)=='open' under the default network trust.
   Cause: `_adopt_bridge_fields` (relay_client.py:249) runs only inside approve/revoke.
   Also: `/connect leave` (relay_commands.py:619-626) wipes peer_devices/peer_trust but leaves approvals, so an approved
   stranger reverts to network trust.
   Fix: call `_adopt_bridge_fields()` before the saves at :351 and :363 (better: one state object, no second copy).
   `leave` clears approvals too. Test: accept, then client.connect/close, assert peer_trust and peer_devices survive.

2. MEDIUM (ran). plugins/hub/contact_requests.py:475-482 (loop) and :320. One bad frame kills the whole knock inbox:
   a sealed `{"introduction":"hi"}` without device_name (or with an extra key / non-str name) raises invalid_response and the
   loop has no per-frame guard; `/connect knocks` says "Knock inbox is unavailable." for the 24h TTL.
   Fix: try/except ContactProtocolError per frame and continue; device_name optional in the envelope (absent -> route[:8]).

3. MEDIUM. plugins/hub/plugin.py:8749 (`/connect knocks`) and :8738 (`/connect code`): default domain is "kollabor.ai" but
   `/connect status` prints `<network-domain>/c/<route>` (relay_commands.py:277). On a self-hosted directory knocks reads the
   wrong inbox. Fix: default to `self._relay_network_domain() or "kollabor.ai"` in both.

4. MEDIUM. plugins/altview/contact_altview.py:155-176, 192. Every row shows `[a]ccept [r]eject` but keys always act on
   `self._requests[0]`; no busy state while the decision is awaited, so a double-tap accepts the next stranger unseen.
   Fix: a cursor (Up/Down selects, a/r act on the selected row, hint shown on the selected row only), ignore keys while a
   decision is in flight, confirmation names who was decided.

5. MEDIUM (ran). plugins/altview/contact_altview.py:155-165. Row budget ignores the sender-chosen name and counts code points,
   not display columns: a 63-char name at 80 cols pushes fingerprint and hints off; at 60 cols the hint is cut mid-token; tabs
   and wide characters overflow. Fix: cap the name (~20 with …), drop tabs, measure display width; tests at 60/80/120 asserting
   every row fits the width.

6. MEDIUM. plugins/hub/plugin.py:114-115 (palette), docs/reference/commands.md, tests/tmux/specs/connect_palette_subcommands.json:33
   say accept/reject decide "a join or knock request by name", but relay_commands.py:521-551 resolves join requests only.
   Fix: "Accept a join request by name" / "Reject a join request by name" in all three places (and the spec's assertion).

7. MEDIUM (ran). plugins/hub/relay_agent.py:183-193 and 868-874. Rollback restores the wrong state: a second knock from an
   already-accepted key under a new name, with the relay POST failing, ends with approvals=[], peer_devices={}, peer_trust={}
   (the earlier accepted peer is revoked). Without a failure the second accept silently renames the peer. `_bind_knock_peer`
   rolls back only on RelayError; an OSError from approve's save leaves the binding orphaned.
   Fix: snapshot prior approval/name/trust and restore exactly that on any exception; refuse re-binding a key that already
   has a different name (human sees why).

8. MEDIUM/LOW (read). plugins/hub/relay_agent.py:309 and relay_commands.py:180. The name-collision check compares only the own
   name and peer_devices values; a joined device never binds its issuer's name, so a stranger can knock as "mac-kollab" there
   and `/connect allow|deny|revoke mac-kollab` then targets the stranger's key.
   Fix: also reject names present in the roster's `device` values (remote_agents rows) and approved peers' self-reported names;
   and bind the issuer's name on the joining device when the join completes.

9. LOW. plugins/hub/relay_client.py:268 and plugin.py:9021. `RelayError("local peer approval capacity reached")` has no
   `.code`; `_rpc_contact_decide` maps it to "transport" ("try again" forever). Fix: error.code = "capacity".

10. LOW. plugins/hub/relay_backend.py:1539-1544 (`_touch_contact_route`) and :1522-1527. SADD then EXPIRE are not atomic and
    both errors are swallowed: if EXPIRE fails the set has no TTL and a crashed peer is returned by lookup indefinitely;
    reconnect race: new SADD followed by old SREM unlists a live key until the next renew.
    Fix: pipeline(transaction=True) for sadd+expire, log at warning instead of pass.

11. LOW (ran). plugins/hub/contact_requests.py:112-117. `PendingContactRequest.__repr__` prints `sender='ed25519:<64 hex>'`
    and the receipt id. Fix: repr shows device_name and expires_at only; delete `sender_identity` (no other user).

12. LOW (tests). tests/unit/test_relay_contacts.py ~292: the assertion `backend.rooms == {}` ("a knock creates no room
    membership") was removed with no replacement; restore it. Missing tests: accept leaves the allow list empty; stale-client
    save (item 1); `_safe_display_text` (ESC, C1, bidi); truncation at 60/80/120 asserting the row fits.

13. LOW. plugins/hub/relay_commands.py:546. The join accept confirmation prints `receipt: <32hex>`; receipts are never shown.
    Also the join form (plugins/altview/connect_altview.py) still prints `Receipt:` after a code is entered.
    Fix: confirmations use the device name; remove receipts from every screen and returned string.
