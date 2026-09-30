# story5: live proof for a stranger's agents reaching an allowed agent (#121)

Proves Story 5 of `docs/specs/agent-network-simple-flow.md` on installed packages,
driven through tmux like a user, on this Mac and `alzan-prod`: two devices in two
networks of their own on kollabor.ai, one knock, one accept, one allowed agent.
The main session runs it (and redeploys the relay first); this directory only holds
the tooling.

## What it does

1. The Mac and the server each start their own network on kollabor.ai (`/connect`, empty code, Enter). Neither joins the other.
2. The Mac starts a second agent (`kollab --as peridot`), so it has one agent to allow and one to leave alone.
3. The server runs `/connect knock <the Mac's contact route> "..."`; the Mac reviews it with `/connect knocks`, presses `a`, and runs `/connect allow <server-device> <agent>`.
4. Each side lists the other's agents (`kollab --hub status`): the server sees only the allowed agent, the Mac sees the server's agent that knocked.
5. From a plain shell on the server: `kollab --hub msg <allowed>@<mac-device> "run uname -n ..."` prints the Mac's hostname and exits 0 (the Mac's shell tool ran).
6. The same for the agent nobody allowed: `unknown agent@device`, non-zero exit, nothing ran on the Mac.
7. The Mac runs `/connect deny <server-device> <agent>`: the next message is refused. It allows the agent again (it answers), then runs `/connect revoke <server-device>`: neither side lists the other, and the message gets `unknown agent@device`.
8. Every captured pane and both kollab logs are scanned: no 64-hex key, no `relay:` address, no error text (the expected refusals are checked for leaks only).

## Preconditions (check these first)

- The commit you mean to ship is what `m1/build_wheels.sh` built and `m1/install_both.sh` installed on both hosts (fresh venvs at `~/kollab-m1/venv`). The relay on kollabor.ai is a build of the same commit; `proof.sh` probes `POST /relay/v1/contact/links` first and stops unless it answers 400 (the `deploy_relay*.sh` scripts print the same probe).
- ChatGPT login on both hosts (`~/.kollab/oauth/openai.json`); every agent runs `--llm openai-oauth`.
- `uv`, `tmux`, `python3` >= 3.12 on the Mac; `ssh alzan-prod` works with no password.
- Nobody is attached to the `s5-*` tmux sessions while it runs (keys vanish). Do not run with `bash -x`.

## Order

```
bash m1/build_wheels.sh <git-ref> <wheeldir>   # once per commit
bash m1/install_both.sh <wheeldir>
bash story5/proof.sh                           # the driver; exit 0 only on a full PASS
bash story5/teardown.sh                        # only s5-mac, s5-mac2, s5-srv and processes from the m1 venvs
```

Network identity is keyed by workspace path, and the script refuses a workspace that already has state. For another run pick new ones:
`S5_MAC_WS=$HOME/kollab-s5-mac2 S5_SRV_WS_NAME=kollab-s5-server2 bash story5/proof.sh`.

| Setting | Default | Meaning |
|---|---|---|
| `S5_MAC_WS` | `~/kollab-s5-mac` | the Mac's fresh workspace |
| `S5_SRV_WS_NAME` | `kollab-s5-server` | the server's, under its `$HOME` |
| `S5_SECOND_AGENT` | `peridot` | hub identity of the Mac's second agent |
| `S5_CLI_SECS` | `300` | time limit of each `kollab --hub` call |

## What a pass looks like

`proof.sh` ends with a table and `PASS: N steps, transcript clean.`, exit 0. Rows:
`pre-*` (build, the live links route, fresh workspaces, logins), `s0-launch`, `s1-two-networks`, `s1-mac-has-two-agents`, `s2-mac-contact-route`, `s3-server-knocks`, `s4-mac-accepts-knock`, `s5-mac-allows-one-agent`, `s6-link-and-visibility`, `s7-delivered-and-answered`, `s8-other-agent-unreachable`, `s9-deny-refuses`, `s10-allow-again-works`, `s11-revoke-cuts-the-link`, `z1-panes-clean`, `z2-log-mac`, `z2-log-srv`. Evidence: `story5/evidence/<step>.txt` (64-hex and `relay:` values replaced before writing), `pane-findings.txt`, `logscan-*.txt`.

If `s1-mac-has-two-agents` fails, the second agent did not come up as a separate agent in the Mac workspace, so "a message to a non-allowed agent" is not proven; start one by hand (`kollab --as peridot` in `~/kollab-s5-mac`) and rerun with new workspaces, or fix how the launch is done.

## Not covered on purpose

The Codex `manual` trust model, sealed config sync, mesh, and the 80/120 column checks (that is `m1`). A knock from a device that is not on the knocked directory: the knock is sent but no path opens (documented in the guide).
