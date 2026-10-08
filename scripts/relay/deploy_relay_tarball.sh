#!/usr/bin/env bash
# Deploy an already-built relay tarball: deploy_relay_tarball.sh <tarball> <short-sha> [version]
# A version is recorded for autoupdate.sh once the relay is healthy.
set -euo pipefail
HOST=server
OUT="$1"
TAG="$(date +%Y%m%d)-$2"
REL="/home/me/.local/share/kollab-relay/releases/$TAG"
LOCAL_SHA=$(shasum -a 256 "$OUT" | cut -d' ' -f1)
echo "tag=$TAG sha256=$LOCAL_SHA"

ssh "$HOST" "mkdir -p '$REL'"
scp -q "$OUT" "$HOST:$REL/source.tar.gz"

ssh "$HOST" bash -s "$REL" "$LOCAL_SHA" "${3:-}" <<'EOF'
set -euo pipefail
REL=$1; WANT=$2; VERSION=$3
cd "$REL"
GOT=$(sha256sum source.tar.gz | cut -d' ' -f1)
[ "$GOT" = "$WANT" ] || { echo "tarball sha mismatch: $GOT"; exit 2; }
tar xzf source.tar.gz
python3 - <<'PY'
import hashlib, json, pathlib
manifest = json.load(open("source-sha256.json"))
for name, digest in manifest.items():
    got = hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()
    assert got == digest, f"{name}: {got} != {digest}"
print(f"manifest ok: {len(manifest)} files")
PY
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip >/dev/null
.venv/bin/pip install -q -r scripts/relay/requirements.txt
KOLLAB_NO_KEYRING=1 .venv/bin/python -c "import sys; sys.path.insert(0,'.'); import plugins.hub.relay_service, plugins.hub.relay_runtime; print('import ok')"

# The relay binds the private interface from runtime.json, not loopback.
HEALTH_URL=$(python3 -c "import json;c=json.load(open('/home/me/.config/kollab-relay/runtime.json'));print('http://%s:%s/relay/v1/health' % (c['bind_host'], c['health_port']))")
echo "health check: $HEALTH_URL"
DROPIN=/etc/systemd/system/kollab-relay.service.d/source-v2.conf
BACKUP="/home/me/.local/share/kollab-relay/dropin-backup-$(date +%Y%m%d-%H%M%S)"
sudo -n cp "$DROPIN" "$BACKUP"
printf '[Service]\nWorkingDirectory=%s\nExecStart=\nExecStart=%s/.venv/bin/python kollabor_cli_main.py relay run --config /home/me/.config/kollab-relay/runtime.json\n' "$REL" "$REL" | sudo -n tee "$DROPIN" >/dev/null
sudo -n systemctl daemon-reload
sudo -n systemctl restart kollab-relay.service
for i in $(seq 1 20); do
  sleep 1
  H=$(curl -s -m 3 "$HEALTH_URL" || true)
  case "$H" in *'"status": "ok"'*'"degraded": false'*) echo "healthy after ${i}s"; if [ -n "$VERSION" ]; then echo "$VERSION" > /home/me/.local/share/kollab-relay/version; fi; break;; esac
  [ "$i" = 20 ] && { echo "NOT HEALTHY: $H"; echo "rolling back to $BACKUP"; sudo -n cp "$BACKUP" "$DROPIN"; sudo -n systemctl daemon-reload; sudo -n systemctl restart kollab-relay.service; exit 3; }
done
echo "enrollment lookup: $(curl -s -m 8 -o /dev/null -w '%{http_code}' -X POST https://kollabor.ai/relay/v1/enrollment/lookup -H 'content-type: application/json' -d '{}')"
echo "contact routes:    $(curl -s -m 8 -o /dev/null -w '%{http_code}' -X POST https://kollabor.ai/relay/v1/contact/lookup -H 'content-type: application/json' -d '{}')  (404 = knocks-as-calls build, knocks ride the WebSocket; 400 = older build)"
echo "public health:     $(curl -s -m 8 https://kollabor.ai/relay/v1/health | head -c 120)"
echo "rollback: sudo cp $BACKUP $DROPIN && sudo systemctl daemon-reload && sudo systemctl restart kollab-relay.service"
EOF
