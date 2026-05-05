"""Unit tests for the phase D hub bridge.

Covers the 9 cases from docs/architecture/rfcs/RFC-2026-04-13-context-service-phase-d-hub-bridge.md:
  1. broadcast on new entry sets hub_shared
  2. no broadcast when broadcaster is None
  3. divergent hash detection queues a warning
  4. same hash does not queue a warning
  5. hub_ask_ctx with matching filter returns filtered entries
  6. hub_ask_ctx with unknown peer returns graceful message
  7. peer-aware context filter returns only shared entries
  8. waiting agent skips broadcast
  9. hub_holders does not duplicate when same peer broadcasts twice
"""

import asyncio

from kollabor_ai.context_service.hub_bridge import (
    HubBridge,
)
from kollabor_ai.context_service.service import ContextService


def _build_entry(svc: ContextService, path: str, content: bytes):
    """Helper: ingest a file_read entry and return it."""
    return svc.ingest_heavy_item(
        kind="file_read",
        tool="read",
        label=path,
        content=content,
        message_uuid="msg-1",
        file_path=path,
        file_version=1,
    )


def _run(coro):
    """Run a coroutine on a fresh loop for sync tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_broadcast_sets_hub_shared_flag():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    sent = []

    async def broadcaster(payload):
        sent.append(payload)

    bridge = HubBridge(
        identity="koordinator",
        context_service=svc,
        broadcaster=broadcaster,
    )
    svc.set_hub_bridge(bridge)

    entry = _build_entry(svc, "/tmp/foo.py", b"x" * 9_000)

    _run(bridge.on_ledger_event(entry))

    assert entry.hub_shared is True
    assert len(sent) == 1
    assert sent[0]["type"] == "context_ledger_update"
    assert sent[0]["source"] == "koordinator"
    assert sent[0]["entry"]["file_path"] == "/tmp/foo.py"


def test_no_broadcast_when_broadcaster_none():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    bridge = HubBridge(
        identity="lapis", context_service=svc, broadcaster=None
    )
    entry = _build_entry(svc, "/tmp/bar.py", b"y" * 9_000)

    _run(bridge.on_ledger_event(entry))

    assert entry.hub_shared is False


def test_divergent_hash_queues_warning():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    our_entry = _build_entry(svc, "/tmp/a.py", b"local" * 2000)
    bridge = HubBridge(identity="me", context_service=svc)

    bridge.on_peer_broadcast(
        {
            "type": "context_ledger_update",
            "source": "lapis",
            "entry": {
                "ctx_id": "ctx-peer-1",
                "content_hash": "different_hash",
                "file_path": "/tmp/a.py",
                "file_version": 2,
                "size_kb": 10,
                "decision": "pending",
            },
            "timestamp": "2026-04-20T00:00:00",
        }
    )

    warning_block = svc.build_divergence_warnings()
    assert warning_block is not None
    assert "/tmp/a.py" in warning_block
    assert "lapis" in warning_block
    assert "lapis" in our_entry.hub_holders


def test_same_hash_no_warning():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    our_entry = _build_entry(svc, "/tmp/b.py", b"same" * 3000)
    bridge = HubBridge(identity="me", context_service=svc)

    bridge.on_peer_broadcast(
        {
            "type": "context_ledger_update",
            "source": "ruby",
            "entry": {
                "ctx_id": "ctx-peer-2",
                "content_hash": our_entry.content_hash,
                "file_path": "/tmp/b.py",
                "file_version": 1,
                "size_kb": 12,
                "decision": "pending",
            },
            "timestamp": "2026-04-20T00:00:00",
        }
    )

    assert svc.build_divergence_warnings() is None


def test_hub_ask_ctx_with_matching_filter():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    bridge = HubBridge(identity="me", context_service=svc)

    for path, ctx_id in (
        ("/tmp/kollabor/a.py", "ctx-1"),
        ("/tmp/kollabor/b.py", "ctx-2"),
        ("/tmp/other/c.py", "ctx-3"),
    ):
        bridge.on_peer_broadcast(
            {
                "source": "lapis",
                "entry": {
                    "ctx_id": ctx_id,
                    "content_hash": f"h-{ctx_id}",
                    "file_path": path,
                    "file_version": 1,
                    "size_kb": 5,
                    "decision": "pending",
                },
            }
        )

    summary = bridge.handle_hub_ask_ctx("lapis", "file:/tmp/kollabor/")
    assert "/tmp/kollabor/a.py" in summary
    assert "/tmp/kollabor/b.py" in summary
    assert "/tmp/other/c.py" not in summary


def test_hub_ask_ctx_unknown_peer():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    bridge = HubBridge(identity="me", context_service=svc)
    result = bridge.handle_hub_ask_ctx("nobody")
    assert "no context data" in result


def test_peer_aware_context_filter():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    entry_a = _build_entry(svc, "/tmp/shared.py", b"s" * 9_000)
    _build_entry(svc, "/tmp/local.py", b"l" * 9_000)
    entry_a.hub_holders.append("lapis")

    svc.request_context_snapshot(filter_spec="peer:lapis")
    snapshot = svc.build_context_snapshot()

    assert snapshot is not None
    assert "/tmp/shared.py" in snapshot
    assert "/tmp/local.py" not in snapshot


def test_waiting_agent_skips_broadcast():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    sent = []

    async def broadcaster(payload):
        sent.append(payload)

    bridge = HubBridge(
        identity="me",
        context_service=svc,
        broadcaster=broadcaster,
        is_waiting=lambda: True,
    )
    entry = _build_entry(svc, "/tmp/w.py", b"z" * 9_000)
    _run(bridge.on_ledger_event(entry))

    assert entry.hub_shared is False
    assert sent == []


def test_hub_holders_no_duplicate_on_repeat_broadcast():
    svc = ContextService(heavy_threshold_kb=0, curate_threshold_kb=10_000)
    our_entry = _build_entry(svc, "/tmp/dup.py", b"orig" * 2000)
    bridge = HubBridge(identity="me", context_service=svc)

    payload = {
        "source": "lapis",
        "entry": {
            "ctx_id": "ctx-dup",
            "content_hash": "different",
            "file_path": "/tmp/dup.py",
            "file_version": 2,
            "size_kb": 8,
            "decision": "pending",
        },
    }
    bridge.on_peer_broadcast(payload)
    bridge.on_peer_broadcast(payload)

    assert our_entry.hub_holders.count("lapis") == 1
