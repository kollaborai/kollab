# shellcheck shell=bash disable=SC2034
# Shared settings for the m4 scripts. Sourced, never run. Builds on m1/env.sh.
M4_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=../m1/env.sh
. "$M4_DIR/../m1/env.sh"

M4_DOMAIN=${M4_DOMAIN:-selfhost.kollabor.ai}
M4_EDGE_HOST=${M4_EDGE_HOST:-alzan-edge}
# The one command listens here on alzan-prod. The bind address and the trusted proxy come from the
# old selfhost relay config (serve_up.sh reads them); this is only the port. The old stack used 9177 (key file),
# 9178 and 9179 (relay workers) and 9180 (health). 9178 is a port the edge already reaches through the firewall;
# it is free once the old stack has stopped.
M4_PORT=${M4_PORT:-9178}
M4_SERVE_SESSION=${M4_SERVE_SESSION:-m4-serve}
M4_OLD_SESSIONS=(sh-relay sh-pub sh-static)

M4_MAC_SESSION=m4-mac
M4_SRV_SESSION=m4-srv
M4_MAC_WS=${M4_MAC_WS:-$HOME/kollab-m4-mac}
M4_SRV_WS_NAME=${M4_SRV_WS_NAME:-kollab-m4-server}
# Files this proof keeps on alzan-prod, next to the m1 tree.
m4_srv_paths() {
  m1_srv_paths
  M4_SRV_ROOT=$M1_SRV_HOME/kollab-m4
  M4_SRV_WS=$M1_SRV_HOME/$M4_SRV_WS_NAME
}

say() { printf '[m4 %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[m4 FATAL] %s\n' "$*" >&2; exit 1; }

# One multiplexed ssh connection to the edge; teardown closes it.
M4_EDGE_SSH_OPTS=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=/tmp/kollab-m4-edge-%C" -o ControlPersist=300)
m4_edge() { ssh "${M4_EDGE_SSH_OPTS[@]}" "$M4_EDGE_HOST" "$@"; }
