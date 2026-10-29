#!/bin/sh
# ks.sh: helpers run INSIDE a machine container (never on the host).
#   ks.sh daemon [folder]            start `kollab --detached` in folder (default ~/work)
#   ks.sh tui [folder]               tmux window `ks` with an interactive kollab attached
#   ks.sh type "<text>"              type a line into that window and press Enter
#   ks.sh screen [lines]             print the window
#   ks.sh proxy <op> [json-args]     connect_proxy op on the folder's daemon hub socket
#   ks.sh procs                      every kollab process (pid, pgid, args)
set -eu
export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-proof-not-real}" KOLLAB_NO_KEYRING=1 TERM=xterm-256color
F="${KS_FOLDER:-$HOME/work}"
case "$1" in
  daemon) cd "${2:-$F}" && kollab --detached >/dev/null 2>&1; sleep 6 ;;
  tui) cd "${2:-$F}" && tmux new-session -d -s ks -x 160 -y 50 "kollab"; sleep 8 ;;
  type) tmux send-keys -t ks -l "$2"; sleep 1; tmux send-keys -t ks Enter; sleep "${3:-4}" ;;
  screen) tmux capture-pane -t ks -p | grep -v '^\s*$' | tail -n "${2:-30}" ;;
  proxy) shift; cd "$F" && python /harness/proxy.py "$@" ;;
  procs) ps -eo pid,pgid,args | grep -E 'kollab|kollabor' | grep -v grep || true ;;
esac
