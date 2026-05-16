import time

from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.registry import AgentRegistry
from plugins.hub.dns.storage import DNSStorage


def test_liveness_marks_approved_record_stale_without_deleting(tmp_path):
    registry = AgentRegistry(DNSStorage(tmp_path))
    registry.register(
        AgentRecord(
            designation="sapphire",
            agent_id="old-session-id",
            approval_state="approved",
            last_seen=time.time() - 120,
            ttl=1,
            socket_path="/tmp/missing.sock",
        )
    )
    record = registry.resolve("sapphire")
    assert record is not None
    record.last_seen = time.time() - 120
    registry.save()

    removed = registry.refresh_liveness([])
    record = registry.resolve("sapphire")

    assert removed == 0
    assert record is not None
    assert record.approval_state == "approved"
    assert record.state == "offline"
    assert record.endpoint_state == "stale"
    assert record.tags["endpoint_state"] == "stale"


def test_liveness_keeps_rejected_record_for_audit(tmp_path):
    registry = AgentRegistry(DNSStorage(tmp_path))
    registry.register(
        AgentRecord(
            designation="bad-agent",
            agent_id="bad-session",
            approval_state="rejected",
            last_seen=time.time() - 120,
            ttl=1,
        )
    )
    record = registry.resolve("bad-agent")
    assert record is not None
    record.last_seen = time.time() - 120
    registry.save()

    removed = registry.refresh_liveness([])
    record = registry.resolve("bad-agent")

    assert removed == 0
    assert record is not None
    assert record.approval_state == "rejected"
    assert record.endpoint_state == "stale"
    assert record.tags["endpoint_state"] == "stale"


def test_liveness_can_remove_stale_pending_record(tmp_path):
    registry = AgentRegistry(DNSStorage(tmp_path))
    registry.register(
        AgentRecord(
            designation="pending-agent",
            agent_id="pending-session",
            approval_state="pending",
            last_seen=time.time() - 120,
            ttl=1,
        )
    )
    record = registry.resolve("pending-agent")
    assert record is not None
    record.last_seen = time.time() - 120
    registry.save()

    removed = registry.refresh_liveness([])

    assert removed == 1
    assert registry.resolve("pending-agent") is None
