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
# Hard pin. These scripts stop processes and rewrite an nginx vhost for this one name; an exported M4_DOMAIN must never
# aim them at kollabor.ai (its vhost, kollab-relay.service, its release dirs).
[ "$M4_DOMAIN" = selfhost.kollabor.ai ] || die "M4_DOMAIN must be selfhost.kollabor.ai, got '$M4_DOMAIN'. These scripts never touch kollabor.ai."
# stdin -> stdout for anything printed to a terminal: a 64-hex key keeps 8 characters, relay: addresses are hidden.
scrub() { sed -E 's/([0-9a-f]{8})[0-9a-f]{56}/\1.../g; s/relay:[^[:space:]|]+/relay:<addr>/g'; }

# alzan-edge takes ssh only from alzan-prod (WireGuard, port 2222, alzan-prod's own key; see
# the alzan-edge entry in ~/.ssh/config), so every edge command hops through alzan-prod. The
# callers pass one command string; %q carries it through alzan-prod's shell unchanged, and
# stdin passes through both hops. One multiplexed connection to alzan-prod; teardown closes it.
M4_EDGE_VIA=${M4_EDGE_VIA:-alzan-prod}
M4_EDGE_TARGET=${M4_EDGE_TARGET:-deploy@10.0.0.1}
M4_EDGE_SSH_OPTS=(-o BatchMode=yes -o ControlMaster=auto -o "ControlPath=/tmp/kollab-m4-edge-%C" -o ControlPersist=300)
m4_edge() { ssh "${M4_EDGE_SSH_OPTS[@]}" "$M4_EDGE_VIA" "ssh -p 2222 -o BatchMode=yes -o ConnectTimeout=10 $M4_EDGE_TARGET $(printf '%q' "$*")"; }
