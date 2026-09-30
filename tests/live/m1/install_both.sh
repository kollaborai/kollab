#!/usr/bin/env bash
# install_both.sh <wheeldir>
# Fresh venv + the eleven local wheels on the Mac (~/kollab-m1/venv, workspace
# ~/kollab-m1-mac) and on alzan-prod (~/kollab-m1/venv, workspace
# ~/kollab-m1-server). Third-party dependencies come from PyPI. Touches nothing
# else: not the uv tool install, not ~/kollab-e2e-*, not any tmux session.
# Re-running reinstalls the same wheels into the same venvs.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
WD=${1:?usage: install_both.sh <wheeldir>}
WD=$(cd "$WD" && pwd)

WHEELS=("$WD"/*-"$M1_VERSION"-*.whl)
[ "${#WHEELS[@]}" -eq "$M1_EXPECT_WHEELS" ] || die "expected $M1_EXPECT_WHEELS *-$M1_VERSION-*.whl in $WD, found ${#WHEELS[@]} (run build_wheels.sh)"
NAMES=()
for w in "${WHEELS[@]}"; do NAMES+=("$(basename "$w")"); done

# ---- Mac -------------------------------------------------------------------
PY=$(m1_pick_python) || die "no Python >= 3.12 on the Mac (set M1_PY_MAC)"
say "mac: $($PY --version) at $PY"
mkdir -p "$M1_MAC_ROOT" "$M1_MAC_WS"
[ -x "$M1_MAC_VENV/bin/python" ] || "$PY" -m venv "$M1_MAC_VENV"
say "mac: pip install of ${#WHEELS[@]} local wheels into $M1_MAC_VENV"
"$M1_MAC_VENV/bin/python" -m pip install -q --disable-pip-version-check --no-cache-dir --force-reinstall "${WHEELS[@]}"

# ---- alzan-prod --------------------------------------------------------------
m1_srv_paths
say "srv: $M1_HOST home is $M1_SRV_HOME"
m1_ssh "python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'" || die "python3 on $M1_HOST is older than 3.12"
m1_ssh "mkdir -p '$M1_SRV_ROOT/wheels' '$M1_SRV_ROOT/bin' '$M1_SRV_WS'"
say "srv: copying wheels to $M1_SRV_ROOT/wheels"
scp -q "${M1_SSH_OPTS[@]}" "${WHEELS[@]}" "$M1_HOST:$M1_SRV_ROOT/wheels/"
LOCAL_SUMS=$(cd "$WD" && shasum -a 256 "${NAMES[@]}" | sort -k2)
REMOTE_SUMS=$(m1_ssh "cd '$M1_SRV_ROOT/wheels' && sha256sum ${NAMES[*]}" | sort -k2)
[ "$LOCAL_SUMS" = "$REMOTE_SUMS" ] || die "wheel checksums differ between the Mac and $M1_HOST"
say "srv: checksums match"
m1_ssh "[ -x '$M1_SRV_VENV/bin/python' ] || python3 -m venv '$M1_SRV_VENV'"
say "srv: pip install into $M1_SRV_VENV"
m1_ssh "cd '$M1_SRV_ROOT/wheels' && '$M1_SRV_VENV/bin/python' -m pip install -q --disable-pip-version-check --no-cache-dir --force-reinstall ${NAMES[*]}"

# ---- verify -------------------------------------------------------------------
cd "$HOME"
MAC_V=$("$M1_MAC_VENV/bin/kollab" --version 2>&1 | tail -1)
SRV_V=$(m1_ssh "cd \"\$HOME\" && '$M1_SRV_VENV/bin/kollab' --version 2>&1 | tail -1")
say "mac: $MAC_V"
say "srv: $SRV_V"
"$M1_MAC_VENV/bin/python" -c "import plugins.hub.device_names, plugins.hub.relay_service" || die "mac: new hub modules missing from the installed build"
m1_ssh "cd \"\$HOME\" && '$M1_SRV_VENV/bin/python' -c 'import plugins.hub.device_names, plugins.hub.relay_service'" || die "srv: new hub modules missing from the installed build"
case "$MAC_V" in *"$M1_VERSION") ;; *) die "mac version is not $M1_VERSION: $MAC_V" ;; esac
case "$SRV_V" in *"$M1_VERSION") ;; *) die "srv version is not $M1_VERSION: $SRV_V" ;; esac
say "both hosts run $M1_VERSION. workspaces: $M1_MAC_WS (mac), $M1_SRV_WS ($M1_HOST)"
