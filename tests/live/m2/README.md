# m2: live proof for agent network milestone 2 (#121)

Proves Story 8 of `docs/specs/agent-network-simple-flow.md` (the sealed config
follows Marco) on installed packages, driven through tmux, on this Mac and
`alzan-prod`. It runs on the network `m1/proof.sh` builds: the Mac issued the
join code, so the Mac is the primary and the server is the secondary.

Written, not yet run live.

## Preconditions

- The wheels come from a ref that contains milestone 2 (branch tip 704a7f6 or newer). Build the commit you mean to ship.
- `m1/proof.sh` passed on that build and its tmux sessions `m1-mac` and `m1-srv` are still up. Do not run `m1/teardown.sh` first.
- The server holds `~/.kollab/private/managed-config.json` (it appears a few seconds after the join). `proof.sh` stops at `s8-pre` if not.
- Nobody is attached to the `m1-*` sessions. Do not run with `bash -x`.

## Order

```
bash m1/build_wheels.sh <git-ref> <wheeldir>
bash m1/install_both.sh <wheeldir>        # both hosts: same venvs and wheels for m1 and m2
bash m1/proof.sh                          # joins the server to the Mac's network, leaves both sessions up
bash m2/proof.sh                          # Story 8 on that network, a few minutes
bash m1/teardown.sh
```

## What it does

| Step | Proves |
|---|---|
| `s8-pre` | both sessions up; the server recorded the Mac as its primary |
| `s8-loadout` | the Mac gets profile `m2-proof` (fake key `sk-m2-proof-<random>`) and activates it; within 60 s the server has the same `active_profile` and model, a key with the same sha256, `config.json` mode 0600 |
| `s8-config-screen` | `/config` on the server, filtered by `loadout`, reads `Loadout: m2-proof   managed by <mac device>` and the same for Model |
| `s8-sealed` | the fake key is in no pane history (3000 lines) and no `kollab.log` on either host |
| `s8-skill-add`, `s8-skill-del` | a skill made on the Mac reaches the server with the same digest within 120 s, and deleting it removes it there within 120 s |
| `s8-cleanup` | the Mac drops the profile and its `active_profile` goes back; the server follows within 60 s |
| `s8-excluded` | the server's `oauth/openai.json` digest and `hub/vaults` listing are what they were before the run |
| `z1-panes-clean`, `z2-log-*` | no 64-hex string, `relay:` address or error text on the panes captured in this run or in either log since it started |

Exit code 0 only if every row is PASS. Evidence is in `m2/evidence/<step>.txt` (gitignored), with `pane-findings.txt` and `logscan-*.txt`.

## What it touches

- Mac: `~/.kollab/config.json` (atomic rewrite adding and removing profile `m2-proof`), `~/.kollab/skills/m2-proof-skill/`. Both are put back on any exit. If the run dies hard: `python3 m2/probe.py restore <the active_profile printed in s8-pre, or - if unset>` and `python3 m2/probe.py skill del`.
- Server: nothing directly. It follows by sync, so its running agent sits on the fake loadout for the length of the run. Do not message it meanwhile. It also gets `~/kollab-m1/bin/probe.py`.
- Already true after the m1 join with this build: the server follows the Mac's whole `config.json` (real API keys included, mode 0600), `agents/` and `skills/`. That is the feature; know it before running against the real hosts.

## Not covered on purpose

- The fullscreen `/llm` picker. The proof writes `kollabor.llm.active_profile` to the global config, which is what a switch persists; try `/llm` by hand once and watch the server's `/config`.
- Restore on reconnect: on the server, edit by hand a value the Mac's `config.json` also sets, restart the server TUI (the launch command of `m1/proof.sh`), and within 60 s of the relay coming back it reads the Mac's value again. A value the Mac does not set is never sent, so it would stay.
- Leave and revoke (Story 9), the chain case, the duplicate `kollab-…` profile a join still copies (constitution section 15), and the Story 1 lines: `sealed config queued: ...` on the Mac after accept, and the `config` row on the server's Connect screen.
