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
