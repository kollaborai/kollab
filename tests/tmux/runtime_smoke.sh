#!/usr/bin/env bash
# Full local runtime smoke for the no-provider path.
#
# This proves a fresh, isolated Kollab home can boot the real daemon/attach UI,
# load a mock MCP server, render status/hub state, route attach mutations, and
# reconnect an attach client without reaching any external provider.

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PYTHONPATH_VALUE="$REPO_ROOT"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-engine/src"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-agent/src"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-ai/src"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-events/src"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-tui/src"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-config/src"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-plugins/src"
PYTHONPATH_VALUE="$PYTHONPATH_VALUE:$REPO_ROOT/packages/kollabor-rpc/src"

TMUX_SOCKET="${KOLLAB_RUNTIME_SMOKE_SOCKET:-kollab-runtime-smoke-$$}"
DAEMON_SESSION="runtime-smoke-daemon-$$"
REATTACH_SESSION="runtime-smoke-reattach-$$"
FRESH_HOME="$(mktemp -d "${TMPDIR:-/tmp}/kollab-runtime-home.XXXXXX")"
CAPTURE_FILE="$FRESH_HOME/capture.txt"
KEEP="${KOLLAB_RUNTIME_SMOKE_KEEP:-0}"
IDENTITY=""
DAEMON_PID=""
ACTIVE_SESSION="$DAEMON_SESSION"
TOTAL=0
PASSED=0
FAILED=0

log() {
    echo "[$(date +%H:%M:%S)] $*"
}

pass() {
    TOTAL=$((TOTAL + 1))
    PASSED=$((PASSED + 1))
    echo "  ✔ $*"
}

fail() {
    TOTAL=$((TOTAL + 1))
    FAILED=$((FAILED + 1))
    echo "  ✖ $*"
}

# shellcheck disable=SC2329  # Called from cleanup via trap.
cleanup_home() {
    if [ "$KEEP" = "1" ]; then
        log "keeping temp home: $FRESH_HOME"
        return
    fi

    python - "$FRESH_HOME" <<'PY'
from pathlib import Path
import shutil
import sys
import tempfile

target = Path(sys.argv[1]).resolve()
tmp_root = Path(tempfile.gettempdir()).resolve()
if tmp_root not in target.parents:
    raise SystemExit(f"refusing to clean non-temp path: {target}")
if not target.name.startswith("kollab-runtime-home."):
    raise SystemExit(f"refusing to clean unexpected path: {target}")
shutil.rmtree(target, ignore_errors=True)
PY
}

# shellcheck disable=SC2329  # Called by trap.
cleanup() {
    tmux -L "$TMUX_SOCKET" send-keys -t "$DAEMON_SESSION" C-c 2>/dev/null || true
    tmux -L "$TMUX_SOCKET" send-keys -t "$REATTACH_SESSION" C-c 2>/dev/null || true
    sleep 0.4
    tmux -L "$TMUX_SOCKET" kill-server 2>/dev/null || true

    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        kill "$DAEMON_PID" 2>/dev/null || true
        sleep 0.5
        if kill -0 "$DAEMON_PID" 2>/dev/null; then
            kill -9 "$DAEMON_PID" 2>/dev/null || true
        fi
    fi

    cleanup_home
}
trap cleanup EXIT

capture() {
    local session="$1"
    tmux -L "$TMUX_SOCKET" capture-pane -t "$session" -p -S -2000 \
        2>/dev/null > "$CAPTURE_FILE" || true
}

show_tail() {
    echo "--- capture tail ---"
    tail -n 80 "$CAPTURE_FILE" 2>/dev/null || true
    echo "--- end capture ---"
}

wait_for() {
    local session="$1"
    local pattern="$2"
    local timeout="${3:-45}"
    local elapsed=0

    while [ "$elapsed" -lt "$timeout" ]; do
        capture "$session"
        if grep -qiE "$pattern" "$CAPTURE_FILE"; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    capture "$session"
    return 1
}

assert_contains() {
    local pattern="$1"
    local description="$2"
    if grep -qiE "$pattern" "$CAPTURE_FILE"; then
        pass "$description"
    else
        fail "$description"
        echo "  expected pattern: $pattern"
        show_tail
    fi
}

