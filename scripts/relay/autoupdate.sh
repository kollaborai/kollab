#!/usr/bin/env bash
# Keep a relay on the newest kollab release on PyPI. kollab-relay-update.timer runs it.
#
# Each run: when PyPI's newest kollab is newer than the one this relay runs, install it
# into a new release directory beside the running one, point the kollab-relay drop-in
# at it, restart, and keep it once health is ok. If health is not ok within 20 s, the
# old drop-in goes back and that version is never tried again. Devices that have not
# updated keep working: within a protocol version both sides ignore what they do not
# know, and a breaking change is a new version served beside the old one
# (docs/specs/agent-public-beacon.md#versioning).
#
# usage: autoupdate.sh [--check]   (--check prints the decision and changes nothing)
#
# Environment, defaults matching scripts/relay/deploy_relay.sh:
#   RELAY_HOME    ~/.local/share/kollab-relay: releases/, version, failed, update.lock
#   RELAY_CONFIG  ~/.config/kollab-relay/runtime.json
#   DROPIN        /etc/systemd/system/kollab-relay.service.d/source-v2.conf
#   SERVICE       kollab-relay.service
#   PYTHON        python3 (the interpreter each release's venv is built from)
#
# Install (as the user the relay runs as; it needs passwordless `sudo -n` for cp, tee
# and systemctl, like deploy_relay.sh):
#   cp scripts/relay/autoupdate.sh ~/.local/share/kollab-relay/autoupdate.sh
#   echo 0.13.0 > ~/.local/share/kollab-relay/version     # the version running now
#   sudo cp kollab-relay-update.service.example /etc/systemd/system/kollab-relay-update.service   # set User=
#   sudo cp kollab-relay-update.timer.example /etc/systemd/system/kollab-relay-update.timer
#   sudo systemctl enable --now kollab-relay-update.timer
# Log: journalctl -u kollab-relay-update
set -euo pipefail

RELAY_HOME=${RELAY_HOME:-$HOME/.local/share/kollab-relay}
RELAY_CONFIG=${RELAY_CONFIG:-$HOME/.config/kollab-relay/runtime.json}
DROPIN=${DROPIN:-/etc/systemd/system/kollab-relay.service.d/source-v2.conf}
SERVICE=${SERVICE:-kollab-relay.service}
PYTHON=${PYTHON:-python3}

exec 9>"$RELAY_HOME/update.lock"
flock -n 9 || exit 0  # a run is already in progress

current=$(cat "$RELAY_HOME/version" 2>/dev/null || true)
latest=$(curl -fsS -m 20 https://pypi.org/pypi/kollab/json \
  | "$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["info"]["version"])')
decision=$("$PYTHON" - "$current" "$latest" <<'PY'
import re, sys

def release(text, *, final):
    """(0, 13, 0). PyPI's newest must be a final release; the running one only needs
    its X.Y.Z (a hand-deployed 0.14.0rc1 or 0.13.0+abc is not older than 0.13.0)."""
    found = (re.fullmatch if final else re.match)(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(map(int, found.groups())) if found else None

current, latest = release(sys.argv[1], final=False), release(sys.argv[2], final=True)
print("update" if latest and (current is None or latest > current) else "current")
PY
)
if grep -qx "$latest" "$RELAY_HOME/failed" 2>/dev/null; then
  decision=failed
fi
echo "relay runs ${current:-unknown}; PyPI has $latest: $decision"
[ "${1:-}" = --check ] && exit 0
[ "$decision" = update ] || exit 0

REL="$RELAY_HOME/releases/$(date +%Y%m%d-%H%M%S)-pypi-$latest"
mkdir -p "$REL"
"$PYTHON" -m venv "$REL/.venv"
# The JSON API above can lead the index pip installs from by ~10 minutes after a release.
for i in $(seq 1 40); do
  "$REL/.venv/bin/pip" install -q --disable-pip-version-check --no-cache-dir "kollab==$latest" 2>"$REL/pip.err" && break
  if [ "$i" = 40 ]; then
    cat "$REL/pip.err"
    echo "kollab==$latest is not installable after 10 min; nothing changed, the next run tries again"
    rm -r "$REL"
    exit 1
  fi
  sleep 15
done
(cd "$REL" && KOLLAB_NO_KEYRING=1 .venv/bin/python -c "import plugins.hub.relay_service, plugins.hub.relay_runtime")

HEALTH_URL=$("$PYTHON" -c "import json, sys; c = json.load(open(sys.argv[1])); print('http://%s:%s/relay/v1/health' % (c['bind_host'], c['health_port']))" "$RELAY_CONFIG")
healthy() {
  case "$(curl -s -m 3 "$HEALTH_URL" || true)" in *'"status": "ok"'*'"degraded": false'*) return 0 ;; esac
  return 1
}

BACKUP="$RELAY_HOME/dropin-backup-$(date +%Y%m%d-%H%M%S)"
sudo -n cp "$DROPIN" "$BACKUP"
printf '[Service]\nWorkingDirectory=%s\nExecStart=\nExecStart=%s/.venv/bin/kollab relay run --config %s\n' \
  "$REL" "$REL" "$RELAY_CONFIG" | sudo -n tee "$DROPIN" >/dev/null
sudo -n systemctl daemon-reload
sudo -n systemctl restart "$SERVICE"
for i in $(seq 1 20); do
  sleep 1
  if healthy; then
    echo "$latest" > "$RELAY_HOME/version"
    echo "relay updated to $latest, healthy after ${i}s (rollback: sudo cp $BACKUP $DROPIN)"
    exit 0
  fi
done

echo "relay $latest was not healthy after 20s; putting $current back"
sudo -n cp "$BACKUP" "$DROPIN"
sudo -n systemctl daemon-reload
sudo -n systemctl restart "$SERVICE"
echo "$latest" >> "$RELAY_HOME/failed"
exit 1
