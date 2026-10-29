#!/bin/sh
# seed_home.sh <home> <device-name>: a fresh machine HOME that trusts the proof CA
# and may dial the private docker subnet. No model, no keyring.
set -eu
H="$1"; NAME="$2"
mkdir -p "$H/.kollab" "$H/work"
touch "$H/.kollab/connect-guide-seen"
cat > "$H/.kollab/config.json" <<EOF
{
  "application": {"setup_completed": true},
  "kollabor": {"updates": {"auto_update_enabled": false}},
  "plugins": {"hub": {
    "endpoint_tls_ca": "/certs/ca.pem",
    "discovery_private_origins": {"*": ["10.231.77.0/24"]},
    "relay_device_name": "$NAME"
  }}
}
EOF
