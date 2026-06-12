import time

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.delivery import DeliveryTrace
from plugins.hub.dns.models import AgentRecord
from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger


class FakePresence:
    def __init__(self, agents):
        self._agents = agents

    def get_cached_agents(self):
        return list(self._agents)


class FakeDnsRegistry:
    def __init__(self, records):
        self._records = records

    def get_all(self):
        return list(self._records)


def test_task_assignment_creates_expected_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))

    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    pending = ledger.pending_replies()

    assert len(pending) == 1
    assert pending[0]["task_id"] == "review-agent-hud"
    assert pending[0]["assignee"] == "lapis"


def test_completion_report_resolves_expected_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="sapphire",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    resolved = ledger.resolve_reply(
        assignee="sapphire",
        evidence="VERDICT: ship-ready, no blockers",
        message_id="msg-2",
    )

    assert resolved is True
    assert ledger.pending_replies() == []


def test_ack_does_not_resolve_expected_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="sapphire",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    resolved = ledger.resolve_reply(
        assignee="sapphire",
        evidence="standing by",
        message_id="msg-2",
    )

    assert resolved is False
    assert len(ledger.pending_replies()) == 1


def test_hub_status_includes_cockpit_counts(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="task-1",
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = None
    plugin._task_ledger = ledger
    plugin._work_queue = None

    status = plugin._format_status()

    assert "pending replies: 1" in status
    assert "delivery trace:" in status


def test_hub_status_includes_full_cockpit_sections(tmp_path):
    ledger = TaskLedger(str(tmp_path / "tasks"))
    card = ledger.create(
        assigner="koordinator",
        assignee="lapis",
        directive="review hub delivery truth",
        priority=2,
    )
    ledger.expect_reply(
        task_id=card.id,
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    trace = DeliveryTrace(tmp_path / "delivery_trace.jsonl")
    trace.record(
        message_id="msg-1",
        event="route_started",
        sender="koordinator",
        target="lapis",
        detail="message",
    )
    trace.record(
        message_id="msg-1",
        event="socket_send_succeeded",
        sender="koordinator",
        target="lapis",
        detail="/tmp/lapis.sock",
    )
    trace.record(
        message_id="msg-2",
        event="quarantined",
        sender="remote-lapis",
        target="koordinator",
        detail="remote unknown sender",
    )
    trace.record(
        message_id="msg-3",
        event="rejected",
        sender="rogue",
        target="mesh",
        detail="sender rejected",
    )
    trace.record(
        message_id="msg-4",
        event="queued_identity_mailbox",
        sender="koordinator",
        target="sapphire",
        detail="offline direct target",
    )

    now = time.time()
    identity = AgentRuntime(
        name="coder",
        identity="koordinator",
        agent_id="agent-koordinator",
        state="idle",
        is_coordinator=True,
        project="/repo",
    )
    peer = AgentRuntime(
        name="coder",
        identity="lapis",
        agent_id="agent-lapis",
        state="working",
        current_task="review hub delivery truth",
        project="/repo",
        last_heartbeat=now - 7,
    )
    records = [
        AgentRecord(
            designation="koordinator",
            approval_state="approved",
            is_coordinator=True,
            endpoint_state="fresh",
            state="idle",
        ),
        AgentRecord(
            designation="lapis",
            approval_state="approved",
            endpoint_state="stale",
            state="offline",
            current_task="review hub delivery truth",
            last_endpoint_seen=now - 125,
        ),
        AgentRecord(
            designation="rogue",
            approval_state="rejected",
            endpoint_state="stale",
            state="offline",
            last_endpoint_seen=now - 300,
        ),
    ]

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = identity
    plugin._presence = FakePresence([peer])
    plugin._task_ledger = ledger
    plugin._work_queue = None
    plugin._dns_registry = FakeDnsRegistry(records)
    plugin._hub_delivery_trace = trace

    status = plugin._format_status()

    assert "cockpit:" in status
    assert "roster: 2 agent(s)" in status
    assert "coordinator: koordinator" in status
    assert "expected replies: 1 pending" in status
    assert f"msg-1 task {card.id} -> lapis" in status
    assert "current assignments: 1 active" in status
    assert f"{card.id} koordinator->lapis" in status
    assert "stale endpoints: 2" in status
    assert "lapis: stale" in status
    assert "rogue: stale, rejected" in status
    assert "delivery decisions: 5 event(s)" in status
    assert "quarantined=1" in status
    assert "rejected=1" in status
    assert "queued=1" in status
    assert "recent decisions:" in status
    assert "quarantined msg-2 remote-lapis->koordinator: remote unknown sender" in status
    assert f"delivery trace: {trace.path}" in status
