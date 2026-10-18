#!/usr/bin/env bash
# Sourced by the tests/tmux/*.sh smoke scripts that start kollab without test_runner.sh.
#
# A smoke script must never run kollab against the developer's real ~/.kollab: the
# app would attach to a live daemon, join the live hub, wake running agents (paid
# model calls) and write conversation logs. The hub's unix sockets also live in
# /tmp/kollabor-hub/<project hash>/, outside HOME, and an agent deletes socket files
# it has no presence record for, so a script run from this checkout needs its own
# project root as well. Same rules as the runner (README, "HOME Isolation").
#
#   REPO_ROOT=...                                   # the checkout, set before sourcing
#   source "$REPO_ROOT/tests/tmux/lib/isolated_home.sh"
#   isolated_home_setup                             # throwaway HOME, sets ISOLATED_ENV
#   tmux ... "$ISOLATED_ENV $ISOLATED_PYTHON main.py"   # the app, isolated
#   isolated_run "$ISOLATED_PYTHON" -c '...'        # anything else that reads kollab state
#   trap isolated_home_cleanup EXIT                 # stops what is left, removes the HOME
#
# HOME is applied per command (`env HOME=... cmd`), never exported by the script,
# so tmux, ps and the script's own tools keep the real one.

ISOLATED_HOME=""
ISOLATED_ENV=""
ISOLATED_PYTHON=""

isolated_home_setup() {
    # $1 (optional): the JSON of the HOME's config.json, when the script needs a profile
    # The interpreter is resolved before HOME moves: a pyenv shim looks under $HOME.
    ISOLATED_PYTHON="$REPO_ROOT/.venv/bin/python"
    [ -x "$ISOLATED_PYTHON" ] || ISOLATED_PYTHON="$(python -c 'import sys; print(sys.executable)')"
    ISOLATED_HOME="$(mktemp -d "${TMPDIR:-/tmp}/kollab-tmux-home.XXXXXX")"
    mkdir -p "$ISOLATED_HOME/project" "$ISOLATED_HOME/.kollab"
    # a configured machine, so the first-run wizard does not cover what the script checks
    local config="${1:-}"
    [ -n "$config" ] || config='{"application":{"setup_completed":true}}'
    printf '%s' "$config" > "$ISOLATED_HOME/.kollab/config.json"
    touch "$ISOLATED_HOME/.kollab/connect-guide-seen"
    # KOLLAB_WEBUI_HOSTS is the developer's VPN address: a web UI would listen there too
    ISOLATED_ENV="env -u KOLLAB_WEBUI_HOSTS KOLLAB_NO_KEYRING=1 HOME=$ISOLATED_HOME KOLLAB_PROJECT_ROOT=$ISOLATED_HOME/project"
}

isolated_run() {
    env -u KOLLAB_WEBUI_HOSTS KOLLAB_NO_KEYRING=1 HOME="$ISOLATED_HOME" \
        KOLLAB_PROJECT_ROOT="$ISOLATED_HOME/project" "$@"
}

# Pids whose environment says HOME is this script's throwaway HOME. The unique
# mkdtemp path is the proof of ownership: only commands started through
# isolated_run / ISOLATED_ENV carry it.
isolated_home_pids() {
    /bin/ps axeww -o pid=,command= | "$ISOLATED_PYTHON" -c '
import os, sys
needle = " HOME=" + sys.argv[1]
for line in sys.stdin:
    pid, _, rest = line.strip().partition(" ")
    at = rest.find(needle)
    while at != -1:
        end = at + len(needle)
        if end == len(rest) or rest[end].isspace():
            if int(pid) != os.getpid():
                print(pid)
            break
        at = rest.find(needle, end)
' "$ISOLATED_HOME"
}

isolated_home_cleanup() {
    [ -n "$ISOLATED_HOME" ] && [ -d "$ISOLATED_HOME" ] || return 0
    local sig pids
    for sig in TERM KILL; do
        pids="$(isolated_home_pids)"
        [ -n "$pids" ] || break
        # shellcheck disable=SC2086  # one pid per line, splitting is the point
        kill -"$sig" $pids 2>/dev/null || true
        sleep 3
    done
    # A daemon killed hard leaves its socket file behind; its presence record names it.
    "$ISOLATED_PYTHON" - "$ISOLATED_HOME" <<'PY'
import glob, json, os, sys

for record in glob.glob(sys.argv[1] + "/.kollab/**/presence/*.json", recursive=True):
    try:
        sock = json.load(open(record)).get("socket_path")
        if isinstance(sock, str) and sock.startswith("/tmp/kollabor-hub/"):
            if os.path.exists(sock):
                os.unlink(sock)
            os.rmdir(os.path.dirname(sock))
    except (OSError, ValueError):
        pass
PY
    rm -rf "$ISOLATED_HOME"
    ISOLATED_HOME=""
}
