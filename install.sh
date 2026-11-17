#!/usr/bin/env bash
# Kollab Installer
# Installs the single-file kollab binary for this machine (macOS or Linux on
# x86_64 or arm64): no Python needed. Elsewhere, or when the binary cannot run,
# it installs the Python package with uv tool > pipx > pip.
#
# Environment:
#   KOLLAB_INSTALL_METHOD  binary, uv, pipx or pip: use only that method
#   KOLLAB_VERSION         install this release (0.15.1) instead of the latest
#   KOLLAB_INSTALL_DIR     where the binary goes (default: ~/.local/bin)
#   KOLLAB_RELEASES_URL    where release binaries are downloaded from

set -euo pipefail

REPO_URL="https://github.com/kollaborai/kollab"
PKG_NAME="kollab"
CMD_NAME="kollab"
VERSION="${KOLLAB_VERSION:-}"
VERSION="${VERSION#v}"
PKG_SPEC="$PKG_NAME${VERSION:+==$VERSION}"
BIN_DIR="${KOLLAB_INSTALL_DIR:-$HOME/.local/bin}"
RELEASES_URL="${KOLLAB_RELEASES_URL:-$REPO_URL/releases}"
ORIGINAL_PATH="$PATH"
INSTALLED_DIR="$HOME/.local/bin"
DIR_ADDED_TO_PATH=0
VERSION_CHECK_TIMEOUT_SECONDS=30

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

prepend_to_path() {
    local dir="$1"
    if [[ -d "$dir" ]]; then
        if [[ ":$PATH:" != *":$dir:"* ]]; then
            DIR_ADDED_TO_PATH=1
        fi
        export PATH="$dir:$PATH"
    fi
}

# The release binary for this machine, named as CI builds them.
binary_asset() {
    local os arch
    case "$(uname -s)" in
        Darwin) os=macos ;;
        Linux) os=linux ;;
        *) return 1 ;;
    esac
    case "$(uname -m)" in
        arm64|aarch64) arch=aarch64 ;;
        x86_64|amd64) arch=x86_64 ;;
        *) return 1 ;;
    esac
    # The Linux binaries need glibc; musl systems (Alpine) take the Python package.
    if [[ "$os" == linux ]] && ldd --version 2>&1 | grep -qi musl; then
        return 1
    fi
    echo "kollab-$os-$arch"
}

sha256_of() {
    if command_exists sha256sum; then
        sha256sum "$1" | cut -d' ' -f1
    elif command_exists shasum; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        return 1
    fi
}

