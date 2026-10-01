#!/usr/bin/env bash
# teardown.sh: stop ONLY the m1-mac / m1-srv tmux sessions and the processes
# started from the m1 venvs (~/kollab-m1/venv on each host). Leaves the other
# tmux sessions (e2e-mac, e2e-srv, sh-*), the uv tool install, workspaces,
# evidence and network state alone. Nothing is deleted.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"

# The bracketed first character keeps the pattern from matching this script's
# own command line.
stop_venv_procs() { # stop_venv_procs <venv-dir>
  local venv=$1 pat pids
  pat="[${venv:0:1}]${venv:1}/bin/"
  pids=$(pgrep -f -- "$pat" || true)
  [ -n "$pids" ] || { echo "no processes running from $venv"; return 0; }
  echo "TERM to: $(echo $pids)"
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 15); do
    pids=$(pgrep -f -- "$pat" || true)
    [ -z "$pids" ] && { echo "stopped"; return 0; }
    sleep 1
  done
  echo "KILL to: $(echo $pids)"
  # shellcheck disable=SC2086
  kill -KILL $pids 2>/dev/null || true
}

say "mac: tmux session $M1_MAC_SESSION"
if tmux kill-session -t "$M1_MAC_SESSION" 2>/dev/null; then say "mac: killed $M1_MAC_SESSION"; else say "mac: no session $M1_MAC_SESSION"; fi
sleep 2
say "mac: processes from $M1_MAC_VENV"
stop_venv_procs "$M1_MAC_VENV"

if m1_ssh true 2>/dev/null; then
  m1_srv_paths
  say "srv: tmux session $M1_SRV_SESSION and processes from $M1_SRV_VENV"
  m1_ssh bash -s -- "$M1_SRV_VENV" "$M1_SRV_SESSION" <<'REMOTE'
venv=$1; session=$2
if tmux kill-session -t "$session" 2>/dev/null; then echo "killed tmux session $session"; else echo "no tmux session $session"; fi
sleep 2
pat="[${venv:0:1}]${venv:1}/bin/"
pids=$(pgrep -f -- "$pat" || true)
if [ -z "$pids" ]; then echo "no processes running from $venv"; exit 0; fi
echo "TERM to: $(echo $pids)"
kill -TERM $pids 2>/dev/null || true
for _ in $(seq 1 15); do
  pids=$(pgrep -f -- "$pat" || true)
  [ -z "$pids" ] && { echo "stopped"; exit 0; }
  sleep 1
done
echo "KILL to: $(echo $pids)"
kill -KILL $pids 2>/dev/null || true
REMOTE
  ssh "${M1_SSH_OPTS[@]}" -O exit "$M1_HOST" 2>/dev/null || true
else
  say "srv: cannot reach $M1_HOST; nothing done there"
fi
say "teardown done"
