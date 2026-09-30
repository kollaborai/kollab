# m1: live proof for agent network milestone 1 (#121)

Proves Stories 1, 2 and 3 of `docs/specs/agent-network-simple-flow.md` on
installed packages, driven through tmux like a user, on this Mac and `alzan-prod`.

## Preconditions (check these first)

- The final `issue-121-network-simple-flow` commit is merged (or is the ref you build). Build the exact commit you mean to ship.
- The relay on kollabor.ai already serves `POST /relay/v1/enrollment/lookup`. `proof.sh` probes it first and stops on 404/405/5xx.
- ChatGPT login exists on both hosts: `~/.kollab/oauth/openai.json` (both agents run `--llm openai-oauth`).
- `uv`, `tmux`, `python3` >= 3.12 on the Mac. `ssh alzan-prod` works with no password. tmux and python3 >= 3.12 on the server.
- Nobody is attached to the `m1-*` tmux sessions while it runs (keys vanish).
- Do not run with `bash -x`: it would print the join code.

## Order

```
bash m1/build_wheels.sh <git-ref> <wheeldir>   # 11 wheels stamped 0.11.0.dev1, sha256 in <wheeldir>/MANIFEST.txt
bash m1/install_both.sh <wheeldir>             # fresh venvs, both hosts, must print 0.11.0.dev1 twice
bash m1/proof.sh                               # the driver; exit 0 only on a full PASS
bash m1/teardown.sh                            # only m1-mac, m1-srv, and processes from the m1 venvs
```

| Script | Needs | Touches |
|---|---|---|
| `build_wheels.sh` | git ref, `uv`, network (build deps, webui runs npm if present) | a mktemp export of the ref (left in `$TMPDIR`), `<wheeldir>` |
| `install_both.sh` | wheels, ssh, PyPI on both hosts | `~/kollab-m1/`, `~/kollab-m1-mac`, `~/kollab-m1-server` on the server, same names |
| `proof.sh` | both venvs, relay, ssh | tmux `m1-mac` / `m1-srv`, the two fresh workspaces, `m1/evidence/`, `~/kollab-m1/bin/` on the server |
| `teardown.sh` | nothing | only what `proof.sh` started |

Never touched: the uv tool install, `~/kollab-e2e-*`, tmux `e2e-mac`, `e2e-srv`, `sh-relay`, `sh-pub`, `sh-static`.
Shared, so watch it: both hosts' `~/.kollab/config.json`, `oauth/`, `agents/` (a 0.11.0.dev1 launch may rewrite `config_version` / `last_app_version`).

## Re-running

Network identity is keyed by workspace path. `proof.sh` refuses a workspace that
already has state. For another run: `mkdir ~/kollab-m1-mac2`, then
`M1_MAC_WS=$HOME/kollab-m1-mac2 M1_SRV_WS_NAME=kollab-m1-server2 bash m1/proof.sh`
(create the server dir first: `ssh alzan-prod mkdir kollab-m1-server2`; the script also creates it).

## What a pass looks like

`proof.sh` ends with a table and `PASS: N steps, transcript clean.`, exit 0:

- `pre-*` installed 0.11.0.dev1 on both, relay lookup route live, fresh workspaces, logins present.
- `s1-*` Mac shows a join code, server takes it in the private form, Mac sees `<server-device> wants to join`, accepts, both `/connect status` list `agent@device` for the other side.
- `s2-agent-to-agent` reply arrives from `agent@device`, carries `uname -n` / `uptime` content, and the server log shows a new shell tool run (`Tool execution completed: [SUCCESS] terminal:`).
- `s3-*` `kollab --hub msg agent@device "..."` from a plain shell prints a reply and exits 0, again under `env -i`, and the server ran its shell twice.
- `s5-width-80` no line wider than 80 on the Connect screen and `/connect status`, both hosts.
- `z1-panes-clean`, `z2-log-*`: on every captured pane and both kollab logs, no join code, no 64-hex string, no `relay:` address, no `receipt`, and no `Traceback`, `ERROR`, `Failed executing`, `refus`, `denied`, `cannot`, `unknown subcommand`.

Any FAIL prints its reason under the table. Evidence: `m1/evidence/<step>.txt` (join code, 64-hex and `relay:` values are replaced before writing), `pane-findings.txt`, `logscan-*.txt`.

## Fallback the driver takes on its own

If bare `/connect` on the Mac shows no join code (a build without the Connect screen), the run says so
and uses `/connect kollabor.ai` (only if the Mac has no network), `/connect code` (presses Enter on
"create code") and `/connect accept <device>`. The step note reads `FALLBACK path`. A fallback run
proves the flow, not the milestone's target screens.

## Not covered on purpose

Sealed config sync (Story 8), knock, trust levels, mesh. The 390 / 820 px checks are web-UI checks; here it is 80 and 120 columns.
