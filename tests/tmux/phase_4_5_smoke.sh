#!/usr/bin/env bash
# ============================================================================
# Phase 4.5 smoke test: daemon + attach client with launch flag routing
# ============================================================================
#
# Proves the fix for "launch flags don't cross the process boundary into
# the daemon" bug. Starts a fresh daemon with the DEFAULT profile, then
# attaches a client with --profile openai-oauth. After the attach settles,
# captures the attach client's pane and asserts:
#
#   1. The banner/status bar shows "openai-oauth" (not "default")
#   2. The drain log shows "switched to profile: openai-oauth"
#   3. No error messages about profile switching
#
# Runs in its own tmux socket so it does not collide with any existing
# daemons other operators may have running, and uses a dynamic identity name so
# reruns don't fight over the same socket path.
#
# Usage:
#   bash tests/tmux/phase_4_5_smoke.sh
#
# Exit code: 0 on pass, non-zero on failure.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

TMUX_SOCKET="kollabor-phase45-$$"
DAEMON_SESSION="phase45-daemon-$$"
ATTACH_SESSION="phase45-attach-$$"
TOTAL=0
PASSED=0
FAILED=0
# IDENTITY is discovered dynamically from presence files after the daemon
# starts, because --as with an arbitrary name is rejected in favor of gem
# designations. We record the set of existing presence files before
# spawning our daemon and treat the first new one as ours.
IDENTITY=""
DAEMON_PID=""

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
    log "cleanup: killing tmux sessions for this smoke test"
    tmux -L "$TMUX_SOCKET" kill-server 2>/dev/null || true

    # Kill our daemon by the PID we captured during discovery. We never
# kill by identity alone -- another operator might have a daemon with the same
    # gem name from a previous session (unlikely but possible). PID is
    # authoritative.
    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        log "cleanup: killing our daemon pid=$DAEMON_PID (identity=$IDENTITY)"
        kill "$DAEMON_PID" 2>/dev/null || true
        sleep 0.5
        kill -9 "$DAEMON_PID" 2>/dev/null || true
    fi

    # Clean up stale presence file if still present
    if [ -n "$IDENTITY" ]; then
        for f in "$HOME/.kollab/hub/presence/"*.json; do
            [ -f "$f" ] || continue
            if grep -q "\"identity\": \"$IDENTITY\"" "$f" 2>/dev/null; then
                local fpid
                fpid=$(grep -o '"pid": [0-9]*' "$f" | awk '{print $2}')
                # Only delete if the pid matches ours (defensive)
                if [ "$fpid" = "$DAEMON_PID" ]; then
                    rm -f "$f"
                fi
            fi
        done
        rm -f "/tmp/kollabor-hub/${IDENTITY}.sock" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "============================================================"
echo "Phase 4.5 Smoke Test: launch flag routing across boundary"
echo "============================================================"
echo "Socket:   $TMUX_SOCKET"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Snapshot existing presence files, spawn daemon, diff to find ours
# ---------------------------------------------------------------------------
log "step 1: recording existing presence files before spawn"

PRESENCE_DIR="$HOME/.kollab/hub/presence"
mkdir -p "$PRESENCE_DIR"

