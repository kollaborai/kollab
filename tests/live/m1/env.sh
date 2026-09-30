# Shared settings for the m1 scripts. Sourced, never run.
M1_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
M1_VERSION=${M1_VERSION:-0.11.0.dev1}
M1_REPO=${M1_REPO:-/Users/malmazan/dev/kollab}
M1_HOST=${M1_HOST:-alzan-prod}
M1_EXPECT_WHEELS=11

M1_MAC_ROOT=$HOME/kollab-m1
M1_MAC_VENV=$M1_MAC_ROOT/venv
M1_MAC_WS=${M1_MAC_WS:-$HOME/kollab-m1-mac}
M1_MAC_SESSION=${M1_MAC_SESSION:-m1-mac}
M1_SRV_SESSION=${M1_SRV_SESSION:-m1-srv}
# Remote paths are resolved against the remote $HOME by m1_srv_paths.
M1_SRV_WS_NAME=${M1_SRV_WS_NAME:-kollab-m1-server}

# One multiplexed ssh connection; teardown closes it.
M1_SSH_OPTS=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=/tmp/kollab-m1-%C" -o ControlPersist=300)
m1_ssh() { ssh "${M1_SSH_OPTS[@]}" "$M1_HOST" "$@"; }

say() { printf '[m1 %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[m1 FATAL] %s\n' "$*" >&2; exit 1; }

m1_srv_paths() {
  M1_SRV_HOME=$(m1_ssh 'printf %s "$HOME"') || die "cannot ssh to $M1_HOST"
  M1_SRV_ROOT=$M1_SRV_HOME/kollab-m1
  M1_SRV_VENV=$M1_SRV_ROOT/venv
  M1_SRV_WS=$M1_SRV_HOME/$M1_SRV_WS_NAME
}

# kollab keeps per-workspace state under ~/.kollab/projects/<path, '/' -> '_'>.
m1_project_dir() { local p=${1#/}; printf '%s/.kollab/projects/%s' "$2" "${p//\//_}"; }

# Interpreter for the Mac venv: kollab needs >= 3.12; the uv-managed 3.12 is
# what earlier acceptance runs used.
m1_pick_python() {
  local c
  for c in "${M1_PY_MAC:-}" "$HOME"/.local/share/uv/python/cpython-3.12*/bin/python3 python3.12 python3.13 python3; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null && { command -v "$c"; return 0; }
  done
  return 1
}
