#!/usr/bin/env bash
# Deploy an existing selected artifact:
#   RELAY_HOST=<ssh alias> deploy_relay_tarball.sh <tarball> <commit-sha> [version]
# RELAY_HOME / RELAY_CONFIG default to the remote login's HOME, never this host's.
# SERVICE and DROPIN match autoupdate.sh; RELAY_PYTHON defaults to python3.
# Every previous drop-in/version backup is retained until operator approval.
set -euo pipefail
[ "$#" -ge 2 ] && [ "$#" -le 3 ] || { echo "usage: $0 <tarball> <commit-sha> [version]" >&2; exit 1; }
: "${RELAY_HOST:?set RELAY_HOST to the verified relay SSH alias}"
OUT=$1
[[ -f "$OUT" && "$2" =~ ^[0-9a-fA-F]{7,40}$ ]] || { echo "expected an artifact and commit SHA" >&2; exit 1; }
VERSION=${3:-}
[[ "$VERSION" =~ ^[0-9A-Za-z.+-]*$ ]] || { echo "invalid version marker" >&2; exit 1; }
if [ -z "${RELAY_HOME:-}" ] || [ -z "${RELAY_CONFIG:-}" ]; then
    remote_home=$(ssh -- "$RELAY_HOST" 'printf "%s" "$HOME"')
    : "${remote_home:?the remote login returned no HOME}"
    RELAY_HOME=${RELAY_HOME:-$remote_home/.local/share/kollab-relay}
    RELAY_CONFIG=${RELAY_CONFIG:-$remote_home/.config/kollab-relay/runtime.json}