# uv tool and pipx would put their own kollab back over the binary on their
# next upgrade, so a kollab they manage at the binary's path is uninstalled.
retire_managed_install() {
    local current="$BIN_DIR/$CMD_NAME" target
    [[ -L "$current" ]] || return 0
    target=$(readlink "$current")
    case "$target" in
        */uv/tools/*)
            if command_exists uv; then
                info "Removing the uv tool install of $PKG_NAME; the binary replaces it..."
                uv tool uninstall "$PKG_NAME" >/dev/null 2>&1 || true
            fi
            ;;
        */pipx/venvs/*)
            if command_exists pipx; then
                info "Removing the pipx install of $PKG_NAME; the binary replaces it..."
                pipx uninstall "$PKG_NAME" >/dev/null 2>&1 || true
            fi
            ;;
    esac
}

# Download the release binary, check its SHA-256 and that it starts, then move
# it into place. Anything short of that leaves the current install alone.
try_binary() {
    local asset url tmp expected actual shown
    command_exists curl || return 1
    asset=$(binary_asset) || return 1
    if [[ -n "$VERSION" ]]; then
        url="$RELEASES_URL/download/v$VERSION/$asset"
    else
        url="$RELEASES_URL/latest/download/$asset"
    fi

    tmp=$(mktemp -d)
    info "Downloading $asset..."
    if ! curl -fsSL "$url" -o "$tmp/$asset" || ! curl -fsSL "$url.sha256" -o "$tmp/$asset.sha256"; then
        warn "No kollab binary at $url"
        rm -rf "$tmp"
        return 1
    fi
    expected=$(cut -d' ' -f1 "$tmp/$asset.sha256")
    if ! actual=$(sha256_of "$tmp/$asset"); then
        warn "Neither sha256sum nor shasum is available to check the download"
        rm -rf "$tmp"
        return 1
    fi
    if [[ "$actual" != "$expected" ]]; then
        warn "The download did not match its SHA-256 checksum"
        rm -rf "$tmp"
        return 1
    fi

    chmod +x "$tmp/$asset"
    info "Starting it once; the first start unpacks its own Python..."
    # A kollab binary's own variables would point this one at another file.
    if ! shown=$(env -u SCIE -u SCIE_ARGV0 -u PEX "$tmp/$asset" --version 2>&1); then
        warn "The binary did not start on this machine: $shown"
        rm -rf "$tmp"
        return 1
    fi
    if [[ -n "$VERSION" && "${shown##* }" != "$VERSION" ]]; then
        warn "Expected kollab $VERSION; the binary reports: $shown"
        rm -rf "$tmp"
        return 1
    fi

    if ! mkdir -p "$BIN_DIR"; then
        rm -rf "$tmp"
        return 1
    fi
    retire_managed_install
    # Copy beside the target first, so the final rename is atomic.
    if ! mv -f "$tmp/$asset" "$BIN_DIR/.$CMD_NAME.new" || ! mv -f "$BIN_DIR/.$CMD_NAME.new" "$BIN_DIR/$CMD_NAME"; then
        warn "Could not write $BIN_DIR/$CMD_NAME"
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
    INSTALLED_DIR="$BIN_DIR"
    prepend_to_path "$BIN_DIR"
    success "Installed kollab ${shown##* } at $BIN_DIR/$CMD_NAME"
}

# Try uv tool (fastest, most modern)
try_uv() {
    local bin_dir
    if ! command_exists uv; then
        return 1
    fi

    info "Installing with uv tool (recommended - fastest, isolated)..."

    uv tool install --force "$PKG_SPEC" || return 1
    bin_dir=$(uv tool dir --bin 2>/dev/null) || bin_dir=""
    INSTALLED_DIR="$bin_dir"
    if [[ -n "$bin_dir" ]]; then
        prepend_to_path "$bin_dir"
    fi
    return 0
}

# Try pipx (isolated, clean)
try_pipx() {
    local bin_dir
    if ! command_exists pipx; then
        return 1
    fi

    info "Installing with pipx (isolated environment)..."

    if [[ -z "$VERSION" ]] && pipx list 2>/dev/null | grep -q "$PKG_NAME"; then
        info "Already installed, upgrading..."
        pipx upgrade "$PKG_NAME" || return 1
    else
        pipx install --force "$PKG_SPEC" || return 1
    fi
    bin_dir=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null) || bin_dir=""
    INSTALLED_DIR="$bin_dir"
    if [[ -n "$bin_dir" ]]; then
        prepend_to_path "$bin_dir"
    fi
    return 0
}

# Resolve the scripts directory used by the interpreter behind pip. This keeps
# system installs and --user installs tied to the executable pip just wrote.
pip_scripts_dir() {
    local pip_cmd="$1" scheme="$2" pip_path shebang python_cmd env_args
    if [[ -n "${PIP_PREFIX:-}" ]]; then
        printf '%s/bin\n' "$PIP_PREFIX"
        return 0
    fi
    pip_path=$(command -v "$pip_cmd") || return 1
    IFS= read -r shebang < "$pip_path" || return 1
    shebang="${shebang#\#!}"
    read -r python_cmd _ <<< "$shebang"
    if [[ "$python_cmd" == */sh ]]; then
        IFS= read -r env_args < <(sed -n '2p' "$pip_path") || return 1
        [[ "$env_args" == *exec* ]] || return 1
        env_args="${env_args#*exec}"
        env_args="${env_args#\'}"
        env_args="${env_args# }"
        if [[ "$env_args" == \"* ]]; then
            python_cmd="${env_args#\"}"
            python_cmd="${python_cmd%%\"*}"
        elif [[ "$env_args" == \'* ]]; then
            python_cmd="${env_args#\'}"
            python_cmd="${python_cmd%%\'*}"
        else
            read -r python_cmd _ <<< "$env_args"
        fi
        if [[ "$python_cmd" != */* ]]; then
            python_cmd=$(command -v "$python_cmd") || return 1
        fi
    elif [[ "$python_cmd" == */env ]]; then
        env_args="${shebang#*env }"
        read -r python_cmd _ <<< "$env_args"
        if [[ "$python_cmd" == "-S" ]]; then
            # env -S splits the remaining shebang into the interpreter and its args.
            env_args="${env_args#-S }"
            read -r python_cmd _ <<< "$env_args"
            python_cmd="${python_cmd#\"}"
            python_cmd="${python_cmd%\"}"
            python_cmd="${python_cmd#\'}"
            python_cmd="${python_cmd%\'}"
        fi
        python_cmd=$(command -v "$python_cmd") || return 1
    fi
    [[ -x "$python_cmd" ]] || return 1
    KOLLAB_NO_KEYRING=1 "$python_cmd" -c \
        'import sysconfig; import sys; print(sysconfig.get_path("scripts", scheme=sys.argv[1] or None))' \
        "$scheme"
}

# Keep a broken installed command from blocking the installer forever.
run_version_check() {
    local executable="$1" output_file pid elapsed status
    output_file=$(mktemp) || return 1
    env -u SCIE -u SCIE_ARGV0 -u PEX "$executable" --version >"$output_file" 2>&1 &
    pid=$!
    for ((elapsed = 0; elapsed < VERSION_CHECK_TIMEOUT_SECONDS * 10; elapsed++)); do
        if [[ " $(jobs -pr) " != *" $pid "* ]]; then
            break
        fi
        sleep 0.1
    done
    if [[ " $(jobs -pr) " == *" $pid "* ]]; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        printf 'timed out after %s seconds' "$VERSION_CHECK_TIMEOUT_SECONDS"
        rm -f "$output_file"
        return 124
    fi
    if wait "$pid"; then
        status=0
    else
        status=$?
    fi
    cat "$output_file"
    rm -f "$output_file"
    return "$status"
}

# Try pip (standard)
try_pip() {
    local install_scheme="" scripts_dir configured_user user_setting
    if ! command_exists pip && ! command_exists pip3; then
        return 1
    fi

    local pip_cmd="pip"
    command_exists pip3 && pip_cmd="pip3"

    info "Installing with $pip_cmd (system-wide)..."

    user_setting=$(printf '%s' "${PIP_USER:-}" | tr '[:upper:]' '[:lower:]')
    case "$user_setting" in
        1|true|yes|on)
            install_scheme="posix_user"
            ;;
        0|false|no|off)
            ;;
        *)
            configured_user=$("$pip_cmd" config get install.user 2>/dev/null || true)
            user_setting=$(printf '%s' "$configured_user" | tr '[:upper:]' '[:lower:]')
            case "$user_setting" in
                1|true|yes|on) install_scheme="posix_user" ;;
            esac
            ;;
    esac

    # Check if we need --user flag
    if "$pip_cmd" install --help 2>&1 | grep -q -- --user; then
        # Try without --user first, fall back if permission denied
        if ! "$pip_cmd" install "$PKG_SPEC" 2>/dev/null; then
            warn "Trying with --user flag..."
            "$pip_cmd" install --user "$PKG_SPEC" || return 1
            install_scheme="posix_user"
        fi
    else
        "$pip_cmd" install "$PKG_SPEC" || return 1
    fi
    scripts_dir=$(pip_scripts_dir "$pip_cmd" "$install_scheme") || scripts_dir=""
    INSTALLED_DIR="$scripts_dir"
    if [[ -n "$scripts_dir" ]]; then
        prepend_to_path "$scripts_dir"
    fi
    return 0
}

