# M2 and Story 5 fixes from the review

Branch `worktree-agent-a54b95f04bd111ae0`, fast-forwarded from `issue-121-network-simple-flow` at bb2282c. Last code commit 6752931, changelog 12af6e7, then this note. Not pushed, no PR, no issue, no live runs.

## Fixed (one commit each, regression test failed before the fix)
- efa62e9 `leave` cleared the managed record only when an inviter was set, and rotate blanks it, so rotate then leave kept `/config` read-only "managed by" the old primary. It now clears unconditionally (`relay_commands.py`). An autouse fixture pins HOME to a tmp dir in `test_connect_command_fixes.py` and in `test_stranger_delivery.py`, the only other test file that runs `leave` (grep).
- 3cc4c9d `_bind_knock_peer` (`relay_agent.py`) refuses a knock from a key that is approved and not in `links`, code `already_named`, which the knock screen already words as "this device is already on your network as <name>". `test_a_failed_relay_decision_keeps_an_approval_that_existed_before` now links the key first, since an approved unlinked key no longer reaches that undo.
- 874844c a keyring sentinel the primary cannot resolve goes in the core bundle as a `keep` list (`Snapshot.keep`, `core_blob`, part of the digest); `_apply_core` does not delete those paths and keeps them managed. The lookup stays cached, so no Keychain dialog. The test helper `push_core` now uses the real `core_blob`.
- 6752931 an exec-bit-only change never synced: the receiver's `sync` now also compares the local exec bit (`_local_executable`), so a mode-only change re-sends the file. No wire or hash change.

## Suite
`tests/unit/`: 5202 passed, 9 skipped, 203 subtests. ruff clean on touched files. `CHANGELOG.md` and `kollabor/updates/CHANGELOG.md` are byte-identical (cmp).

## Not done
- A keyring that unlocks later is still not noticed until config.json changes or the primary restarts (cached on purpose).
- Still open from the review: `_scan_files` re-reads files skipped for compressed size every scan (CPU only), and a rejected or ignored knock leaves the knocker's approval, link and reply grant. The live M2 proof was not re-run.
