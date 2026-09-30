# m4 script review (#121)
Verdict: SAFE TO RUN after 5409e45, with the risks below. Nothing was run against a real host.
Scope: matches key on the config origin https://selfhost.kollabor.ai, the tmux names sh-relay/sh-pub/sh-static/m4-*, and the vhost's server_name. kollabor.ai, kollab-relay.service and release dirs are never named. All scripts use set -euo pipefail.

Fixed in 5409e45 (tests/live/m4 only):
- M4_DOMAIN could aim everything at kollabor.ai: env.sh now dies unless it is selfhost.kollabor.ai.
- repoint_vhost.py rewrote every upstream in a file: it now refuses a file whose server_name lines are not exactly the domain.
- up stopped the old stack before checking the edge: new `edge_vhost.sh check` (sudo -n, nginx -t, one vhost, rewrite accepted) runs first.
- Rollback said "backup is back" even when the put-back failed, restore could replay a stale backup, a second apply saved the changed vhost as the original. Now truthful, LATEST retired after restore, an unrestored apply blocks the next one.
- After a failed up, the message said to run restore-old-stack.sh by hand (m4-serve still holds the port). An EXIT trap now says `teardown.sh --restore-old`, which waits until the one command is dead, restores stack then edge even if one fails, waits for public health, and exits non-zero naming failures.
- restore-old-stack.sh died on the first existing session and verified nothing: it now skips running processes, restarts dead ones, exits 1 if one is not up after 3 s. sh-* kills match exactly.
- old-stack.json was overwritten by a second run, pre.env was sourced unquoted from a fetched manifest, keys and relay: addresses were printed, and bash -x would print the join code. All fixed.
- Default teardown on alzan-prod spares relay run / discovery_publish (a restored manual stack may live in the m1 venv).

Checked: shellcheck clean, existing unit test green, 26 offline checks (fake ssh/sudo/nginx/curl for apply/restore/rollback, isolated tmux for the restore script). The harness is not committed.

Remaining risk:
- restore replays argv and cwd only, not the original shells' environment (venv, relative python3); the 3 s check catches a crash, not a wrong config.
- An upstream name shared with another vhost file is not detected (same-file is). `systemctl reload nginx` reloads every vhost, so unrelated pending edits go live too (nginx -t guards syntax only).
- The domain is down from up to apply; up does not roll back by itself, it prints the way back.
- serve_up.sh up and teardown --restore-old were read and shellchecked, never executed; README does not yet mention check or LATEST.used.
