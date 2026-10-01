#!/usr/bin/env bash
# teardown.sh: stop ONLY the gs-* tmux sessions and the processes started from the
# kollab-gs and kollab-gs-pypi venvs, on both hosts. Leaves every other session, the
# real installs, workspaces, evidence and network state alone. Nothing is deleted.
# It closes only its own ssh master (/tmp/kollab-gs-*), never the shared m1 one.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../m1/env.sh"
M1_SSH_OPTS=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=/tmp/kollab-gs-%C" -o ControlPersist=300)

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

for s in gs-mac gs-pypi-mac; do
  if tmux kill-session -t "$s" 2>/dev/null; then say "mac: killed $s"; else say "mac: no session $s"; fi
done
sleep 2
for root in kollab-gs kollab-gs-pypi; do say "mac: processes from ~/$root/venv"; stop_venv_procs "$HOME/$root/venv"; done

if m1_ssh true 2>/dev/null; then
  SRV_HOME=$(m1_ssh 'printf %s "$HOME"')
  say "srv: sessions gs-srv, gs-pypi-srv and processes from ~/kollab-gs/venv, ~/kollab-gs-pypi/venv"
  m1_ssh bash -s -- "$SRV_HOME/kollab-gs/venv" "$SRV_HOME/kollab-gs-pypi/venv" <<'REMOTE'
for s in gs-srv gs-pypi-srv; do
  if tmux kill-session -t "$s" 2>/dev/null; then echo "killed tmux session $s"; else echo "no tmux session $s"; fi
done
sleep 2
for venv in "$@"; do
  pat="[${venv:0:1}]${venv:1}/bin/"
  pids=$(pgrep -f -- "$pat" || true)
  if [ -z "$pids" ]; then echo "no processes running from $venv"; continue; fi
  echo "TERM to: $(echo $pids)"
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 15); do
    pids=$(pgrep -f -- "$pat" || true)
    [ -z "$pids" ] && break
    sleep 1
  done
  if [ -n "$pids" ]; then echo "KILL to: $(echo $pids)"; kill -KILL $pids 2>/dev/null || true; else echo "stopped"; fi
done
REMOTE
  ssh "${M1_SSH_OPTS[@]}" -O exit "$M1_HOST" 2>/dev/null || true
else
  say "srv: cannot reach $M1_HOST; nothing done there"
fi
say "teardown done"
