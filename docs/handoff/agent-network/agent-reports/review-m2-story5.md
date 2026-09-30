# Review of M2 (sealed config sync) and Story 5 (delivery across rooms)

Branch `worktree-agent-a079cd31165683df0`, fast-forwarded from `issue-121-network-simple-flow` at 704a7f6. Last code commit f395f4a, then this note. Not pushed, no PR, no issue, no live runs.

## Fixed (one commit each, regression test failed before the fix)
- 9d10fa0 A secondary deleted a synced file whenever the primary skipped it (grew past 512 KiB or 30 KB compressed). The sealed `sync` header now carries `partial`; the receiver keeps and remembers absent files (`config_sync.py` `sync_body`, `_finish`).
- 2d93e84 The `sync` manifest overflowed one secure request at about 1000 files although `MAX_FILES` is 1500 (measured: 1100 entries = 69.8K chars against the 65,536 cap), so files never synced. The manifest is cut to `MAX_MANIFEST_ZBYTES`; the tail is counted as skipped (`_fit_manifest`).
- f395f4a A knock-accepted stranger later enrolled with a join code kept its `links` entry and `agents` trust on the issuer, so the issuer bound envelopes to the pair room and the member to the network room (binding mismatch). `RelayClient.add_config_recipient` now drops both.

## Not fixed (file:line, suggested fix)
- `plugins/hub/relay_commands.py:849` `leave` clears the managed record only `if inviter:`, but `/connect rotate` (`relay_client.py` `rotate_room`) already blanks the inviter. Rotate then leave leaves `/config` read-only "managed by" the old primary and every bundle from the next primary gets `other_primary`. Fix: clear unconditionally, after pinning HOME to tmp in `tests/unit/test_connect_command_fixes.py:183,201` (they would delete the real record).
- `plugins/hub/relay_agent.py:195` `_bind_knock_peer` accepts a knock from a key that is already an approved member: sets `agents` trust and `links` (same binding mismatch as f395f4a) and it stays a config recipient. Fix: refuse when approved and not already in `links`.
- `plugins/hub/config_sync.py:290-303` an unresolved keyring sentinel: a first-build miss is cached by the stat signature until config.json changes, and a restarted primary with a locked keyring drops the key, so secondaries delete it (`_apply_core`, `previous - leaves`). The M2 report's "keyring-only change syncs on reconnect" is wrong (reconnect re-sends the cached core). Fix: send unresolved paths so secondaries keep them. Do not simply un-cache: a missing macOS Keychain entry pops a dialog on every read.
- `config_sync.py` `_local_sha` / `sync`: an exec-bit-only change never syncs (content sha only). Low.
- `config_sync.py` `_scan_files`: files skipped for compressed size lose their hash cache entry every scan, so they are re-read and re-compressed every 10 s. CPU only.
- Still open from the Story 5 report: a rejected or ignored knock leaves the knocker's approval, link declaration and reply grant.
- Dropped on purpose: `ConnectSnapshot.from_wire` requires `config_from`, so an older daemon reply is refused; the unreleased-development policy says not to add compatibility for that.

## Checked, no bug found
Seal and open (signature binds kind, recipient, revision, blob sha), path and symlink refusal, `receive` gated on `state.inviter`, config recipients set only at the two join-code accept sites, stranger gates (`peer.forward` and `peer.exchange` refused, directory filtered to grants), relay links (registered signer, nonce, skew, mutual consent in memory and Lua), deploy probe lines.

## Suite
`tests/unit/`: 5189 passed, 9 skipped, 203 subtests (baseline 5186, +3 regression tests). ruff clean on the touched files.

## Next
Take the first "not fixed" item (`relay_commands.py:849` plus the HOME pin in its tests), then re-run the live M2 proof.
