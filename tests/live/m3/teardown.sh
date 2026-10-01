#!/usr/bin/env bash
# teardown.sh: undo what m3/proof.sh added, then leave the m1 sessions to m1/teardown.sh.
# Stops C (its window and any process whose cwd is C's workspace), removes C's workspace and the
# loopback certificate, puts B's workspace config back and restarts B on it (B would otherwise keep
# its loopback endpoint bound and proof.sh could not run again: its preflight wants those ports free).
# C's device stays on A's network like B does; leaving is not part of this proof.
set -uo pipefail
M3_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$M3_DIR/env.sh"

m1_ssh true 2>/dev/null || die "cannot reach $M1_HOST"
m3_srv_paths
say "srv: stopping C ($M3_C_SESSION) and every process in $M3_C_WS"
m1_ssh bash -s -- "$M3_C_SESSION" "$M3_C_WS" <<'REMOTE'
session=$1; ws=$2
tmux kill-session -t "$session" 2>/dev/null && echo "killed tmux session $session" || echo "no tmux session $session"
sleep 2
for _ in 1 2 3 4 5 6 7 8 9 10; do
  pids=""
  for d in /proc/[0-9]*; do
    [ "$(readlink "$d/cwd" 2>/dev/null)" = "$ws" ] && pids="$pids ${d#/proc/}"
  done
  [ -z "$pids" ] && { echo "no process left in $ws"; exit 0; }
  echo "TERM to:$pids"
  kill -TERM $pids 2>/dev/null || true
  sleep 1
done
echo "KILL to:$pids"
kill -KILL $pids 2>/dev/null || true
REMOTE

say "srv: removing $M3_C_WS and $M3_TLS, restoring B's workspace config"
m1_ssh "python3 '$M1_SRV_ROOT/bin/m3probe.py' unconfig '$M1_SRV_WS' 2>&1 || true"
m1_ssh "rm -rf -- '$M3_C_WS' '$M3_TLS'"
# C starts every run as a stranger: drop its network state (sha256 of the
# workspace path) and its project dir.
C_PROJECT=$(m1_project_dir "$M3_C_WS" "$M1_SRV_HOME")
m1_ssh "d=\$(printf %s '$M3_C_WS' | sha256sum | cut -d' ' -f1); rm -rf -- \"\$HOME/.kollab/network/\$d\" '$C_PROJECT'"
say "srv: restarting B ($M1_SRV_SESSION) on its restored config"
stop_ws "$M1_SRV_SESSION" "$M1_SRV_WS" >/dev/null
launch_srv "$M1_SRV_SESSION" "$M1_SRV_WS"
say "left in place on purpose: m3/evidence/ and C's hub vault"
say "teardown done; run m1/teardown.sh next"