fi
SERVICE=${SERVICE:-kollab-relay.service}
DROPIN=${DROPIN:-/etc/systemd/system/$SERVICE.d/source-v2.conf}
PYTHON=${RELAY_PYTHON:-python3}
ATTEMPTS=${RELAY_HEALTH_ATTEMPTS:-20}
INTERVAL=${RELAY_HEALTH_INTERVAL:-1}
for path in "$RELAY_HOME" "$RELAY_CONFIG" "$DROPIN"; do
    [[ "$path" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo "expected an ordinary absolute deployment path" >&2; exit 1; }
done
[[ "$SERVICE" =~ ^[A-Za-z0-9_.@-]+\.service$ && "$ATTEMPTS" =~ ^[1-9][0-9]*$ && "$INTERVAL" =~ ^[0-9]+([.][0-9]+)?$ ]] \
    || { echo "invalid service or health polling settings" >&2; exit 1; }
TAG="$(date -u +%Y%m%d-%H%M%S)-$2"
LOCAL_SHA=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$OUT")

# OpenSSH joins command arguments through the remote shell. Quote the complete
# argument vector instead of interpolating runtime paths as shell code.
remote() {
    local command
    command=$(python3 -c 'import shlex,sys; print(shlex.join(["bash","-s","--",*sys.argv[1:]]))' "$@")
    ssh -- "$RELAY_HOST" "$command"
}
REL=$(remote "$RELAY_HOME" "$TAG" <<'PREPARE'
set -euo pipefail
mkdir -p "$1/releases"
mktemp -d "$1/releases/$2.XXXXXX"
PREPARE
)
echo "tag=$TAG sha256=$LOCAL_SHA"
scp -q "$OUT" "$RELAY_HOST:$REL/source.tar.gz"

remote "$REL" "$LOCAL_SHA" "$VERSION" "$RELAY_HOME" "$RELAY_CONFIG" "$DROPIN" "$SERVICE" "$PYTHON" "$ATTEMPTS" "$INTERVAL" <<'DEPLOY'
set -Eeuo pipefail
REL=$1; WANT=$2; VERSION=$3; RELAY_HOME=$4; RELAY_CONFIG=$5
DROPIN=$6; SERVICE=$7; PYTHON=$8; ATTEMPTS=$9; INTERVAL=${10}
TAG=${REL##*/}
exec 9>"$RELAY_HOME/update.lock"
flock -n 9 || { echo "relay update lock is held; uploaded artifact was not activated" >&2; exit 75; }
cd "$REL"
"$PYTHON" - "$WANT" <<'PY'
import hashlib, json, pathlib, sys, tarfile
archive = pathlib.Path("source.tar.gz")
if hashlib.sha256(archive.read_bytes()).hexdigest() != sys.argv[1]:
    raise SystemExit("uploaded tarball checksum mismatch")
with tarfile.open(archive) as source:
    members = source.getmembers()
    names = [member.name for member in members]
    if len(set(names)) != len(names) or any(
        not member.isfile() or pathlib.PurePosixPath(member.name).is_absolute()
        or ".." in pathlib.PurePosixPath(member.name).parts for member in members
    ):
        raise SystemExit("source archive must contain unique ordinary relative files")
    manifest = json.load(source.extractfile("source-sha256.json"))
    if set(manifest) != set(names) - {"source-sha256.json"}:
        raise SystemExit("source manifest does not cover the exact artifact")
    for name, digest in manifest.items():
        if hashlib.sha256(source.extractfile(name).read()).hexdigest() != digest:
            raise SystemExit(f"source manifest checksum mismatch: {name}")
    source.extractall(".", filter="data")
print(f"manifest verified: {len(manifest)} files")
PY
"$PYTHON" -m venv .venv
.venv/bin/pip install -q --upgrade pip >/dev/null
.venv/bin/pip install -q -r scripts/relay/requirements.txt
KOLLAB_NO_KEYRING=1 .venv/bin/python -c "import sys; sys.path.insert(0,'.'); import plugins.hub.relay_service, plugins.hub.relay_runtime"
HEALTH_URL=$("$PYTHON" - "$RELAY_CONFIG" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
host = config["bind_host"]
if ":" in host:
    host = f"[{host}]"
print(f'http://{host}:{int(config["health_port"])}/relay/v1/health')
PY
)
healthy() {
    curl -fsS -m 3 "$HEALTH_URL" 2>/dev/null | "$PYTHON" -c '
import json,sys
try:
    value = json.load(sys.stdin)
except (ValueError, OSError):
    sys.exit(1)
sys.exit(0 if isinstance(value, dict) and value.get("status") == "ok" and value.get("degraded") is False else 1)
'
}
fingerprint() {
    "$PYTHON" - "$1" <<'PY'
import hashlib, os, stat, sys
try:
    descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except FileNotFoundError:
    print("absent")
else:
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SystemExit("deployment target must be an ordinary file")
        os.set_blocking(stream.fileno(), True)
        print(hashlib.file_digest(stream, "sha256").hexdigest())
PY
}
require_hash() {
    local actual
    actual=$(fingerprint "$1") || { echo "cannot verify deployment target: $1" >&2; return 1; }
    if [ "$actual" != "$2" ] && { [ -z "${3:-}" ] || [ "$actual" != "$3" ]; }; then
        echo "concurrent edit prevents changing $1; retained backup=$BACKUP" >&2
        return 1
    fi
}
render_dropin() {
    printf '[Service]\nWorkingDirectory=%s\nExecStart=\nExecStart=%s/.venv/bin/python kollabor_cli_main.py relay run --config %s\n' \
        "$REL" "$REL" "$RELAY_CONFIG"
}
sudo -n test -f "$DROPIN"
[ ! -L "$RELAY_HOME/version" ] || { echo "version marker must not be a symlink" >&2; exit 1; }
BACKUP=$(mktemp -d "$RELAY_HOME/rollback-$TAG.XXXXXX")
sudo -n cp -p -- "$DROPIN" "$BACKUP/dropin.conf"
if [ -e "$RELAY_HOME/version" ]; then
    [ -f "$RELAY_HOME/version" ] || { echo "version marker must be an ordinary file" >&2; exit 1; }
    cp -p -- "$RELAY_HOME/version" "$BACKUP/version"
else
    touch "$BACKUP/version.absent"
fi
printf '%s\n' "Retain until operator approval for cleanup." > "$BACKUP/RETENTION"
ORIGINAL_DROPIN_SHA=$(fingerprint "$BACKUP/dropin.conf")
ORIGINAL_VERSION_SHA=absent
if [ -f "$BACKUP/version" ]; then ORIGINAL_VERSION_SHA=$(fingerprint "$BACKUP/version"); fi
OWNED_DROPIN_SHA=$ORIGINAL_DROPIN_SHA
OWNED_VERSION_SHA=$ORIGINAL_VERSION_SHA
PENDING_VERSION_SHA=
CANDIDATE_DROPIN_SHA=$(render_dropin | "$PYTHON" -c 'import hashlib,sys; print(hashlib.file_digest(sys.stdin.buffer,"sha256").hexdigest())')
restore_files() {
    # Refuse the whole rollback if either target already belongs to another writer.
    require_hash "$DROPIN" "$OWNED_DROPIN_SHA" || return 1
    require_hash "$RELAY_HOME/version" "$OWNED_VERSION_SHA" "$PENDING_VERSION_SHA" || return 1
    require_hash "$BACKUP/dropin.conf" "$ORIGINAL_DROPIN_SHA" || return 1
    if [ "$ORIGINAL_VERSION_SHA" != absent ]; then
        require_hash "$BACKUP/version" "$ORIGINAL_VERSION_SHA" || return 1
    fi
    # Recheck each target immediately before its own write/removal.
    require_hash "$DROPIN" "$OWNED_DROPIN_SHA" || return 1
    sudo -n cp -p -- "$BACKUP/dropin.conf" "$DROPIN" || return 1
    require_hash "$RELAY_HOME/version" "$OWNED_VERSION_SHA" "$PENDING_VERSION_SHA" || return 1
    if [ "$ORIGINAL_VERSION_SHA" = absent ]; then
        if [ -e "$RELAY_HOME/version" ]; then rm -- "$RELAY_HOME/version" || return 1; fi
    else
        cp -p -- "$BACKUP/version" "$RELAY_HOME/version" || return 1
    fi
}
rollback() {
    local original_status=$1 failed=0
    trap - ERR INT TERM HUP
    set +e
    if restore_files; then
        sudo -n systemctl daemon-reload || failed=1
        sudo -n systemctl restart "$SERVICE" || failed=1
        if [ "$failed" -eq 0 ]; then
            healthy || { echo "prior files restored; previous service health remains unverified" >&2; failed=1; }
        fi
    else
        failed=1
        echo "automatic rollback refused or incomplete; service left for operator recovery" >&2
    fi
    echo "deployment failed (status $original_status); rollback status=$failed; retained backup=$BACKUP" >&2
    exit "$original_status"
}
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM
trap 'rollback 129' HUP
require_hash "$RELAY_HOME/version" "$OWNED_VERSION_SHA"
require_hash "$DROPIN" "$OWNED_DROPIN_SHA"
OWNED_DROPIN_SHA=$CANDIDATE_DROPIN_SHA
render_dropin | sudo -n tee "$DROPIN" >/dev/null
sudo -n systemctl daemon-reload
sudo -n systemctl restart "$SERVICE"
for ((i=1; i<=ATTEMPTS; i++)); do
    sleep "$INTERVAL"
    if healthy; then
        require_hash "$DROPIN" "$OWNED_DROPIN_SHA"
        require_hash "$RELAY_HOME/version" "$OWNED_VERSION_SHA"
        if [ -n "$VERSION" ]; then
            marker=$(mktemp "$RELAY_HOME/.version.XXXXXX")
            printf '%s\n' "$VERSION" > "$marker"
            NEW_VERSION_SHA=$(fingerprint "$marker")
            require_hash "$RELAY_HOME/version" "$OWNED_VERSION_SHA"
            # A signal can arrive immediately after mv, before the next assignment.
            PENDING_VERSION_SHA=$NEW_VERSION_SHA
            mv -- "$marker" "$RELAY_HOME/version"
            OWNED_VERSION_SHA=$NEW_VERSION_SHA
            PENDING_VERSION_SHA=
        fi
        trap - ERR INT TERM HUP
        echo "activated=$REL service=$SERVICE version=${VERSION:-unchanged} sha256=$WANT"
        echo "retained rollback=$BACKUP (dropin.conf, version or version.absent); cleanup requires operator approval"
        exit 0
    fi
done
echo "candidate did not become healthy; restoring prior drop-in and version" >&2
false
DEPLOY
