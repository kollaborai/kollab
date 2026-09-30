# m4 self-host: report

Branch: worktree-agent-a19e7801aee487817 (worktree /Users/malmazan/dev/kollab/.claude/worktrees/agent-a19e7801aee487817)
Started at 1111cbd (fast-forwarded to issue-121-network-simple-flow). Tip: fd1c0ea. Not pushed, no PR/issue.

Commits (all end ", refs #121", no attribution):
- 9085b10 Add kollab relay serve --domain (module, CLI dispatch, allowlist, unit tests, tmux spec)
- 694dd95 Docs: constitution (section 4 line, Story 6, section 11, section 12 milestone 4), ops guide, connect guide, README, commands ref, beacon contract, relay-change-guide, CHANGELOG + kollabor/updates/CHANGELOG (cmp: identical)
- 82ff43d tests/live/m4 live proof tooling
- 81cf1fd renewal loop survives any failed write; InMemoryBackend docstring
- 6ddf5e2 live proof: port 9178, edge reachability check, tests for the vhost rewrite + old-stack finder
- a8a79ad doc wording
- fd1c0ea live proof: two log windows so the deliberate restart cannot fail the clean-transcript check

## What the command does
`kollab relay serve --domain agents.example.com`
- One process: relay (create_app, one worker, in-memory backend), signed discovery publisher (publish(), renewed every 60 s, names the relay only while ready), and the key file (GET /.well-known/agent-keys.json, plus the alias /.well-known/agent-keys) on one local port (default 127.0.0.1:9078; --bind, --port).
- State under ~/.kollab/relay/<domain> (--state-dir): service.key 0600, publisher.json (revision), serve.lock, public/agent-keys.json. Dir must be 0700 and yours. One process per dir. A standalone publisher's state dir is adopted as is (same key, revision continues). Refuses a lost key and a dir of another domain.
- Prints: state/key created-or-loaded, listen address, trusted proxies, the TXT record value, the five proxy routes, paste-ready pointers; prints "ready: relay up, key file published (revision N)" only after the port is bound and the healthy publish succeeded. No instructions are printed if the bind fails.
- --print nginx|caddy|systemd prints config for the same settings and exits, creates nothing.
- Other flags: --trusted-proxy IP (default 127.0.0.1 and ::1 when bound to loopback, none otherwise), --max-connections-per-source, --max-connections-per-room.
- `kollab relay run --config` untouched. `kollab relay serve --origin ...` still dispatches to the bare worker (kollabor_cli_main.py). No change to relay_service.py or the wire contract.

