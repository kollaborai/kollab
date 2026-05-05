#!/usr/bin/env bash
# Manual test for system message injector fix
# This script launches kollab and waits for you to manually test

SESSION_NAME="manual-test-sysmsg"
PROJECT_DIR="/path/to/kollab"

echo "=========================================="
echo "Manual Test: System Message Injector Fix"
echo "=========================================="
echo ""
echo "This test will launch kollab in a tmux session."
echo "You should:"
echo "  1. Type: 'Tell me about sub agents'"
echo "  2. Verify you see a small orange indicator: '[i] agent orchestration skill triggered'"
echo "  3. Verify you DO NOT see 95 lines of system instructions"
echo ""
echo "Press Enter to start..."
read

# Kill existing session
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

# Create new session
echo "Launching kollab in tmux session: $SESSION_NAME"
cd "$PROJECT_DIR"
tmux new-session -d -s "$SESSION_NAME" "cd $PROJECT_DIR && python main.py"

echo ""
echo "Attaching to session. When done, press Ctrl-B then D to detach."
echo "To completely exit, use Ctrl-C in kollab or run: tmux kill-session -t $SESSION_NAME"
echo ""
sleep 2

tmux attach-session -t "$SESSION_NAME"