assert_not_contains() {
    local pattern="$1"
    local description="$2"
    if grep -qiE "$pattern" "$CAPTURE_FILE"; then
        fail "$description"
        echo "  unexpected pattern: $pattern"
        show_tail
    else
        pass "$description"
    fi
}

type_text() {
    local session="$1"
    local text="$2"
    local delay="${3:-0.035}"
    local i char

    for (( i = 0; i < ${#text}; i++ )); do
        char="${text:i:1}"
        if [ "$char" = " " ]; then
            tmux -L "$TMUX_SOCKET" send-keys -t "$session" Space
        else
            tmux -L "$TMUX_SOCKET" send-keys -t "$session" -l "$char"
        fi
        sleep "$delay"
    done
}

send_slash() {
    local session="$1"
    local text="$2"
    local command="${text#/}"

    tmux -L "$TMUX_SOCKET" send-keys -t "$session" C-u
    sleep 0.2
    tmux -L "$TMUX_SOCKET" send-keys -t "$session" -l "/"
    sleep 0.7
    type_text "$session" "$command" 0.035
    tmux -L "$TMUX_SOCKET" send-keys -t "$session" C-m
}

run_and_assert() {
    local session="$1"
    local command="$2"
    local pattern="$3"
    local description="$4"
    local timeout="${5:-45}"

    log "running $command"
    send_slash "$session" "$command"
    if wait_for "$session" "$pattern" "$timeout"; then
        pass "$description"
    else
        fail "$description"
        echo "  command: $command"
        echo "  expected pattern: $pattern"
        show_tail
    fi
}

write_mock_mcp_config() {
    local config_dir="$FRESH_HOME/.kollab/mcp"
    local config_path="$config_dir/mcp_settings.json"
    local server_path="$REPO_ROOT/tests/tmux/mock_mcp_server.sh"
    mkdir -p "$config_dir"

    python - "$config_path" "$server_path" <<'PY'
from pathlib import Path
import json
import sys

path = Path(sys.argv[1])
server = Path(sys.argv[2])
config = {
    "servers": {
        "mock-runtime": {
            "type": "stdio",
            "command": str(server),
            "enabled": True,
            "description": "Runtime smoke mock MCP server",
        }
    }
}
path.write_text(json.dumps(config, indent=2), encoding="utf-8")
PY
}

discover_identity() {
    python - "$FRESH_HOME" <<'PY'
from pathlib import Path
import json
import os
import sys

home = Path(sys.argv[1])
root = home / ".kollab"
paths = list(root.glob("projects/*/hub/presence/*.json"))
paths.extend((root / "hub" / "presence").glob("*.json"))
for path in sorted(paths):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    identity = data.get("identity") or ""
    pid = str(data.get("pid") or "")
    if identity and pid:
        try:
            os.kill(int(pid), 0)
        except OSError:
            continue
        print(f"{identity} {pid}")
        raise SystemExit(0)
raise SystemExit(1)
PY
}

start_daemon_attach() {
    log "starting daemon-backed attach client with isolated HOME"
    tmux -L "$TMUX_SOCKET" new-session -d -s "$DAEMON_SESSION" -x 150 -y 46 \
        "env HOME='$FRESH_HOME' TERM=xterm-256color KOLLAB_NO_AUTO_DETECT=1 KOLLAB_HUB_PROJECT_SCOPED=1 PYTHONPATH='$PYTHONPATH_VALUE' python main.py --daemon --profile default --agent coder"
}

start_reattach() {
    log "reattach: starting second attach client for $IDENTITY"
    tmux -L "$TMUX_SOCKET" new-session -d -s "$REATTACH_SESSION" -x 150 -y 46 \
        "env HOME='$FRESH_HOME' TERM=xterm-256color KOLLAB_NO_AUTO_DETECT=1 KOLLAB_HUB_PROJECT_SCOPED=1 PYTHONPATH='$PYTHONPATH_VALUE' python main.py --attach '$IDENTITY'"
}

echo "============================================================"
echo "Kollab Runtime Smoke"
echo "============================================================"
echo "socket:     $TMUX_SOCKET"
echo "fresh home: $FRESH_HOME"
echo ""

if ! command -v tmux >/dev/null 2>&1; then
    fail "tmux is required"
    exit 1
fi

write_mock_mcp_config
start_daemon_attach

if wait_for "$DAEMON_SESSION" "❯|Ready|attached to|daemon startup failed" 60; then
    pass "daemon attach rendered initial UI"
else
    fail "daemon attach did not render initial UI"
    show_tail
fi

if grep -qi "daemon startup failed" "$CAPTURE_FILE"; then
    fail "daemon startup failed"
    show_tail
else
    pass "daemon startup stayed clean"
fi

for _ in $(seq 1 40); do
    discovered="$(discover_identity 2>/dev/null || true)"
    if [ -n "$discovered" ]; then
        IDENTITY="${discovered% *}"
        DAEMON_PID="${discovered##* }"
        break
    fi
    sleep 0.5
done

if [ -n "$IDENTITY" ] && [ -n "$DAEMON_PID" ]; then
    pass "hub presence discovered identity=$IDENTITY pid=$DAEMON_PID"
else
    fail "hub presence was not discovered"
fi

run_and_assert "$DAEMON_SESSION" "/mcp reload" \
    "MCP Servers Reloaded|Reconnected" \
    "mock MCP reload completed"
run_and_assert "$DAEMON_SESSION" "/mcp test mock-runtime" \
    "Status: CONNECTED|Tools available: [1-9]" \
    "mock MCP server connected through runtime state"
run_and_assert "$DAEMON_SESSION" "/mcp tools mock-runtime" \
    "mock_echo|mock_test" \
    "mock MCP tools rendered"
run_and_assert "$DAEMON_SESSION" "/doctor proof" \
    "kollab doctor:|proof xml|proof native|proof mock-mcp" \
    "/doctor proof rendered tool contracts"
assert_contains "proof xml" "XML tool contract proof visible"
assert_contains "proof native" "native tool contract proof visible"
assert_contains "proof mock-mcp" "MCP tool contract proof visible"

run_and_assert "$DAEMON_SESSION" "/profile default" \
    "Switched to profile: default|Profile not found" \
    "profile command routed through attach"
assert_not_contains "Profile not found" "default profile exists"

run_and_assert "$DAEMON_SESSION" "/agent coder" \
    "Switched to agent: coder|Agent not found" \
    "agent command routed through attach"
assert_not_contains "Agent not found" "coder agent exists"

run_and_assert "$DAEMON_SESSION" "/permissions strict" \
    "Permission mode set to CONFIRM_ALL" \
    "permission strict mode routed through attach"
run_and_assert "$DAEMON_SESSION" "/permissions default" \
    "Permission mode set to DEFAULT" \
    "permission default mode routed through attach"

run_and_assert "$DAEMON_SESSION" "/status" \
    "Daemon PID|Profile|Permissions|Agent" \
    "/status rendered attach runtime state"
tmux -L "$TMUX_SOCKET" send-keys -t "$DAEMON_SESSION" Escape 2>/dev/null || true
sleep 0.5

run_and_assert "$DAEMON_SESSION" "/hub status" \
    "hub: .*agent|delivery trace" \
    "hub status rendered roster and delivery trace"
assert_contains "delivery trace" "delivery trace path visible"

log "reattach: detaching first attach client with ctrl-z"
tmux -L "$TMUX_SOCKET" send-keys -t "$DAEMON_SESSION" C-z
sleep 2

if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
    pass "daemon survived attach detach"
else
    fail "daemon did not survive attach detach"
fi

if [ -n "$IDENTITY" ]; then
    start_reattach
    ACTIVE_SESSION="$REATTACH_SESSION"
    if wait_for "$REATTACH_SESSION" "attached to|❯|Ready" 45; then
        pass "reattach client rendered"
    else
        fail "reattach client did not render"
        show_tail
    fi

    run_and_assert "$REATTACH_SESSION" "/status" \
        "Daemon PID|Profile|Permissions|Agent" \
        "reattach /status stayed coherent"
    tmux -L "$TMUX_SOCKET" send-keys -t "$REATTACH_SESSION" Escape \
        2>/dev/null || true
    sleep 0.5

    run_and_assert "$REATTACH_SESSION" "/hub status" \
        "hub: .*agent|delivery trace" \
        "reattach /hub status stayed coherent"
fi

capture "$ACTIVE_SESSION"
assert_not_contains "Traceback" "no traceback in runtime output"
assert_not_contains "daemon startup failed" "no daemon startup failure in output"

echo ""
show_tail
echo ""
echo "============================================================"
echo "Results: $PASSED/$TOTAL passed, $FAILED failed"
echo "============================================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
