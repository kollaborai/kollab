#!/usr/bin/env bash
# Kollab Installer
# Auto-detects best installation method: uvx > pipx > pip

set -euo pipefail

REPO_URL="https://github.com/kollaborai/kollab"
PKG_NAME="kollab"
CMD_NAME="kollab"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# Detect command availability
command_exists() {
    command -v "$1" &>/dev/null
}

# Try uvx (fastest, most modern)
try_uvx() {
    if ! command_exists uvx && ! command_exists uv; then
        return 1
    fi

    info "Installing with uvx (recommended - fastest, isolated)..."

    if command_exists uvx; then
        uvx --from "$PKG_NAME" "$CMD_NAME" --version 2>/dev/null && return 0
    elif command_exists uv; then
        uv tool run "$PKG_NAME" "$CMD_NAME" --version 2>/dev/null && return 0
    fi

    warn "uvx available but package not found. Installing to uv tool cache..."
    if command_exists uvx; then
        uvx --from "$PKG_NAME" "$CMD_NAME" --version 2>/dev/null && return 0
    elif command_exists uv; then
        uv tool install "$PKG_NAME" && return 0
    fi

    return 1
}

# Try pipx (isolated, clean)
try_pipx() {
    if ! command_exists pipx; then
        return 1
    fi

    info "Installing with pipx (isolated environment)..."

    if pipx list 2>/dev/null | grep -q "$PKG_NAME"; then
        info "Already installed, upgrading..."
        pipx upgrade "$PKG_NAME"
    else
        pipx install "$PKG_NAME"
    fi
}

# Try pip (standard)
try_pip() {
    if ! command_exists pip && ! command_exists pip3; then
        return 1
    fi

    local pip_cmd="pip"
    command_exists pip3 && pip_cmd="pip3"

    info "Installing with $pip_cmd (system-wide)..."

    # Check if we need --user flag
    if "$pip_cmd" install --help 2>&1 | grep -q -- --user; then
        # Try without --user first, fall back if permission denied
        if ! "$pip_cmd" install "$PKG_NAME" 2>/dev/null; then
            warn "Trying with --user flag..."
            "$pip_cmd" install --user "$PKG_NAME"
        fi
    else
        "$pip_cmd" install "$PKG_NAME"
    fi
}

# Verify installation
verify_install() {
    if command_exists "$CMD_NAME"; then
        success "Installation verified! '$CMD_NAME' is available."
        echo
        info "Run '$CMD_NAME' to start using Kollab."
        info "For configuration, see: $REPO_URL"
        return 0
    fi

    # Check if in PATH
    if [[ ":$PATH:" == *":$HOME/.local/bin:"* ]]; then
        error "'$CMD_NAME' not found in PATH after installation."
        info "You may need to restart your shell or run: export PATH=\"\$HOME/.local/bin:\$PATH\""
        return 1
    fi

    warn "'$CMD_NAME' installed but may not be in PATH."
    info "Try: export PATH=\"\$HOME/.local/bin:\$PATH\""
    return 1
}

# Main installation flow
main() {
    echo
    echo "  Kollab Installer"
    echo "  ================"
    echo

    # Try methods in order of preference
    if try_uvx; then
        verify_install
        exit 0
    fi

    if try_pipx; then
        verify_install
        exit 0
    fi

    if try_pip; then
        verify_install
        exit 0
    fi

    # Nothing worked
    error "Could not find a working installer."
    echo
    info "Please install one of the following:"
    echo "  - uv:     https://docs.astral.sh/uv/getting-started/installation/"
    echo "  - pipx:   https://pipx.pypa.io/stable/installation/"
    echo "  - Python: https://www.python.org/downloads/"
    echo
    info "Then run this script again."
    exit 1
}

main "$@"
