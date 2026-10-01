#!/usr/bin/env bash
# teardown.sh: stop ONLY the s7-* tmux sessions and the processes started from the kollab-s7
# venv, on both hosts. Leaves every other session, the real installs, workspaces, evidence and
# network state alone. Nothing is deleted. It closes only its own ssh master (/tmp/kollab-s7-*),
# never the shared m1 one.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../m1/env.sh"
M1_SSH_OPTS=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=/tmp/kollab-s7-%C" -o ControlPersist=300)

stop_venv_procs() { # stop_venv_procs <venv-dir>; the bracket keeps pgrep from matching itself
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

if tmux kill-session -t s7-mac 2>/dev/null; then say "mac: killed s7-mac"; else say "mac: no session s7-mac"; fi
sleep 2
say "mac: processes from ~/kollab-s7/venv"; stop_venv_procs "$HOME/kollab-s7/venv"

if m1_ssh true 2>/dev/null; then
  SRV_HOME=$(m1_ssh 'printf %s "$HOME"')
  say "srv: session s7-srv and processes from ~/kollab-s7/venv"
  m1_ssh bash -s -- "$SRV_HOME/kollab-s7/venv" <<'REMOTE'
if tmux kill-session -t s7-srv 2>/dev/null; then echo "killed tmux session s7-srv"; else echo "no tmux session s7-srv"; fi
sleep 2
venv=$1
pat="[${venv:0:1}]${venv:1}/bin/"
pids=$(pgrep -f -- "$pat" || true)
if [ -z "$pids" ]; then echo "no processes running from $venv"; exit 0; fi
echo "TERM to: $(echo $pids)"
kill -TERM $pids 2>/dev/null || true
for _ in $(seq 1 15); do
  pids=$(pgrep -f -- "$pat" || true)
  [ -z "$pids" ] && break
  sleep 1
done
if [ -n "$pids" ]; then echo "KILL to: $(echo $pids)"; kill -KILL $pids 2>/dev/null || true; else echo "stopped"; fi
REMOTE
  ssh "${M1_SSH_OPTS[@]}" -O exit "$M1_HOST" 2>/dev/null || true
else
  say "srv: cannot reach $M1_HOST; nothing done there"
fi
say "teardown done"