## Decisions
- systemd: printed with --print systemd, never written. Installing a unit needs root and is host-specific; existing repo practice (scripts/relay/kollab-relay.service.example, deploy_relay*.sh) is operator-installed units. The unit runs as the current user on the SAME state dir (an earlier draft used /var/lib; that would have silently changed the published identity when moving from shell to systemd).
- In-memory single worker, not the RelayRuntime supervisor: the supervisor's managed backend needs Docker (Valkey). The managed Valkey keeps no data on disk (save "", tmpfs), so durability is identical: a restart ends pending join codes (5 min) and knocks (24 h); presence rebuilds. More workers/hosts: relay run --config.
- Default trust of X-Real-IP from loopback when bound to loopback (otherwise every client shares the proxy's per-address limit of 16). The printed nginx/Caddy configs overwrite X-Real-IP. Non-loopback bind trusts nothing until --trusted-proxy.
- "key file" = the served signed /.well-known/agent-keys.json (same document as "manifest").

## Tests
- tests/unit/test_relay_selfhost.py: 33 passed. tests/unit/test_live_m4_tools.py: 6 passed. test_relay_build_allowlist.py: 1 passed (new module is in the packaged allowlist and imports from it alone). Targeted run of these + test_relay_runtime + test_hub_discovery: 84 passed.
- Full tests/unit/ -q ran once at 9085b10-era code: 4764 passed, 7 skipped, 81 s. NOT re-run after the last +8 tests and the later small edits (told to stop); the later edits touch the new module's error handling, a docstring, tests/live and docs only.
- ruff check clean on every touched .py (relay_selfhost.py, relay_backend.py, kollabor_cli_main.py, build_service.py, the 3 test files, tests/live/m4/*.py). shellcheck -S warning clean on tests/live/m4/*.sh.
- tmux spec tests/tmux/specs/relay_selfhost.json: PASS, 12/12 assertions (first run creates key + prints TXT + five routes + --print pointers, key file verifies over HTTP, health, enrollment route live, Ctrl-C, restart loads the same key, no traceback).
- nginx: the printed snippet passes `nginx -t`, and a real local nginx in front of the running command served all five routes (key file signature ok, health ok, enrollment/contact POST -> relay 400, GET on enrollment 403, /relay/v1/metrics 404, anything else 404, websocket upgrade + relay challenge frame).

## Smoke test (real entry point, throwaway HOME, loopback domain and free ports, killed after)
python kollabor_cli_main.py relay serve --domain localhost:55284 --port 55285
- banner as in the constitution Story 6 (state created, listen, TXT `_agent.localhost TXT "v=aid1;u=https://localhost:55284/.well-known/agent-keys.json"`, five routes), then `ready: relay up, key file published (revision 2)`
- key file: HTTP 200, application/json, Cache-Control no-store, bytes == public/agent-keys.json, verify_manifest ok, roles [rendezvous, relay], control https://localhost:55284/relay/v1
- health 200 {"status": "ok", "protocol": "kollab-relay/1", "origin": "https://localhost:55284", ...}; alias route 200
- state dir mode 0700, service.key 0600; SIGTERM -> exit 0
- restart: "signing key loaded", same publisher key, revision 2 -> 4, exit 0. No processes left.

## Live proof tooling added (tests/live/m4/, NOT run; README.md there has the full procedure and rollback)
Order: m1/build_wheels.sh + m1/install_both.sh, then
1. serve_up.sh plan|up: finds the manual selfhost stack by config (inspect_old.py: relay run --config with origin https://selfhost.kollabor.ai, discovery_publish --origin, http.server on the publisher output dir), records evidence/old-stack.json + pre-manifest.json/pre.env, writes ~/kollab-m4/restore-old-stack.sh, checks the publisher state dir is 700 / service.key 600, stops the three (SIGTERM, supervisor first), starts tmux m4-serve running `kollab relay serve --domain selfhost.kollabor.ai --state-dir <publisher state dir> --bind <old bind, 10.0.0.5> --port 9178 --trusted-proxy <old trusted proxy or 10.0.0.1>` from ~/kollab-m1/venv, and requires the same publisher key as before.
2. edge_vhost.sh show|apply|restore: rewrites the alzan-edge vhost (repoint_vhost.py: upstream servers collapse to one, health and key-file proxy_pass point at the one port; refuses anything it does not understand), checks the edge can curl the port first, backs up to /etc/nginx/m4-backups, nginx -t, reload, public health check, automatic put-back on failure. Diff verified against the saved selfhost vhost.
3. verify_serve.sh: DNS TXT, discovery via the installed client code (DNS + TLS + signature, relay advertised, same key and higher revision than pre.env), health, metrics not public, / 404, lookup routes 400, GET on enrollment refused, websocket challenge.
4. proof.sh (Story 6): Mac `/connect selfhost.kollabor.ai`; Mac Connect screen shows a code; server `/connect`, Tab, clears the prefilled domain, types selfhost.kollabor.ai, Tab, types the code (masked), Enter; Mac sees "wants to join", presses a; server "joined ... trust:"; both list agent@device; `kollab --hub msg agent@device` reply + server shell ran; no bare kollabor.ai in statuses/logs; Ctrl-C the one command, restart via ~/kollab-m4/serve-cmd.sh, same key + higher revision, both devices answer another message; pane and log leak/error scans (strict window to the first message, traceback-and-leak-only window across the restart).
5. teardown.sh [--restore-old]: stops the m4 TUIs (never the one command); --restore-old stops m4-serve, runs restore-old-stack.sh, then edge_vhost.sh restore.
Also: env.sh (sources m1/env.sh), .gitignore (evidence/).

## Not done / unverified
- Nothing in tests/live/m4 has been run against alzan-prod / alzan-edge / selfhost.kollabor.ai (told not to). The bash is shellchecked and the Python helpers are unit-tested; the ssh, tmux and edge paths are untested. Look first at: inspect_old.py process matching on the real host; the firewall path for the port (defaults to the old worker port 9178; edge_vhost.sh apply refuses if the edge cannot curl it); the private-form key sequence in proof.sh (Tab, 30x BSpace, type domain, Tab, type code), not yet driven against the real form.
- Caddy snippet is not machine-checked (no Caddy here).
- Story 6's sentence "joined devices reconnect on their own after a restart" is target text until the live proof runs.
- Full tests/unit/ not re-run after the final small edits (see above).
- Enrollment rate limit (10 calls/min per address per route) and the 16/source, 16/room defaults are unchanged; an office behind one address needs --max-connections-per-source.

## Exact next step
1. In this worktree run `/Users/malmazan/dev/kollab/.venv/bin/python -m pytest tests/unit/ -q` once (expect about 4772 passed, 7 skipped) and merge worktree-agent-a19e7801aee487817 into the milestone branch (possible doc conflicts with m2/m3: CHANGELOG [Unreleased], constitution sections 4/5/11/12, connect.md).
2. Build wheels from the merged ref, install on both hosts, then run tests/live/m4 in the README order, starting with `bash tests/live/m4/serve_up.sh plan`.
