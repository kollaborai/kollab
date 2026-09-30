# M2 finish: report (queue task 1 done)

Branch `worktree-agent-a5cc4061fe4f00ca6`, fast-forwarded to 704a7f6. Content commits 047dd18 (docs) and 1244070 (`tests/live/m2/`); the commit that adds this file is the tip. Not pushed, no PR. No code changed.

Changed
- `docs/specs/agent-network-simple-flow.md`: decisions row; Story 1 accept line (`sealed config queued: ...`) and the server's `config` row; Story 8 (Loadout/Model rows, key shows as `set`); section 9 rewritten to the code (primary and chain, synced, not synced with the machine-local list and size limits, primary wins, `/config` rows, timing, leave/revoke/rotate); section 12 item 2 proof bar; section 15 item: a join still copies the issuer's API-key profile once, so a duplicate loadout can show.
- `docs/guides/connect.md` "What accepting copies"; `CHANGELOG.md` and `kollabor/updates/CHANGELOG.md` one Added line, `cmp` prints nothing.
- `tests/live/m2/`: `proof.sh` (Story 8), `probe.py` (state/switch/restore/skill on either host), `README.md` (run order), `.gitignore`.
- `HANDOFF.md`: M2 row, task 1, live-run order.

Checks
- `bash -n` and shellcheck clean; `probe.py` py_compile and ruff clean, run on a throwaway HOME.
- Offline dry run of `proof.sh` on stub tmux/ssh/scp and two fake homes: 11 steps PASS. With the key planted in a pane, `s8-sealed` FAILs and exit is 1. Nothing live, no ssh.
- Full suite: 5186 passed, 9 skipped, 203 subtests (112 s).

Not proven
- `tests/live/m2` is unrun live. The stub sync is a file copy, so the dry run checks flow and quoting, not the product.
- The proof rewrites `kollabor.llm.active_profile` in the Mac's `config.json` (what `/llm` persists) instead of driving the picker. The server's agent sits on the fake `m2-proof` loadout during the run.
- Restore on reconnect, leave/revoke, the chain case and the Story 1 screens are manual (README "Not covered").

Next: task 2 (M3 finish) or task 5's live runs (`m1/proof.sh`, then `m2/proof.sh`, then `m1/teardown.sh`). Merge: cherry-pick 047dd18, 1244070, then this commit.
