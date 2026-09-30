# Shared settings for the m3 scripts. Sourced, never run. Everything else comes from m1/env.sh.
M3_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$M3_DIR/../m1/env.sh"          # M1_* settings, m1_ssh, say, die, m1_srv_paths, m1_project_dir

# C: a second device on the server, in its own workspace and tmux session, under its own hub identity.
M3_C_SESSION=${M3_C_SESSION:-m3-c}
M3_C_WS_NAME=${M3_C_WS_NAME:-kollab-m3-c}
M3_C_AS=${M3_C_AS:-peridot}
# Loopback ports of the TLS endpoints of B (the m1 server device) and C.
M3_B_PORT=${M3_B_PORT:-8801}
M3_C_PORT=${M3_C_PORT:-8802}
M3_DISCOVERY_PORT=39531

# Resolved on the server by m3_srv_paths (needs M1_SRV_HOME from m1_srv_paths).
m3_srv_paths() {
  m1_srv_paths
  M3_C_WS=$M1_SRV_HOME/$M3_C_WS_NAME
  M3_TLS=$M1_SRV_ROOT/m3-tls
}

# Shared by proof.sh and teardown.sh: stop a server workspace's processes / start a kollab in a tmux session.
stop_ws() { # stop_ws <session> <workspace on the server>: kill the window and every process whose cwd is the workspace
  m1_ssh bash -s -- "$1" "$2" <<'REMOTE'
session=$1; ws=$2
tmux kill-session -t "$session" 2>/dev/null || true
sleep 2
for _ in 1 2 3 4 5 6 7 8 9 10; do
  pids=""
  for d in /proc/[0-9]*; do
    [ "$(readlink "$d/cwd" 2>/dev/null)" = "$ws" ] && pids="$pids ${d#/proc/}"
  done
  [ -z "$pids" ] && { echo stopped; exit 0; }
  kill -TERM $pids 2>/dev/null || true
  sleep 1
done
kill -KILL $pids 2>/dev/null || true
sleep 1
echo killed
REMOTE
}
launch_srv() { # launch_srv <session> <workspace> [extra kollab flags]: the same launch line as m1/proof.sh
  m1_ssh "tmux kill-session -t $1 2>/dev/null; tmux new-session -d -s $1 -x 120 -y 40 \"cd '$2' && env KOLLAB_NO_KEYRING=1 '$M1_SRV_VENV/bin/kollab' ${M1_KOLLAB_FLAGS:-} ${3:-} --llm openai-oauth; exec zsh\""
}
