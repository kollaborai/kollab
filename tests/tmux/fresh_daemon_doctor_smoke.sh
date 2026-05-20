#!/usr/bin/env bash
# Fresh daemon proof: start kollab with an isolated HOME, route /doctor through
# the daemon-backed attach client, and assert the proof contracts render.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

TMUX_SOCKET="kollab-fresh-doctor-$$"
SESSION="fresh-doctor-$$"
FRESH_HOME="$(mktemp -d "${TMPDIR:-/tmp}/kollab-fresh-home.XXXXXX")"
CAPTURE_FILE="$FRESH_HOME/capture.txt"
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

cleanup() {
    tmux -L "$TMUX_SOCKET" send-keys -t "$SESSION" C-c 2>/dev/null || true
    sleep 0.5
    tmux -L "$TMUX_SOCKET" kill-server 2>/dev/null || true
}
trap cleanup EXIT

echo "============================================================"
echo "Fresh Daemon Doctor Smoke"
echo "============================================================"
echo "socket:     $TMUX_SOCKET"
echo "fresh home: $FRESH_HOME"
echo ""

if ! command -v tmux >/dev/null 2>&1; then
    fail "tmux is required"
    exit 1
fi

log "starting daemon-backed attach client with isolated HOME"
tmux -L "$TMUX_SOCKET" new-session -d -s "$SESSION" -x 140 -y 42 \
    "HOME='$FRESH_HOME' TERM=xterm-256color python main.py --daemon"

log "waiting for first render"
for _ in $(seq 1 40); do
    tmux -L "$TMUX_SOCKET" capture-pane -t "$SESSION" -p -S -2000 2>/dev/null > "$CAPTURE_FILE" || true
    if grep -qi "❯\\|Ready\\|daemon startup failed" "$CAPTURE_FILE"; then
        break
    fi
    sleep 0.5
done

if grep -qi "daemon startup failed" "$CAPTURE_FILE"; then
    fail "daemon startup failed"
    sed -n '1,120p' "$CAPTURE_FILE"
    exit 1
fi

tmux -L "$TMUX_SOCKET" send-keys -t "$SESSION" -l "/doctor"
tmux -L "$TMUX_SOCKET" send-keys -t "$SESSION" C-m

log "waiting for doctor report"
DOCTOR_SEEN=0
for _ in $(seq 1 60); do
    tmux -L "$TMUX_SOCKET" capture-pane -t "$SESSION" -p -S -2000 2>/dev/null > "$CAPTURE_FILE" || true
    if grep -q "kollab doctor:" "$CAPTURE_FILE"; then
        DOCTOR_SEEN=1
        break
    fi
    sleep 0.5
done

if [ "$DOCTOR_SEEN" -eq 1 ]; then
    pass "doctor report rendered through daemon attach path"
else
    fail "doctor report did not render"
fi

if grep -q "proof read" "$CAPTURE_FILE"; then
    pass "proof read check rendered"
else
    fail "proof read check missing"
fi

if grep -q "proof xml" "$CAPTURE_FILE"; then
    pass "xml proof rendered"
else
    fail "xml proof missing"
fi

if grep -q "proof native" "$CAPTURE_FILE"; then
    pass "native proof rendered"
else
    fail "native proof missing"
fi

if grep -q "proof mock-mcp" "$CAPTURE_FILE"; then
    pass "mock MCP proof rendered"
else
    fail "mock MCP proof missing"
fi

if grep -qi "Traceback" "$CAPTURE_FILE"; then
    fail "traceback appeared in runtime output"
else
    pass "no traceback in runtime output"
fi

if grep -qi "daemon startup failed" "$CAPTURE_FILE"; then
    fail "daemon startup failure appeared"
else
    pass "daemon startup stayed clean"
fi

echo ""
echo "--- capture tail ---"
tail -n 60 "$CAPTURE_FILE"
echo "--- end capture ---"
echo ""

echo "============================================================"
echo "Results: $PASSED/$TOTAL passed, $FAILED failed"
echo "============================================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
