#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT/packages/kollabor-engine/src:$ROOT/packages/kollabor-agent/src:$ROOT/packages/kollabor-ai/src:$ROOT/packages/kollabor-events/src:$ROOT/packages/kollabor-tui/src:$ROOT/packages/kollabor-config/src:$ROOT/packages/kollabor-plugins/src:$ROOT/packages/kollabor-rpc/src:$ROOT"

python -m pytest \
  tests/unit/test_tool_call_contract_golden.py \
  tests/unit/llm/test_native_tools_handler.py \
  tests/unit/mcp/test_mcp_integration.py \
  tests/unit/test_permission_tool_metadata.py \
  tests/unit/test_attach_permission_bridge.py \
  tests/unit/test_attach_startup_order.py \
  tests/unit/test_widget_state.py \
  tests/unit/test_widget_state_refresher.py \
  tests/unit/tui/test_status_widgets_remote_state.py \
  tests/unit/llm/test_agent_hud.py \
  tests/unit/test_hub_wake_order.py \
  tests/unit/test_hub_msg_parsing.py \
  tests/unit/test_hub_mesh_force.py \
  tests/unit/test_hub_identity_mailbox.py \
  tests/unit/test_hub_dns_liveness.py \
  tests/unit/test_hub_delivery_policy.py \
  tests/unit/test_hub_delivery_trace.py \
  tests/unit/test_hub_remote_trust.py \
  tests/unit/test_hub_pending_replies.py \
  tests/unit/test_ghost_response.py \
  tests/unit/tui/test_permission_prompt_render.py \
  tests/test_hub_rpc_integration.py

tests/tmux/fresh_daemon_doctor_smoke.sh