# Verify installation
verify_install() {
    local executable="$INSTALLED_DIR/$CMD_NAME" shown version_line installed_version found
    if [[ -z "$INSTALLED_DIR" || ! -x "$executable" ]]; then
        if [[ -n "$INSTALLED_DIR" ]]; then
            error "The selected install method did not produce an executable at $executable."
        else
            error "Could not identify the executable produced by the selected install method."
        fi
        found=$(PATH="$ORIGINAL_PATH" command -v "$CMD_NAME" || true)
        if [[ -n "$found" ]]; then
            info "PATH resolves to $found; install Kollab with the selected method or check its install location."
        else
            info "Check the install method's output and install Kollab again."
        fi
        return 1
    fi

    if shown=$(run_version_check "$executable"); then
        :
    else
        local version_status=$?
        if [[ "$version_status" -eq 124 ]]; then
            error "The installed executable at $executable timed out on --version: $shown"
        else
            error "The installed executable at $executable did not start: $shown"
        fi
        return 1
    fi
    version_line="${shown##*$'\n'}"
    if [[ ! "$version_line" =~ ^kollab[[:space:]]([0-9][A-Za-z0-9.+!_-]*)$ ]]; then
        error "Could not parse the version from the expected 'kollab <version>' output at $executable: $shown"
        return 1
    fi
    installed_version="${BASH_REMATCH[1]}"
    if [[ -n "$VERSION" && "$installed_version" != "$VERSION" ]]; then
        error "Expected kollab $VERSION at $executable, but it reports $installed_version."
        return 1
    fi

    # ORIGINAL_PATH is the environment a newly opened shell inherits.
    found=$(PATH="$ORIGINAL_PATH" command -v "$CMD_NAME" || true)
    if [[ ! "$found" -ef "$executable" ]]; then
        if [[ -n "$found" ]]; then
            warn "Another $CMD_NAME comes first on PATH: $found"
            info "The installed executable is $executable. Put $INSTALLED_DIR before it in PATH, then open a new shell."
        else
            warn "Kollab $installed_version is installed at $executable but is not discoverable in a new shell."
            info "Add $INSTALLED_DIR to PATH, then open a new shell."
        fi
        return 1
    fi

    success "Installation verified: kollab $installed_version at $executable."
    echo
    info "Run '$CMD_NAME' to start using Kollab."
    info "For configuration, see: $REPO_URL"
    return 0
}

