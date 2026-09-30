#!/usr/bin/env bash
# teardown.sh [--restore-old]
#
# default        stop the two proof TUIs (tmux m4-mac, m4-srv) and the kollab processes they started from the
#                m1 venvs. The one command (tmux m4-serve, `kollab relay serve`) and the edge stay as they are:
#                that is the new steady state of selfhost.kollabor.ai.
# --restore-old  put the manual stack back: stop m4-serve (Ctrl-C), run ~/kollab-m4/restore-old-stack.sh on
#                alzan-prod (tmux sh-relay, sh-pub, sh-static as serve_up.sh recorded them), then
#                edge_vhost.sh restore so the edge points at the old ports again.
#
# Nothing is deleted: not the workspaces, evidence, state directories, the backup or the restore script.
set -euo pipefail
# shellcheck source=env.sh
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
RESTORE=0
case "${1:-}" in "") ;; --restore-old) RESTORE=1 ;; *) die "usage: teardown.sh [--restore-old]" ;; esac

# Like m1/teardown.sh, but never the one command: it runs from the same venv.
# The bracketed first character keeps the pattern from matching this script's own command line.
stop_venv_procs() { # stop_venv_procs <venv-dir>
  local venv=$1 pat pids keep
  pat="[${venv:0:1}]${venv:1}/bin/"
  pids=$(pgrep -f -- "$pat" || true)
  keep=$(pgrep -f -- '[r]elay serve --domain' || true)
  pids=$(comm -23 <(sort <<<"$pids") <(sort <<<"$keep") | tr '\n' ' ')
  [ -n "${pids// /}" ] || { echo "no processes running from $venv"; return 0; }
  echo "TERM to: $pids"
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 15); do
    sleep 1
    still=""
    for p in $pids; do kill -0 "$p" 2>/dev/null && still="$still $p"; done
    [ -z "$still" ] && { echo "stopped"; return 0; }
  done
  echo "KILL to:$still"
  # shellcheck disable=SC2086
  kill -KILL $still 2>/dev/null || true
}

say "mac: tmux session $M4_MAC_SESSION"
if tmux kill-session -t "$M4_MAC_SESSION" 2>/dev/null; then say "mac: killed $M4_MAC_SESSION"; else say "mac: no session $M4_MAC_SESSION"; fi
sleep 2
say "mac: processes from $M1_MAC_VENV (not the one command)"
stop_venv_procs "$M1_MAC_VENV"

if m1_ssh true 2>/dev/null; then
  m4_srv_paths
  say "srv: tmux session $M4_SRV_SESSION and processes from $M1_SRV_VENV (not the one command)"
  m1_ssh bash -s -- "$M1_SRV_VENV" "$M4_SRV_SESSION" <<'REMOTE'
venv=$1; session=$2
if tmux kill-session -t "$session" 2>/dev/null; then echo "killed tmux session $session"; else echo "no tmux session $session"; fi
sleep 2
pat="[${venv:0:1}]${venv:1}/bin/"
pids=$(pgrep -f -- "$pat" || true)
keep=$(pgrep -f -- '[r]elay serve --domain' || true)
pids=$(comm -23 <(sort <<<"$pids") <(sort <<<"$keep") | tr '\n' ' ')
if [ -z "${pids// /}" ]; then echo "no processes running from $venv"; exit 0; fi
echo "TERM to: $pids"
kill -TERM $pids 2>/dev/null || true
for _ in $(seq 1 15); do
  sleep 1
  still=""
  for p in $pids; do kill -0 "$p" 2>/dev/null && still="$still $p"; done
  [ -z "$still" ] && { echo "stopped"; exit 0; }
done
echo "KILL to:$still"
kill -KILL $still 2>/dev/null || true
REMOTE
else
  say "srv: cannot reach $M1_HOST; nothing done there"
fi

if [ "$RESTORE" = 1 ]; then
  m4_srv_paths
  say "restore: stopping the one command in tmux $M4_SERVE_SESSION"
  m1_ssh "tmux send-keys -t $M4_SERVE_SESSION C-c 2>/dev/null; true" </dev/null
  for _ in $(seq 1 30); do
    sleep 2
    m1_ssh "pgrep -f '[r]elay serve --domain $M4_DOMAIN' >/dev/null" </dev/null || break
  done
  m1_ssh "tmux kill-session -t $M4_SERVE_SESSION 2>/dev/null; true" </dev/null
  say "restore: bringing back the manual stack from $M4_SRV_ROOT/restore-old-stack.sh"
  m1_ssh "bash '$M4_SRV_ROOT/restore-old-stack.sh'" </dev/null || die "restore-old-stack.sh failed; start the three processes by hand from evidence/old-stack.json"
  say "restore: pointing the edge back at the old ports"
  bash "$M4_DIR/edge_vhost.sh" restore
fi

ssh "${M1_SSH_OPTS[@]}" -O exit "$M1_HOST" 2>/dev/null || true
ssh "${M4_EDGE_SSH_OPTS[@]}" -O exit "$M4_EDGE_HOST" 2>/dev/null || true
say "teardown done"
