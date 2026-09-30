# M2 sync debug: why the sealed config never reached the server (proof r9)

Branch of this worktree, fast-forwarded from `issue-121-network-simple-flow` at 71798d1. Not pushed, no PR, no live runs, ssh was read-only.

## Root cause (product bug, plus a weak proof check)
- The receiver refused every bundle with `other_primary`. Mac log, 08:51:10 to 09:07, `config_sync_service.py:0219`: `WARN config sync: a device did not take the settings (other_primary), retry in 10s`, doubling to 300s. The primary pushed and was refused; nothing else in either log (send, seal and apply were never the problem).
- `Receiver._check` (`config_sync.py:788`) refuses when the managed record names a different primary. The record is machine-global (`~/.kollab/private/managed-config.json`); the relay identity is per workspace (`~/.kollab/network/<sha256 of the workspace>`). r9 is a fresh workspace, so a new Mac key.
- Server record: written 07:55 by the r8 run, `primary_name` `synthyo-kollab-m1-mac-r8`, primary key hash `4e7184bc`. The r9 Mac's device key hash is `27a31616` and the server's r9 `inviter` hash is `27a31616` (join was right, the record was stale). `leave` clears the record, teardown never runs `leave`, and `join` did not touch it, so it refused the new primary for good.
- `s8-pre` passed vacuously: it only checks a record exists (it printed the r8 name and passed).

## Fix
- `fe740e0` `RelayClient.join_invite` drops a record whose primary is not the new inviter (same primary keeps it). Only for a state under the machine's own `~/.kollab/network`, so tests with a tmp `state_dir` never touch the real record. `tests/unit/test_join_rebases_managed_config.py`: the stale-record test fails before the fix, passes after; 2 guard tests.
- Suite: `tests/unit/` 5284 passed, 9 skipped, 203 subtests (baseline 5202 on the last note). ruff clean on touched files.

## Rebuild
- Yes: rebuild and install on both hosts. The join runs on the joining device, so the server needs it; without a rebuild delete `~/.kollab/private/managed-config.json` on the server (and the Mac, if present) before `m1/proof.sh`.

## Not done
- `tests/live/m2/proof.sh` s8-pre should require the record's primary to be this Mac (e.g. `probe.py state <workspace>` comparing the record key with the relay `inviter`). Two devices logged the refusal at 08:57; I did not identify the second recipient.
- Multi-workspace machines: the latest join now wins the global record, and an older network's primary is then refused quietly (WARN on its log only). Nothing tells either user.
