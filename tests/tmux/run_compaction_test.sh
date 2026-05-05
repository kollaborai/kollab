#!/usr/bin/env bash
# Real end-to-end context compaction test
# Sends 17 messages to the app via tmux (char-by-char like test_runner),
# waits for compaction to trigger, then verifies the JSONL session log.
#
# Usage: bash tests/tmux/run_compaction_test.sh

set -euo pipefail

PROJ_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SOCKET_NAME="compaction-$$"
SESSION_NAME="compact-test-$$"
LOG_DIR="$HOME/.kollab/projects"
TERM_WIDTH=140
TERM_HEIGHT=40
KEY_DELAY=0.15
CHAR_DELAY=0.03
MSG_WAIT=15
RESULT=0
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

cleanup() {
    echo ""
    echo "cleaning up..."
    tmux -L "$SOCKET_NAME" kill-server 2>&1 || true
}
trap cleanup EXIT

# --- helpers ---

type_text() {
    local text="$1"
    for (( i=0; i<${#text}; i++ )); do
        char="${text:$i:1}"
        tmux -L "$SOCKET_NAME" send-keys -t "$SESSION_NAME" -l "$char"
        sleep "$CHAR_DELAY"
    done
    sleep "$KEY_DELAY"
}

send_enter() {
    tmux -L "$SOCKET_NAME" send-keys -t "$SESSION_NAME" Enter
    sleep "$KEY_DELAY"
}

capture() {
    tmux -L "$SOCKET_NAME" capture-pane -t "$SESSION_NAME" -p
}

send_message() {
    local msg="$1"
    local wait="${2:-$MSG_WAIT}"
    type_text "$msg"
    send_enter
    sleep "$wait"
}

assert_contains() {
    local output="$1"
    local pattern="$2"
    local desc="$3"
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    if echo "$output" | grep -qiE "$pattern"; then
        echo "  ✔ $desc"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo "  ✖ $desc (pattern: $pattern)"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        RESULT=1
    fi
}

# --- start ---

echo "=== context compaction real test ==="
echo "socket: $SOCKET_NAME"
echo "session: $SESSION_NAME"
echo "msg wait: ${MSG_WAIT}s per interaction"
echo ""

# Start app
cd "$PROJ_DIR"
tmux -L "$SOCKET_NAME" new-session -d -s "$SESSION_NAME" \
    -x "$TERM_WIDTH" -y "$TERM_HEIGHT" "python main.py"
echo "▶ app starting (8s)..."
sleep 8

OUTPUT=$(capture)
assert_contains "$OUTPUT" "Ready!" "app started"

if ! echo "$OUTPUT" | grep -qiE "Ready!"; then
    echo "✖ app failed to start, aborting"
    echo "$OUTPUT" | tail -10
    exit 1
fi

# Set TRUST_ALL permissions so tool approvals don't block the test
echo "▶ setting permissions to trust mode..."
type_text "/permissions trust"
send_enter
sleep 3
OUTPUT=$(capture)
echo "  permissions set"

# 16 short questions - designed for fast LLM responses
QUESTIONS=(
    "what is 2 plus 2"
    "name three colors"
    "capital of France"
    "name 3 programming languages"
    "what year was python created"
    "name 3 planets"
    "speed of light in km/s"
    "name 3 oceans"
    "who invented the telephone"
    "tallest mountain on earth"
    "name 3 types of trees"
    "boiling point of water in celsius"
    "name 3 musical instruments"
    "what are the 3 primary colors"
    "how many continents are there"
    "what is the largest ocean"
)

echo ""
for i in "${!QUESTIONS[@]}"; do
    n=$((i + 1))
    echo "▶ [$n/16] ${QUESTIONS[$i]}"
    send_message "${QUESTIONS[$i]}" "$MSG_WAIT"

    # Quick status check every 4 interactions
    if (( n % 4 == 0 )); then
        OUTPUT=$(capture)
        CTX=$(echo "$OUTPUT" | grep -o "ctx:[^ ]*" | head -1 || true)
        echo "  status: ${CTX:-no ctx widget}"
    fi
done

echo ""
echo "▶ waiting 25s for compaction background task..."
sleep 25

# Interaction 17 - this triggers LLM_REQUEST_PRE which applies the compaction
echo "▶ [17] post-compaction: what was the first thing I asked you"
send_message "what was the first thing I asked you" 20

echo ""
echo "--- verification ---"

# Capture final state
OUTPUT=$(capture)
echo ""
echo "final screen (last 15 lines):"
echo "$OUTPUT" | tail -15
echo ""

# Check ctx widget shows compaction round
assert_contains "$OUTPUT" "ctx:" "ctx widget visible"

# Check for compaction round indicator (r1)
if echo "$OUTPUT" | grep -qiE "ctx: r[0-9]"; then
    echo "  ✔ compaction round visible in status"
    PASSED_TESTS=$((PASSED_TESTS + 1))
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
elif echo "$OUTPUT" | grep -qiE "ctx: compacting"; then
    echo "  ℹ compaction still in progress"
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    # Wait more
    echo "  waiting 30s more..."
    sleep 30
    OUTPUT=$(capture)
    if echo "$OUTPUT" | grep -qiE "ctx: r[0-9]"; then
        echo "  ✔ compaction round visible after extra wait"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo "  ✖ still not showing round number"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        RESULT=1
    fi
else
    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo "  ✖ compaction round indicator not found"
    FAILED_TESTS=$((FAILED_TESTS + 1))
    RESULT=1
fi

# Check JSONL log
echo ""
echo "--- JSONL verification ---"
ENCODED_PATH=$(echo "$PROJ_DIR" | sed 's|^/||' | tr '/' '_')
CONV_DIR="$LOG_DIR/$ENCODED_PATH/conversations"

if [ ! -d "$CONV_DIR" ]; then
    echo "✖ conversations dir not found: $CONV_DIR"
    # Try alternative encoding
    ALT_DIR=$(find "$LOG_DIR" -type d -name "conversations" 2>&1 | head -1)
    if [ -n "$ALT_DIR" ] && [ -d "$ALT_DIR" ]; then
        echo "  found alt: $ALT_DIR"
        CONV_DIR="$ALT_DIR"
    else
        RESULT=1
    fi
fi

if [ -d "$CONV_DIR" ]; then
    LATEST=$(ls -1t "$CONV_DIR"/*.jsonl 2>/dev/null | head -1 || true)
    if [ -n "$LATEST" ] && [ -f "$LATEST" ]; then
        echo "session file: $(basename "$LATEST")"
        TOTAL_TESTS=$((TOTAL_TESTS + 1))

        if grep -q "context_compaction" "$LATEST" 2>&1; then
            echo "✔ compaction record found in JSONL"
            PASSED_TESTS=$((PASSED_TESTS + 1))
            echo ""
            echo "--- compaction record ---"
            grep "context_compaction" "$LATEST" | python3 -m json.tool 2>&1 || \
                grep "context_compaction" "$LATEST"
            echo "--- end record ---"
        else
            echo "✖ no compaction record in JSONL"
            echo "  last 3 lines:"
            tail -3 "$LATEST"
            FAILED_TESTS=$((FAILED_TESTS + 1))
            RESULT=1
        fi
    else
        echo "✖ no JSONL files found"
        RESULT=1
    fi
fi

# Summary
echo ""
echo "=============================================="
echo "results: $PASSED_TESTS/$TOTAL_TESTS passed"
if [ $RESULT -eq 0 ]; then
    echo "=== PASS ==="
else
    echo "=== FAIL ==="
fi
echo "=============================================="
exit $RESULT
