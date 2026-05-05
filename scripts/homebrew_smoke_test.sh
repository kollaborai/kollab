#!/usr/bin/env bash
#
# Smoke test for the Kollab Homebrew formula in a clean user environment.
#
# This validates the macOS/Homebrew install path, not Linuxbrew. It creates a
# temporary HOME, cache, and config directory so first-run files are seeded as
# they would be for a new user.
#
# Usage:
#   ./scripts/homebrew_smoke_test.sh
#   ./scripts/homebrew_smoke_test.sh 1.0.1
#
# Exits 0 on success, 1 on failure.

set -euo pipefail

EXPECTED_VERSION="${1:-}"
TAP_FORMULA="${TAP_FORMULA:-kollaborai/tap/kollab}"
BREW_BIN="${BREW_BIN:-/opt/homebrew/bin/brew}"

if [[ ! -x "$BREW_BIN" ]]; then
    BREW_BIN="$(command -v brew || true)"
fi

if [[ -z "$BREW_BIN" || ! -x "$BREW_BIN" ]]; then
    echo "Error: brew not found. Set BREW_BIN or install Homebrew." >&2
    exit 1
fi

TMP_HOME="$(mktemp -d /tmp/kollab-brew-home.XXXXXX)"
TMP_CACHE="$(mktemp -d /tmp/kollab-brew-cache.XXXXXX)"
TMP_CONFIG="$(mktemp -d /tmp/kollab-brew-config.XXXXXX)"

cleanup() {
    rm -rf "$TMP_HOME" "$TMP_CACHE" "$TMP_CONFIG"
}
trap cleanup EXIT

export HOME="$TMP_HOME"
export HOMEBREW_CACHE="$TMP_CACHE"
export XDG_CONFIG_HOME="$TMP_CONFIG"
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1
export HOMEBREW_NO_ENV_HINTS=1

echo "=== kollab Homebrew smoke test ==="
echo "Formula: $TAP_FORMULA"
echo "Home:    $TMP_HOME"
echo ""

echo "--- reinstall ---"
"$BREW_BIN" reinstall "$TAP_FORMULA"

echo ""
echo "--- brew test ---"
"$BREW_BIN" test "$TAP_FORMULA"

PREFIX="$("$BREW_BIN" --prefix kollab)"
KOLLAB_BIN="$PREFIX/bin/kollab"
PYTHON_BIN="$PREFIX/libexec/bin/python"

if [[ ! -x "$KOLLAB_BIN" ]]; then
    echo "Error: missing brewed kollab executable: $KOLLAB_BIN" >&2
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Error: missing brewed venv python: $PYTHON_BIN" >&2
    exit 1
fi

echo ""
echo "--- version ---"
VERSION_OUTPUT="$("$KOLLAB_BIN" --version)"
echo "  $VERSION_OUTPUT"

if [[ -n "$EXPECTED_VERSION" && "$VERSION_OUTPUT" != *"$EXPECTED_VERSION"* ]]; then
    echo "Error: expected version $EXPECTED_VERSION, got: $VERSION_OUTPUT" >&2
    exit 1
fi

echo ""
echo "--- first-run seed files ---"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path

from kollabor_config.config_utils import initialize_config, initialize_system_prompt

initialize_config()
initialize_system_prompt()

prompt = Path.home() / ".kollab" / "agents" / "default" / "system_prompt.md"
section = Path.home() / ".kollab" / "agents" / "default" / "sections" / "00-header.md"

assert prompt.exists(), f"missing default agent prompt: {prompt}"
assert section.exists(), f"missing default agent section: {section}"
assert "SYSTEM PROMPT LOAD FAILURE" not in prompt.read_text(encoding="utf-8")

print(f"  default agent prompt: {prompt}")
print(f"  default agent section: {section}")
PY

echo ""
echo "=== Homebrew smoke test passed ==="