# Main installation flow. Each try_* runs inside an `if`, where set -e is off,
# so every step in them returns 1 on failure itself.
main() {
    local methods method
    echo
    echo "  Kollab Installer"
    echo "  ================"
    echo

    case "${KOLLAB_INSTALL_METHOD:-auto}" in
        auto) methods=(binary uv pipx pip) ;;
        binary|uv|pipx|pip) methods=("$KOLLAB_INSTALL_METHOD") ;;
        *)
            error "KOLLAB_INSTALL_METHOD must be binary, uv, pipx or pip."
            exit 1
            ;;
    esac

    # Try methods in order of preference
    for method in "${methods[@]}"; do
        if "try_$method"; then
            verify_install
            exit $?
        fi
        if [[ "$method" == binary && ${#methods[@]} -gt 1 ]]; then
            info "Installing the Python package instead..."
        fi
    done

    # Nothing worked
    error "Could not install $PKG_NAME."
    echo
    info "The binary covers macOS and Linux (glibc) on x86_64 and arm64."
    info "Anywhere else, install one of the following:"
    echo "  - uv:     https://docs.astral.sh/uv/getting-started/installation/"
    echo "  - pipx:   https://pipx.pypa.io/stable/installation/"
    echo "  - Python: https://www.python.org/downloads/"
    echo
    info "Then run this script again."
    exit 1
}

main "$@"
