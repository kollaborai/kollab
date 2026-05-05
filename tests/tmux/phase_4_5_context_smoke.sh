#!/usr/bin/env bash
# ============================================================================
# Phase 4.5 step 6 smoke test: --context launch flag + ContextRegistry
# ============================================================================
#
# Proves the multi-context daemon works end-to-end:
#   1. Spawn a fresh kollab in tmux. Registers an empty ContextRegistry
#      with 'main' as the live context.
#   2. Spawn attach client with --context feature-tmux-test. The context
#      doesn't exist yet, so the drain should create it + attach.
#   3. Capture attach client pane and assert:
#        - drain says "attached to context: feature-tmux-test"
#        - no 'failed on daemon' error
#   4. Verify the context registry file on disk contains both contexts.
#
# Runs in its own tmux socket and cleans up only its own daemon.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

TMUX_SOCKET="kollabor-phase45ctx-$$"
DAEMON_SESSION="phase45ctx-daemon-$$"
ATTACH_SESSION="phase45ctx-attach-$$"
TOTAL=0
PASSED=0
FAILED=0
IDENTITY=""
DAEMON_PID=""
CONTEXT_NAME="feature-tmux-test"

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
    log "cleanup: killing tmux sessions"
    tmux -L "$TMUX_SOCKET" kill-server 2>/dev/null || true

    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        log "cleanup: killing our daemon pid=$DAEMON_PID (identity=$IDENTITY)"
        kill "$DAEMON_PID" 2>/dev/null || true
        sleep 0.5
        kill -9 "$DAEMON_PID" 2>/dev/null || true
    fi

    if [ -n "$IDENTITY" ]; then
        for f in "$HOME/.kollab/hub/presence/"*.json; do
            [ -f "$f" ] || continue
            if grep -q "\"identity\": \"$IDENTITY\"" "$f" 2>/dev/null; then
                local fpid
                fpid=$(grep -o '"pid": [0-9]*' "$f" | awk '{print $2}')
                if [ "$fpid" = "$DAEMON_PID" ]; then
                    rm -f "$f"
                fi
            fi
        done
        rm -f "/tmp/kollabor-hub/${IDENTITY}.sock" 2>/dev/null || true

        # Clean up context file we may have created
        local ctx_file="$HOME/.kollab/hub/contexts/${IDENTITY}.json"
        if [ -f "$ctx_file" ]; then
            log "cleanup: removing test context file $ctx_file"
            rm -f "$ctx_file"
        fi
    fi
}
trap cleanup EXIT

echo "============================================================"
echo "Phase 4.5 Step 6 Smoke Test: --context launch flag"
echo "============================================================"
echo "Socket:        $TMUX_SOCKET"
echo "Context name:  $CONTEXT_NAME"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Start daemon
# ---------------------------------------------------------------------------
log "step 1: recording existing presence files"

PRESENCE_DIR="$HOME/.kollab/hub/presence"
mkdir -p "$PRESENCE_DIR"
EXISTING_PRESENCE=$(ls -1 "$PRESENCE_DIR"/*.json 2>/dev/null | sort)

log "step 1b: spawning interactive kollab in tmux"
tmux -L "$TMUX_SOCKET" new-session -d -s "$DAEMON_SESSION" -x 140 -y 40 \
    "python main.py"

log "waiting up to 20s for hub presence file..."
DAEMON_PRESENCE=""
for i in $(seq 1 40); do
    CURRENT_PRESENCE=$(ls -1 "$PRESENCE_DIR"/*.json 2>/dev/null | sort)
    NEW_FILES=$(comm -13 <(echo "$EXISTING_PRESENCE") <(echo "$CURRENT_PRESENCE") | grep -v '^$' || true)
    if [ -n "$NEW_FILES" ]; then
        DAEMON_PRESENCE=$(echo "$NEW_FILES" | head -n 1)
        break
    fi
    sleep 0.5
done

if [ -z "$DAEMON_PRESENCE" ] || [ ! -f "$DAEMON_PRESENCE" ]; then
    fail "no new presence file after 20s"
    exit 1
fi

IDENTITY=$(grep -o '"identity": "[^"]*"' "$DAEMON_PRESENCE" | head -n 1 | sed 's/"identity": "//;s/"$//')
DAEMON_PID=$(grep -o '"pid": [0-9]*' "$DAEMON_PRESENCE" | awk '{print $2}')

if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    fail "daemon presence points at dead pid $DAEMON_PID"
    exit 1
fi

pass "daemon spawned, identity=$IDENTITY pid=$DAEMON_PID"

# ---------------------------------------------------------------------------
# Step 2: Attach with --context NAME (context doesn't exist yet)
# ---------------------------------------------------------------------------
log "step 2: attaching with --context $CONTEXT_NAME"

tmux -L "$TMUX_SOCKET" new-session -d -s "$ATTACH_SESSION" -x 140 -y 40 \
    "python main.py --attach $IDENTITY --context $CONTEXT_NAME"

log "waiting 10s for drain + refresh..."
sleep 10

ATTACH_CAPTURE=$(tmux -L "$TMUX_SOCKET" capture-pane -t "$ATTACH_SESSION" -p 2>/dev/null)

echo ""
echo "--- attach client capture ---"
echo "$ATTACH_CAPTURE"
echo "--- end capture ---"
echo ""

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

# Assertion 1: Drain message mentions attaching to our context
if echo "$ATTACH_CAPTURE" | grep -qi "attached to context.*$CONTEXT_NAME"; then
    pass "drain log: 'attached to context: $CONTEXT_NAME' visible"
else
    fail "drain log: context attach message NOT visible"
fi

# Assertion 2: No 'failed on daemon' for --context
if echo "$ATTACH_CAPTURE" | grep -qi "context.*failed on daemon"; then
    fail "drain produced 'failed on daemon' error for --context"
else
    pass "no 'failed on daemon' error for --context"
fi

# Assertion 3: Context registry file exists on disk with our identity
CONTEXT_FILE="$HOME/.kollab/hub/contexts/${IDENTITY}.json"
if [ -f "$CONTEXT_FILE" ]; then
    pass "context registry file exists at $CONTEXT_FILE"
else
    fail "context registry file does NOT exist at $CONTEXT_FILE"
fi

# Assertion 4: Registry file contains both 'main' and the test context
if [ -f "$CONTEXT_FILE" ]; then
    if grep -q "\"name\": \"main\"" "$CONTEXT_FILE" 2>/dev/null && \
       grep -q "\"name\": \"$CONTEXT_NAME\"" "$CONTEXT_FILE" 2>/dev/null; then
        pass "registry file contains both 'main' and '$CONTEXT_NAME'"
    else
        fail "registry file missing expected context names"
        echo "    file contents:"
        cat "$CONTEXT_FILE" | head -30 | sed 's/^/      /'
    fi
fi

# Assertion 5: Active context in the file is our test context
if [ -f "$CONTEXT_FILE" ]; then
    ACTIVE=$(grep -o '"active": "[^"]*"' "$CONTEXT_FILE" | head -n 1 | sed 's/"active": "//;s/"$//')
    if [ "$ACTIVE" = "$CONTEXT_NAME" ]; then
        pass "active context in file is '$CONTEXT_NAME'"
    else
        fail "active context in file is '$ACTIVE', expected '$CONTEXT_NAME'"
    fi
fi

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "Results: $PASSED/$TOTAL passed, $FAILED failed"
echo "============================================================"

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
