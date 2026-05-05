#!/bin/bash
# ============================================================================
# Spec Verification Test Template
# ============================================================================
# USAGE: Copy this template and customize for your spec
#
# cp tests/tmux/templates/spec_verification_template.sh \
#    tests/tmux/verify_[spec-name].sh
# chmod +x tests/tmux/verify_[spec-name].sh
#
# IMPORTANT: Uses dynamic socket/session names for parallel-safe testing
# ============================================================================

set -e

# ============================================================================
# CONFIGURATION - Customize these for your spec
# ============================================================================
SPEC_NAME="your-spec-name"              # e.g., "inline-slider", "toggle-widget"
SPEC_FILE="docs/specs/your-spec.md"     # Path to spec document
PHASE="Phase X"                          # e.g., "Phase 3"

# Dynamic names for parallel safety (DO NOT CHANGE)
SOCKET_NAME="kollabor-$$"
SESSION_NAME="verify-${SPEC_NAME}-$$"

# ============================================================================
# SETUP
# ============================================================================
echo "=============================================="
echo "Spec Verification: $SPEC_NAME"
echo "Phase: $PHASE"
echo "Spec: $SPEC_FILE"
echo "=============================================="
echo "Socket: $SOCKET_NAME"
echo "Session: $SESSION_NAME"
echo "PID: $$"
echo ""

# Track results
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Cleanup function - runs on exit
cleanup() {
    echo ""
    echo "Cleaning up tmux session..."
    tmux -L "$SOCKET_NAME" kill-server 2>/dev/null || true
}
trap cleanup EXIT

# Helper: Run a test
run_test() {
    local test_name="$1"
    local expected="$2"
    local actual="$3"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    if echo "$actual" | grep -q "$expected"; then
        echo "[PASS] $test_name"
        PASSED_TESTS=$((PASSED_TESTS + 1))
        return 0
    else
        echo "[FAIL] $test_name"
        echo "  Expected: $expected"
        echo "  Actual (last 5 lines):"
        echo "$actual" | tail -5 | sed 's/^/    /'
        FAILED_TESTS=$((FAILED_TESTS + 1))
        return 1
    fi
}

# Helper: Send keys and wait
send_keys() {
    tmux -L "$SOCKET_NAME" send-keys -t "$SESSION_NAME" "$@"
    sleep 0.3
}

# Helper: Capture current screen
capture() {
    tmux -L "$SOCKET_NAME" capture-pane -t "$SESSION_NAME" -p
}

# ============================================================================
# START APPLICATION
# ============================================================================
echo "Starting application..."
tmux -L "$SOCKET_NAME" new-session -d -s "$SESSION_NAME" -x 120 -y 35 "python main.py"
sleep 3

# Verify app started
if ! tmux -L "$SOCKET_NAME" list-sessions 2>/dev/null | grep -q "$SESSION_NAME"; then
    echo "[FATAL] Application failed to start"
    exit 1
fi
echo "[OK] Application started"
echo ""

# ============================================================================
# REQUIREMENT 1: [CUSTOMIZE - Description of requirement]
# ============================================================================
echo "=== Requirement 1: [Description] ==="

# Send test input
send_keys "your test input here"
send_keys Enter
sleep 0.5

# Capture and verify
OUTPUT=$(capture)
run_test "Requirement 1 description" "expected pattern" "$OUTPUT" || true

# ============================================================================
# REQUIREMENT 2: [CUSTOMIZE - Description of requirement]
# ============================================================================
echo ""
echo "=== Requirement 2: [Description] ==="

# Send test input
send_keys Tab
sleep 0.3

# Capture and verify
OUTPUT=$(capture)
run_test "Requirement 2 description" "expected pattern" "$OUTPUT" || true

# ============================================================================
# REQUIREMENT 3: [CUSTOMIZE - Add more requirements as needed]
# ============================================================================
# echo ""
# echo "=== Requirement 3: [Description] ==="
# send_keys "..."
# OUTPUT=$(capture)
# run_test "Requirement 3" "expected" "$OUTPUT" || true

# ============================================================================
# SUMMARY
# ============================================================================
echo ""
echo "=============================================="
echo "VERIFICATION SUMMARY: $SPEC_NAME"
echo "=============================================="
echo "Total:  $TOTAL_TESTS"
echo "Passed: $PASSED_TESTS"
echo "Failed: $FAILED_TESTS"
echo ""

if [ "$FAILED_TESTS" -eq 0 ]; then
    echo "[PASS] All requirements verified!"
    echo ""
    echo "Ready for: Next phase / Production"
    exit 0
else
    echo "[FAIL] $FAILED_TESTS requirement(s) failed"
    echo ""
    echo "Action needed: Fix failing requirements"
    exit 1
fi
