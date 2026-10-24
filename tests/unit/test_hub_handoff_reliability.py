"""Handoff reliability and failed-dispatch surfacing (issue #283).

A task handed to an agent stays on the ledger as an unacknowledged handoff
until the assignee proves receipt; the supervisor resends once, then alerts.
A send that did not reach its target is told to the sender and listed by
`/hub status`.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.plugin import HubPlugin
from plugins.hub.supervisor import (
    ack_handoff,
    begin_handoff,
    evaluate,
    unacked_handoffs,
)
from plugins.hub.task_ledger import TaskLedger

ROSTER = [
    {"identity": "koordinator", "state": "working", "is_coordinator": True},
    {"identity": "lapis", "state": "idle", "is_coordinator": False},
]


def _kinds(decisions):
    return [d.kind for d in decisions if d.kind.endswith("handoff") or "handoff" in d.kind]


def test_unacked_handoff_is_resent_once_then_alerted_once(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    begin_handoff(ledger, "task0001", "koordinator", "lapis", "build the thing")
    t = time.time()

    assert [c.id for c in unacked_handoffs(ledger)] == ["task0001"]
    # Inside the ack window: nothing happens, the card just stays visible.
    assert _kinds(evaluate(ledger, ROSTER, t + 10)[0]) == []

    retry = [d for d in evaluate(ledger, ROSTER, t + 130)[0] if d.kind == "retry_handoff"]
    assert len(retry) == 1 and retry[0].worker == "lapis"
    assert retry[0].text == "build the thing"

    # The resend restarts the window; no second resend, an alert after it.
    assert _kinds(evaluate(ledger, ROSTER, t + 10)[0]) == []
    kinds = _kinds(evaluate(ledger, ROSTER, t + 130)[0])
    assert kinds == ["alert_handoff_unacked"]
    assert _kinds(evaluate(ledger, ROSTER, t + 400)[0]) == []


def test_acknowledged_handoff_is_left_alone(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    begin_handoff(ledger, "task0001", "koordinator", "lapis", "do it")
    assert ack_handoff(ledger, "task0001", "lapis")
    assert unacked_handoffs(ledger) == []
    assert _kinds(evaluate(ledger, ROSTER, time.time() + 500)[0]) == []


def test_a_worker_checkpoint_counts_as_acknowledgement(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    begin_handoff(ledger, "task0001", "koordinator", "lapis", "do it")
    ledger.checkpoint("task0001", "started")
    assert unacked_handoffs(ledger) == []


def test_handoff_claims_the_open_card_for_the_assignee(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    card = ledger.enqueue(assigner="koordinator", directive="open work")
    got = begin_handoff(ledger, card.id, "koordinator", "lapis", "open work")
    assert got.assignee == "lapis" and got.status == "active"
    assert [c.id for c in unacked_handoffs(ledger)] == [card.id]


def _plugin(tmp_path, local=()):
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="koordinator",
        agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._vault = None
    plugin._relay_agent = None
    plugin._task_ledger = TaskLedger(tasks_dir=str(tmp_path))
    plugin._presence = MagicMock()
    plugin._presence.scan_all_presence.return_value = list(local)
    plugin._presence.discover_agents_async = AsyncMock(return_value=list(local))
    plugin._display_outgoing_message = MagicMock()
    plugin._bridge_forward = AsyncMock()
    return plugin


@pytest.mark.asyncio
async def test_a_tagged_handoff_is_tracked_when_sent(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._route_message = AsyncMock(return_value=[])

    result = await plugin._handle_hub_msg_tool(
        {
            "id": "t1",
            "to": "lapis",
            "content": "please implement the login fix",
            "task_id": "task0001",
        }
    )

    assert result.success
    card = plugin._task_ledger.get("task0001")
    assert card.assignee == "lapis" and card.status == "active"
    assert [c.id for c in unacked_handoffs(plugin._task_ledger)] == ["task0001"]


@pytest.mark.asyncio
async def test_a_rejected_dispatch_is_an_error_and_shows_in_status(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._route_message = AsyncMock(return_value=[("lapis", "in cooldown for 30s")])

    result = await plugin._handle_hub_msg_tool(
        {"id": "t1", "to": "lapis", "content": "ping about the build"}
    )

    assert not result.success
    assert "rejected" in result.error and "lapis" in result.error
    status = "\n".join(plugin._dispatch_failure_lines())
    assert "dispatch failures: 1" in status and "lapis: in cooldown" in status


@pytest.mark.asyncio
async def test_an_offline_target_is_recorded_for_status(tmp_path):
    plugin = _plugin(tmp_path, local=[])
    plugin._route_message = AsyncMock(return_value=[])

    result = await plugin._handle_hub_msg_tool(
        {"id": "t1", "to": "ghost", "content": "anyone home about the build"}
    )

    assert result.output.startswith("warning: 'ghost' is not online.")
    assert "ghost: not online" in "\n".join(plugin._dispatch_failure_lines())


@pytest.mark.asyncio
async def test_a_socket_failure_is_told_to_the_sender(tmp_path):
    lapis = AgentRuntime(name="coder", identity="lapis", agent_id="lapis-id")
    plugin = _plugin(tmp_path, local=[lapis])
    plugin._decide_sender_delivery = MagicMock(
        return_value=SimpleNamespace(mode="allow", reason="", trace_level="info")
    )
    with patch(
        "plugins.hub.plugin.AgentMessenger.send_to_agent",
        AsyncMock(return_value=False),
    ), patch("plugins.hub.plugin.AgentMessenger.send_to_file", AsyncMock()):
        result = await plugin._handle_hub_msg_tool(
            {"id": "t1", "to": "lapis", "content": "ping about the build"}
        )

    assert "socket send failed" in result.output
    assert "delivered to lapis" not in result.output
    assert "lapis: socket send failed" in "\n".join(plugin._dispatch_failure_lines())