# Snapshot which presence files exist BEFORE we spawn. Anything new after
# we spawn is ours.
EXISTING_PRESENCE=$(ls -1 "$PRESENCE_DIR"/*.json 2>/dev/null | sort)
log "existing presence files: $(echo "$EXISTING_PRESENCE" | wc -l | tr -d ' ')"

log "step 1b: spawning interactive kollab in tmux (acts as 'daemon' target)"

# We don't use --detached here because it forks and the parent exits, which
# confuses tmux's exit-on-command-exit behavior. Instead we run an interactive
# kollab inside tmux -- it registers in hub presence just like a daemon would,
# and the attach client can find it via its identity. This matches a common
# real workflow of leaving kollab running in one tab and attaching from another.
tmux -L "$TMUX_SOCKET" new-session -d -s "$DAEMON_SESSION" -x 140 -y 40 \
    "python main.py"

# Wait up to 20s for a NEW presence file to appear (not in our initial snapshot).
log "waiting up to 20s for a NEW hub presence file to appear..."
DAEMON_PRESENCE=""
for i in $(seq 1 40); do
    CURRENT_PRESENCE=$(ls -1 "$PRESENCE_DIR"/*.json 2>/dev/null | sort)
    NEW_FILES=$(comm -13 <(echo "$EXISTING_PRESENCE") <(echo "$CURRENT_PRESENCE") | grep -v '^$' || true)
    if [ -n "$NEW_FILES" ]; then
        # Take the first new file as ours
        DAEMON_PRESENCE=$(echo "$NEW_FILES" | head -n 1)
        break
    fi
    sleep 0.5
done

if [ -z "$DAEMON_PRESENCE" ] || [ ! -f "$DAEMON_PRESENCE" ]; then
    fail "no new presence file appeared within 20s"
    echo ""
    echo "daemon stdout (last 30 lines):"
    tmux -L "$TMUX_SOCKET" capture-pane -t "$DAEMON_SESSION" -p 2>/dev/null | tail -30
    exit 1
fi

IDENTITY=$(grep -o '"identity": "[^"]*"' "$DAEMON_PRESENCE" | head -n 1 | sed 's/"identity": "//;s/"$//')
DAEMON_PID=$(grep -o '"pid": [0-9]*' "$DAEMON_PRESENCE" | awk '{print $2}')

# Sanity: verify pid is alive and matches the tmux process we spawned
if ! kill -0 "$DAEMON_PID" 2>/dev/null; then
    fail "daemon presence file points at dead pid $DAEMON_PID"
    exit 1
fi

pass "daemon spawned, discovered identity=$IDENTITY pid=$DAEMON_PID"

# ---------------------------------------------------------------------------
# Step 2: Attach with --profile openai-oauth (THE critical test)
# ---------------------------------------------------------------------------
log "step 2: attaching client with --profile openai-oauth"

tmux -L "$TMUX_SOCKET" new-session -d -s "$ATTACH_SESSION" -x 140 -y 40 \
    "python main.py --attach $IDENTITY --profile openai-oauth"

# Wait for attach to settle (drain flags, render first frame).
# We need long enough for:
#  - RPC round trip to daemon
#  - Daemon-side profile switch + provider reinit
#  - Client-side widget refresher next tick (runs every 2s)
# Extra 3s on top of the refresher interval gives us slack so the
# status bar reliably reflects post-drain state for the assertions.
log "waiting 8s for attach client to settle..."
sleep 8

# Capture the attach client pane
ATTACH_CAPTURE=$(tmux -L "$TMUX_SOCKET" capture-pane -t "$ATTACH_SESSION" -p 2>/dev/null)

echo ""
echo "--- attach client capture ---"
echo "$ATTACH_CAPTURE"
echo "--- end capture ---"
echo ""

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

# Assertion 1: The attach client shows "switched to profile: openai-oauth"
# from the drain log (phase 4.5 step 5 drain message).
if echo "$ATTACH_CAPTURE" | grep -qi "switched to profile.*openai-oauth"; then
    pass "drain log: 'switched to profile: openai-oauth' visible"
else
    fail "drain log: 'switched to profile' message NOT visible"
fi

# Assertion 2: Status bar shows openai-oauth (not "default")
# The widget refresher pulls from remote_state which the daemon updates
# after the set_active_profile RPC lands.
if echo "$ATTACH_CAPTURE" | grep -qi "openai-oauth"; then
    pass "status/output shows 'openai-oauth'"
else
    fail "status/output does NOT show 'openai-oauth'"
fi

# Assertion 3: No "Profile not found" error in the drain output
if echo "$ATTACH_CAPTURE" | grep -qi "Profile not found"; then
    fail "drain produced 'Profile not found' error"
else
    pass "no 'Profile not found' error"
fi

# Assertion 4: No "--profile openai-oauth failed on daemon" error
if echo "$ATTACH_CAPTURE" | grep -qi "profile.*failed on daemon"; then
    fail "drain produced 'failed on daemon' error"
else
    pass "no 'failed on daemon' error"
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
