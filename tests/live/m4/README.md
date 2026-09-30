# m4: live proof for agent network milestone 4 (#121)

Proves Story 6 of `docs/specs/agent-network-simple-flow.md` on installed packages: one command,
`kollab relay serve --domain selfhost.kollabor.ai`, replaces the manual stack on `alzan-prod`, and two
devices join a network on that domain and exchange a message. It builds on m1: same `env.sh`, the same
wheels and venvs (`~/kollab-m1/venv` on both hosts), the same `scan.py` / `tmuxtype.py`, the same fresh-workspace
rule. Nothing here has been run yet.

## What it changes (read before running)

| Where | What |
|---|---|
| alzan-prod | Stops the manual selfhost stack (relay supervisor, standalone publisher, static server; tmux `sh-relay`, `sh-pub`, `sh-static`). State stays. Starts tmux `m4-serve` running the one command from the m1 venv, on the old relay config's bind address (10.0.0.5) and trusted proxy (the edge), at port 9178 (`M4_PORT`): the old first worker's port, so the edge's firewall path already exists. Writes `~/kollab-m4/` (`serve.env`, `serve-cmd.sh`, `restore-old-stack.sh`). |
| alzan-edge | Rewrites the `selfhost.kollabor.ai` nginx vhost so its five routes reach the one port instead of three (`edge_vhost.sh`, `sudo -n`, backup in `/etc/nginx/m4-backups/`, `nginx -t` before the reload, automatic put-back on failure). |
| this Mac | tmux `m4-mac`, workspace `~/kollab-m4-mac`, evidence in `m4/evidence/` (git-ignored). |
| kollabor.ai | Nothing. The proof asserts that no screen and no log names it. |

`selfhost.kollabor.ai` is down between `serve_up.sh up` and `edge_vhost.sh apply`, a minute or two.

## Preconditions

- The commit you build contains `kollab relay serve --domain`. Build exactly what you mean to ship.
- The m1 preconditions: `uv`, `tmux`, Python >= 3.12, `ssh alzan-prod` with no password, ChatGPT logins on both hosts
  (`~/.kollab/oauth/openai.json`), nobody attached to the `m4-*` tmux sessions while it runs, no `bash -x` (it would print the join code).
- `ssh alzan-edge` works with no password and `sudo -n` works there.
- DNS `selfhost.kollabor.ai`, its `_agent` TXT record, the certificate and the edge vhost exist (they do today) and the
  manual stack is running, so `serve_up.sh` can read its settings (bind address, trusted proxy, publisher state directory).
- `dig` on this Mac.

## Order

```
bash m1/build_wheels.sh <git-ref> <wheeldir>     # 11 wheels stamped 0.11.0.dev1
bash m1/install_both.sh <wheeldir>               # fresh venvs, both hosts
bash m4/serve_up.sh plan                         # read-only: what runs today, what would change
bash m4/serve_up.sh up                           # stop the manual stack, start the one command, same publisher key
bash m4/edge_vhost.sh show                       # the diff the edge will get
bash m4/edge_vhost.sh apply                      # back up, write, nginx -t, reload, check public health
bash m4/verify_serve.sh                          # what an outsider sees: DNS, discovery, health, routes, websocket
bash m4/proof.sh                                 # Story 6; exit 0 only on a full PASS
bash m4/teardown.sh                              # stops the proof TUIs; the one command stays up
bash m4/teardown.sh --restore-old                # or: put the manual stack and the old vhost back
```

## What a pass looks like

`proof.sh` ends with a table and `PASS: N steps, transcript clean.`:

- `pre-*` both hosts run 0.11.0.dev1; the one command runs and the manual stack does not; `verify_serve.sh` passes; workspaces are fresh; logins exist.
- `s6-01` the Mac starts a network with `/connect selfhost.kollabor.ai` and its status line names that domain.
- `s6-02` .. `s6-08` the Mac shows a join code; the server types the company domain and the code into the private form, sees
  `request sent to selfhost.kollabor.ai`, the Mac shows `wants to join`, accepts, the server prints `joined ... trust: open`,
  and each side lists the other's `agent@device`.
- `s6-09`, `s6-10` `kollab --hub msg agent@device "..."` from a shell prints the server agent's reply, exit 0, and the server ran its shell.
- `s6-11` neither `/connect status` nor either `kollab.log` names bare `kollabor.ai`.
- `s6-12` .. `s6-14` Ctrl-C stops the one command without a traceback; started again it loads the same signing key (revision higher);
  both devices reconnect on their own and answer another message.
- `z0` .. `z2` the command's own output, every captured pane and both kollab logs hold no join code, 64-hex string, `relay:` address,
  receipt or error text.

`verify_serve.sh` on its own checks the parts an outsider can: the `_agent` TXT record, discovery through the client code (DNS, TLS,
signature, relay advertised, same publisher key and a higher revision than `evidence/pre.env`), health, `/relay/v1/metrics` not public,
anything else 404, both lookup routes answering 400 to an empty body, and the websocket sending its challenge.

## If it goes wrong

- The old publisher key matters: the Mac and the server pinned it in earlier runs. If the one command started with a different key,
  `/connect selfhost.kollabor.ai` fails with `key_changed`. `serve_up.sh up` stops before touching the edge when the key differs.
- `bash m4/teardown.sh --restore-old` stops `m4-serve`, runs `~/kollab-m4/restore-old-stack.sh` (the three tmux sessions with the
  command lines `serve_up.sh` recorded in `evidence/old-stack.json`) and runs `edge_vhost.sh restore`.
- By hand: `ssh alzan-edge 'sudo cat /etc/nginx/m4-backups/LATEST'` names the vhost backup to copy back.

## Not covered on purpose

The Caddy snippet (no Caddy on the Mac; the nginx one is machine-checked and was driven through a real nginx locally), a proxy on another
address family, the multi-worker `kollab relay run` form, sealed config sync, mesh. The 80/120 column checks are m1's; this milestone adds no screen.
