#!/usr/bin/env bash
# Spec verification for docs/specs/webui-trajectory-tab.md.
# Uses a private tmux socket/session so concurrent verification runs do not
# share the user's normal Kollab terminal state.

set -euo pipefail

SPEC_NAME="webui-trajectory-tab"
SOCKET_NAME="kollab-${SPEC_NAME}-$$"
SESSION_NAME="verify-${SPEC_NAME}-$$"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cleanup() {
  tmux -L "$SOCKET_NAME" kill-server >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== Spec Verification: ${SPEC_NAME} ==="
echo "socket=${SOCKET_NAME} session=${SESSION_NAME}"

tmux -L "$SOCKET_NAME" new-session -d -s "$SESSION_NAME" -x 140 -y 40 \
  "cd '$PROJECT_ROOT' && \
   if npm --prefix packages/kollabor-webui/frontend run typecheck && \
      npm --prefix packages/kollabor-webui/frontend run build && \
      python -m pytest tests/unit/test_engine_history_payload.py tests/unit/test_engine_history_usage_metadata.py tests/unit/test_webui_auth_wiring.py -q && \
      python -m py_compile packages/kollabor-agent/src/kollabor_agent/queue_processor.py && \
      python -m py_compile packages/kollabor-engine/src/kollabor_engine/session.py && \
      rg -q 'data-testid=\"trajectory-view\"|streamEvents|turn_complete' packages/kollabor-webui/frontend/src/components/trajectory/TrajectoryView.tsx && \
      rg -q 'metadata=_assistant_history_usage_metadata|assistant_metadata = _assistant_history_usage_metadata' packages/kollabor-agent/src/kollabor_agent/queue_processor.py && \
      rg -q 'tool_calls|tool_call_id' packages/kollabor-webui/frontend/src/runtime.tsx && \
      rg -q 'to_dict|metadata.*thinking' packages/kollabor-engine/src/kollabor_engine/session.py && \
      rg -q 'h-svh.*max-h-svh.*overflow-hidden' packages/kollabor-webui/frontend/src/App.tsx; then \
     echo WEBUI_TRAJECTORY_SPEC_VERIFY_PASS; \
   else \
     echo WEBUI_TRAJECTORY_SPEC_VERIFY_FAIL; \
   fi; \
   sleep 300"

for _ in $(seq 1 180); do
  output="$(tmux -L "$SOCKET_NAME" capture-pane -t "$SESSION_NAME" -p 2>/dev/null || true)"
  if grep -q "WEBUI_TRAJECTORY_SPEC_VERIFY_PASS" <<<"$output"; then
    echo "$output"
    echo "[PASS] Phase 1/2 source, typecheck, build, auth, and usage gates"
    exit 0
  fi
  if grep -q "WEBUI_TRAJECTORY_SPEC_VERIFY_FAIL" <<<"$output"; then
    echo "$output"
    echo "[FAIL] verification command reported an error"
    exit 1
  fi
  if grep -q "Error\|FAILED\|error TS" <<<"$output"; then
    echo "$output"
    echo "[FAIL] verification command reported an error"
    exit 1
  fi
  sleep 0.5
done

tmux -L "$SOCKET_NAME" capture-pane -t "$SESSION_NAME" -p || true
echo "[FAIL] verification timed out"
exit 1
