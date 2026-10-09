# m3: live proof for agent network milestone 3, the mesh (#121)

Proves section 10 of `docs/specs/agent-network-simple-flow.md` on installed packages, driven
through tmux, on this Mac and `server`: **A** (the Mac) reaches **C** through **B**.

- A is the Mac, tmux `m1-mac`. B is the server device, tmux `m1-srv` on server. Both are the devices `m1/proof.sh` left joined.
- C is a second device on server: own workspace `~/kollab-m3-c`, own tmux `m3-c`, own hub identity (`--as peridot`; two devices on one home need distinct identities).
- C is on **no relay**. Its only reachable door is B's loopback TLS endpoint, `127.0.0.1` on the same host, which the Mac cannot reach. A gets to C only if B forwards, and B can forward only opaque, end-to-end sealed frames.

Written, not yet run live.

## Preconditions

- The wheels come from a ref that contains milestone 3 (`feac607` or newer) **and** the change "every network member approves every other member". Without it step `c3-members` fails: B and C only approve their inviter after a join by code. The proof does not seed B<->C approvals; that is deliberate, do not add any.
- `m1/proof.sh` passed on that build and its sessions `m1-mac` and `m1-srv` are still up. Do not run `m1/teardown.sh` first. `m2/proof.sh` may run before this; it leaves the sessions up.
- server has the `openssl` CLI and `certifi` in the m1 venv, tcp `8801`/`8802` and udp `39531` free (`ss -lntu`), and nobody attached to the three sessions. Do not run with `bash -x`.
- The Mac and the server need no `peer_direct_enabled` / `peer_forward_enabled` keys. Both default on; `c8-defaults-on` fails if any config sets one.

## Order

```
bash m1/build_wheels.sh <git-ref> <wheeldir>
bash m1/install_both.sh <wheeldir>
bash m1/proof.sh                # A and B joined, both sessions left up
bash m3/proof.sh                # the mesh, about 15 minutes, needs one paid model turn on C
bash m3/teardown.sh             # C, its workspace, the certificate, B's config
bash m1/teardown.sh
```

## How C is made relay-less

1. C is started in a fresh workspace and joins A's network by a code A issues (`/connect code`, `/connect accept`), exactly as B did. That gives C its network state under `~/.kollab/network/<digest>`.
2. C is stopped: its tmux window and every process whose cwd is `~/kollab-m3-c`.
3. `probe.py relayless` sets `enabled` to `false` in that `state.json`. A device that starts with it off never connects to the relay, so it runs under its own per-process direct session.
4. C is started again with the same identity. A's status must have dropped C before this (`c4-c-relayless` waits for it), so a later listing cannot be a leftover.

B and C each get the same config keys, written to the workspace `.kollab/config.json` by `probe.py config` (nothing else): `endpoint_enabled`, `endpoint_host` `127.0.0.1`, `endpoint_port` (8801 / 8802), `endpoint_advertise_host` `127.0.0.1`, `endpoint_tls_cert/key/ca`, `peer_allow_private_network`, `peer_discovery_advertise_enabled`, `peer_discovery_scan_enabled`. `endpoint_tls_ca` is the loopback certificate plus the public roots (certifi), because the same CA file also verifies kollabor.ai. A gets no config.

## What it does

| Step | Proves |
|---|---|
| `pre` | both m1 sessions up; ports free; nothing of an earlier run left; tools present |
| `c1-endpoints` | B restarted with the endpoint keys is bound on `127.0.0.1:8801` and on the LAN discovery port |
| `c2-c-joins` | C joined A's network by code; the code never shows on C (masked field); one new state dir |
| `c3-members` | B lists C's agent and C lists B's, with no hand-made approvals |
| `c4-c-relayless` | A dropped C; C restarted with `enabled=false`, its status says `reconnect on launch disabled`, listens on `127.0.0.1:8802` |
| `c5-a-sees-c` | A's `/connect status` lists `peridot@<C device>` again, with C on no relay: learned through B |
| `c6-message` | `kollab --hub msg peridot@<C device> ...` from A exits 0 and prints C's answer (the reply word) |
| `c7-sealed` | request and reply markers are in no line of B's pane history (3000) or `kollab.log`, and the request showed on C |
| `c8-defaults-on` | no config sets `peer_direct_enabled` / `peer_forward_enabled`: the route ran on the defaults |
| `z1-panes-clean`, `z2-log-mac`, `z2-log-srv`, `z2-log-c` | no join code, 64-hex string, `relay:` address or error text on the captured panes or in any of the three logs since the run began |

Exit code 0 only if every row is PASS. Evidence is in `m3/evidence/<step>.txt` (gitignored), with `pane-findings.txt` and `logscan-*.txt`.

## What it touches

- Mac: nothing on disk except tmux scrollback (`clear-history` after C's join so the code is gone).
- Server: B's `.kollab/config.json` (backed up as `config.json.m3-bak`, or `config.json.m3-none` if there was none) and a restart of B's TUI; `~/kollab-m3-c`; `~/kollab-m1/m3-tls` (key, cert, CA, mode 0600); C's network state and hub vault under `~/.kollab`. `teardown.sh` removes the workspace, the certificate and the config change; the network state and vault stay.
- A production relay is used for C's join only. No deploy, no relay change.

## First-run risks (nothing here has run)

- Stopping a window does not always stop the daemon behind it. `stop_ws` also kills every process whose cwd is the workspace; if `c1-endpoints` shows B still not listening, look for a daemon started from another directory (`pgrep -af kollab`).
- The wording of `/connect status` rows and of the join lines is copied from `m1/proof.sh`. A changed word fails a step loudly at the step, not silently later.
- Transit limits (120 per minute per peer, 600 total, 16 at once) are proven in `tests/unit/test_mesh_network.py`, not here: driving 120 model turns a minute is not a live test.

## Not covered on purpose

- More than one hop, a stranger's route, and self-host relays (M4 has its own proof).
- The direct-then-relay fallback (covered by unit tests: `test_a_refused_direct_endpoint_falls_back_to_the_relay`).
- The open design call in `agent-reports/review-m3-m4.md` (a designation two approved devices both claim admits neither).
