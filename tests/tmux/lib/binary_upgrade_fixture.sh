#!/bin/bash
# binary_upgrade_fixture.sh <dir>: an opted-in kollab binary with a newer release waiting.
#
# Builds two binaries from this checkout (run it from the repo root):
#   <dir>/bin/kollab      this version, installed, with automatic updates on
#   <dir>/www/kollab-*    the same code one patch release newer, on a local release server
# Both binaries ask that server, not GitHub, for the latest release. <dir>/home is
# a throwaway HOME with a fake model, and the version check's cache already names
# the newer release, so the next launch installs it and restarts into it.
# <dir>/server.pid and <dir>/home/fake.pid are for cleanup; <dir>/new-version
# holds the version the launch must end on.
set -euo pipefail
R=$(pwd)
D=$1
mkdir -p "$D/src" "$D/wheels" "$D/old-wheels" "$D/new-wheels" "$D/old" "$D/www" "$D/bin" "$D/home/.kollab" "$D/home/ws"
touch "$D/home/.kollab/connect-guide-seen"

PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
OLD=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
NEW=$(python3 -c "v = '$OLD'.split('.'); v[-1] = str(int(v[-1]) + 1); print('.'.join(v))")
echo "$NEW" > "$D/new-version"

# The workspace packages as this checkout builds them; the kollab wheel is rebuilt below.
uv build --all-packages --wheel --out-dir "$D/wheels" >/dev/null 2>&1
find "$D/wheels" -name 'kollabor_*.whl' -exec cp {} "$D/old-wheels/" \; -exec cp {} "$D/new-wheels/" \;

rsync -a --exclude .git --exclude .venv --exclude node_modules --exclude /dist \
    --exclude __pycache__ --exclude .pytest_cache --exclude .mypy_cache --exclude .ruff_cache "$R/" "$D/src/"
python3 - "$D/src" "$PORT" "$NEW" <<'PY'
import sys
from pathlib import Path

src, port, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
updater = src / "kollabor/updates/auto_update.py"
text = updater.read_text()
old = 'LATEST_RELEASE = "https://api.github.com/repos/kollaborai/kollab/releases/latest"'
assert text.count(old) == 1, "LATEST_RELEASE moved; update this fixture"
updater.write_text(text.replace(old, f'LATEST_RELEASE = "http://127.0.0.1:{port}/latest"'))
(src / "new-version.txt").write_text(new)
PY
(cd "$D/src" && uv build --package kollab --wheel --out-dir "$D/old-wheels" >/dev/null 2>&1)
python3 - "$D/src/pyproject.toml" "$OLD" "$NEW" <<'PY'
import sys
from pathlib import Path

path, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text()
assert text.count(f'version = "{old}"') >= 1
path.write_text(text.replace(f'version = "{old}"', f'version = "{new}"', 1))
PY
(cd "$D/src" && uv build --package kollab --wheel --out-dir "$D/new-wheels" >/dev/null 2>&1)

python3 "$R/scripts/build_binary.py" --wheels "$D/old-wheels" --out "$D/old" --expect-version "$OLD" | grep '^built'
python3 "$R/scripts/build_binary.py" --wheels "$D/new-wheels" --out "$D/www" --expect-version "$NEW" | grep '^built'
for binary in "$D"/www/kollab-*; do
    case "$binary" in *.sha256) ;; *) ASSET=$(basename "$binary") ;; esac
done
cp "$D/old/$ASSET" "$D/bin/kollab"

python3 - "$D/www" "$PORT" "$NEW" "$ASSET" <<'PY'
import json
import sys
from pathlib import Path

www, port, new, asset = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
base = f"http://127.0.0.1:{port}/"
names = [asset, f"{asset}.sha256"]
release = {"tag_name": f"v{new}", "assets": [{"name": n, "browser_download_url": base + n} for n in names]}
(www / "latest").write_text(json.dumps(release))
PY
(python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$D/www" >/dev/null 2>&1 & echo $! > "$D/server.pid")

("$R/.venv/bin/python" "$R/tests/tmux/lib/fake_llm.py" "$D/home/fake.port" >/dev/null 2>&1 & echo $! > "$D/home/fake.pid")
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$D/home/fake.port" ] && break; sleep 0.3; done
python3 - "$D/home" "$(cat "$D/home/fake.port")" "$NEW" <<'PY'
import json
import sys
import time
from pathlib import Path

home, port, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
config = {
    "application": {"setup_completed": True},
    "kollabor": {
        "llm": {"active_profile": "fake", "profiles": {"fake": {
            "provider": "custom", "model": "fake-echo",
            "base_url": f"http://127.0.0.1:{port}/v1", "api_key": "not-a-real-key"}}},
        "updates": {
            "auto_update_enabled": True,
            "last_check_timestamp": int(time.time()),
            "cached_latest_version": new,
            "cached_release_url": f"https://github.com/kollaborai/kollab/releases/tag/v{new}",
            "cached_release_name": f"v{new}",
        },
    },
}
(home / ".kollab" / "config.json").write_text(json.dumps(config))
PY
echo "fixture ready: v$OLD installed, v$NEW released on port $PORT"
