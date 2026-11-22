"""Tests for the daemon RPC adapter around the in-memory queue state."""

from __future__ import annotations

import asyncio
import base64

import pytest
from kollabor_rpc import RpcServer

from kollabor.state import web_message_queue as queue_module
from kollabor.state.web_message_queue import WebMessage
from kollabor.state.web_message_queue_rpc import (
    QueueRpcAuthorizationError,
    QueueScopeCapacityExceeded,
    WebMessageQueueRegistry,
    register_web_message_queue_handlers,
)


async def _rpc_call(server: RpcServer, method: str, params: dict) -> dict:
    return await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "queue-rpc-test",
            "method": method,
            "params": params,
        }
    )


@pytest.mark.asyncio
async def test_rpc_round_trips_ordered_batch_and_only_dispatch_payload():
    registry = WebMessageQueueRegistry(max_scopes=4)
    server = RpcServer()
    changes = []
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=lambda _params: "authorized-scope-a",
        on_change=changes.append,
    )

    minted = await _rpc_call(
        server,
        "state.web_queue.allocate_ids",
        {"item_count": 2, "operation_count": 4},
    )
    assert minted["error"] is None
    ids = minted["result"]

    enqueued = await _rpc_call(
        server,
        "state.web_queue.enqueue_batch",
        {
            "operation_id": ids["operation_ids"][0],
            "batch_id": ids["batch_id"],
            "items": [
                {
                    "request_id": request_id,
                    "payload_b64": base64.b64encode(payload).decode("ascii"),
                }
                for request_id, payload in zip(
                    ids["request_ids"], (b"first", b"second"), strict=True
                )
            ],
        },
    )
    assert enqueued["result"]["status"] == "queued"
    assert changes == [
        {
            "scope_id": "authorized-scope-a",
            "incarnation": ids["incarnation"],
            "revision": 1,
        }
    ]
    assert "first" not in repr(changes)

    snapshot = await _rpc_call(server, "state.web_queue.snapshot", {})
    batch = snapshot["result"]["batches"][0]
    assert batch["request_ids"] == ids["request_ids"]
    assert [item["payload_b64"] for item in batch["items"]] == [
        base64.b64encode(b"first").decode("ascii"),
        base64.b64encode(b"second").decode("ascii"),
    ]

    claimed = await _rpc_call(
        server,
        "state.web_queue.claim_batch",
        {
            "operation_id": ids["operation_ids"][1],
            "batch_id": ids["batch_id"],
            "send_now": True,
        },
    )
    item_claim = await _rpc_call(
        server,
        "state.web_queue.claim_next_item",
        {
            "operation_id": ids["operation_ids"][2],
            "claim_id": claimed["result"]["claim_id"],
        },
    )
    payload = await _rpc_call(
        server,
        "state.web_queue.item_payload",
        {"attempt_id": item_claim["result"]["attempt_id"]},
    )
    assert payload["result"]["items"][0]["request_id"] == ids["request_ids"][0]
    assert payload["result"]["items"][0]["payload_b64"] == base64.b64encode(
        b"first"
    ).decode("ascii")

    settled = await _rpc_call(
        server,
        "state.web_queue.settle_item",
        {
            "operation_id": ids["operation_ids"][3],
            "attempt_id": item_claim["result"]["attempt_id"],
            "outcome": "accepted",
        },
    )
    assert settled["result"]["status"] == "accepted"
    snapshot = await _rpc_call(server, "state.web_queue.snapshot", {})
    assert [item["request_id"] for item in snapshot["result"]["batches"][0]["items"]] == [
        ids["request_ids"][1]
    ]


@pytest.mark.asyncio
async def test_rpc_uses_authorized_scope_resolver_not_caller_scope_parameter():
    registry = WebMessageQueueRegistry(max_scopes=2)
    server = RpcServer()

    def resolve_scope(params: dict) -> str:
        if params.get("denied") is True:
            raise QueueRpcAuthorizationError("queue access denied")
        return "principal-a-conversation-a"

    register_web_message_queue_handlers(server, registry, resolve_scope=resolve_scope)
    authorized = await _rpc_call(
        server,
        "state.web_queue.snapshot",
        {"scope_id": "principal-b-conversation-secret"},
    )
    assert authorized["result"]["scope_id"] == "principal-a-conversation-a"

    denied = await _rpc_call(
        server,
        "state.web_queue.snapshot",
        {"scope_id": "principal-b-conversation-secret", "denied": True},
    )
    assert denied["error_kind"] == "handler"
    assert registry.scope_count == 1


@pytest.mark.asyncio
async def test_registry_reuses_scopes_and_rejects_new_scope_while_live_scope_is_active():
    registry = WebMessageQueueRegistry(max_scopes=1)
    async with registry.acquire("scope-a") as first:
        async with registry.acquire("scope-a") as same:
            assert same is first
        with pytest.raises(QueueScopeCapacityExceeded):
            async with registry.acquire("scope-b"):
                pytest.fail("a second live scope exceeded the configured bound")


@pytest.mark.asyncio
async def test_registry_retires_empty_scope_only_after_its_retry_lease(monkeypatch):
    now = 1_000_000_000
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now)
    registry = WebMessageQueueRegistry(
        max_scopes=1,
        queue_options={"operation_retry_lease_seconds": 0.1},
    )
    async with registry.acquire("scope-a") as queue:
        request_id = queue.new_request_id()
        await queue.enqueue_batch(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (WebMessage(request_id, b"temporary"),),
        )
        await queue.remove_item(queue.new_operation_id(), request_id)

    now += 100_000_001
    async with registry.acquire("scope-b") as replacement:
        assert replacement.scope_id == "scope-b"
    assert registry.scope_count == 1


@pytest.mark.asyncio
async def test_registry_never_retires_pending_scope_even_after_lease(monkeypatch):
    now = 2_000_000_000
    monkeypatch.setattr(queue_module.time, "monotonic_ns", lambda: now)
    registry = WebMessageQueueRegistry(
        max_scopes=1,
        queue_options={"operation_retry_lease_seconds": 0.1},
    )
    async with registry.acquire("scope-a") as queue:
        await queue.enqueue_batch(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (WebMessage(queue.new_request_id(), b"pending"),),
        )

    now += 200_000_000
    with pytest.raises(QueueScopeCapacityExceeded):
        async with registry.acquire("scope-b"):
            pytest.fail("pending payload was silently discarded")


@pytest.mark.asyncio
async def test_concurrent_acquisition_of_same_scope_returns_one_queue():
    registry = WebMessageQueueRegistry(max_scopes=2)

    async def get_queue() -> object:
        async with registry.acquire("scope-a") as queue:
            await asyncio.sleep(0)
            return queue

    first, second = await asyncio.gather(get_queue(), get_queue())
    assert first is second
    assert registry.scope_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ("enqueue", "remove", "settle", "release"))
async def test_replayed_mutation_never_notifies_an_old_revision(mutation):
    registry = WebMessageQueueRegistry(max_scopes=2)
    server = RpcServer()
    changes = asyncio.Queue(maxsize=64)
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=lambda _params: "authorized-scope-a",
        on_change=changes.put_nowait,
    )

    async def allocate(item_count: int) -> dict:
        response = await _rpc_call(
            server,
            "state.web_queue.allocate_ids",
            {"item_count": item_count, "operation_count": 8},
        )
        assert response["error"] is None
        return response["result"]

    async def enqueue(ids: dict, *, operation_index: int = 0) -> None:
        response = await _rpc_call(
            server,
            "state.web_queue.enqueue_batch",
            {
                "operation_id": ids["operation_ids"][operation_index],
                "batch_id": ids["batch_id"],
                "items": [
                    {
                        "request_id": request_id,
                        "payload_b64": base64.b64encode(b"private").decode("ascii"),
                    }
                    for request_id in ids["request_ids"]
                ],
            },
        )
        assert response["error"] is None

    replay_params: dict
    if mutation == "enqueue":
        replay_ids = await allocate(1)
        await enqueue(replay_ids)
        later_ids = await allocate(1)
        await enqueue(later_ids)
        replay_params = {
            "operation_id": replay_ids["operation_ids"][0],
            "batch_id": replay_ids["batch_id"],
            "items": [
                {
                    "request_id": replay_ids["request_ids"][0],
                    "payload_b64": base64.b64encode(b"private").decode("ascii"),
                }
            ],
        }
        replay_method = "state.web_queue.enqueue_batch"
    elif mutation == "remove":
        ids = await allocate(2)
        await enqueue(ids)
        replay_params = {
            "operation_id": ids["operation_ids"][1],
            "request_id": ids["request_ids"][0],
        }
        first_remove = await _rpc_call(
            server, "state.web_queue.remove_item", replay_params
        )
        assert first_remove["error"] is None
        second_remove = await _rpc_call(
            server,
            "state.web_queue.remove_item",
            {
                "operation_id": ids["operation_ids"][2],
                "request_id": ids["request_ids"][1],
            },
        )
        assert second_remove["error"] is None
        replay_method = "state.web_queue.remove_item"
    elif mutation == "settle":
        ids = await allocate(2)
        await enqueue(ids)
        claim = await _rpc_call(
            server,
            "state.web_queue.claim_batch",
            {
                "operation_id": ids["operation_ids"][1],
                "batch_id": ids["batch_id"],
            },
        )
        item = await _rpc_call(
            server,
            "state.web_queue.claim_next_item",
            {
                "operation_id": ids["operation_ids"][2],
                "claim_id": claim["result"]["claim_id"],
            },
        )
        replay_params = {
            "operation_id": ids["operation_ids"][3],
            "attempt_id": item["result"]["attempt_id"],
            "outcome": "accepted",
        }
        settled = await _rpc_call(server, "state.web_queue.settle_item", replay_params)
        assert settled["error"] is None
        later_ids = await allocate(1)
        await enqueue(later_ids)
        replay_method = "state.web_queue.settle_item"
    else:
        ids = await allocate(1)
        await enqueue(ids)
        claim = await _rpc_call(
            server,
            "state.web_queue.claim_batch",
            {
                "operation_id": ids["operation_ids"][1],
                "batch_id": ids["batch_id"],
            },
        )
        replay_params = {
            "operation_id": ids["operation_ids"][2],
            "claim_id": claim["result"]["claim_id"],
        }
        released = await _rpc_call(
            server, "state.web_queue.release_claim", replay_params
        )
        assert released["error"] is None
        later_ids = await allocate(1)
        await enqueue(later_ids)
        replay_method = "state.web_queue.release_claim"

    replayed = await _rpc_call(server, replay_method, replay_params)
    assert replayed["error"] is None
    snapshot = await _rpc_call(server, "state.web_queue.snapshot", {})
    current_revision = snapshot["result"]["revision"]
    assert replayed["result"]["revision"] < current_revision

    emitted = []
    while not changes.empty():
        emitted.append(changes.get_nowait())
    assert not emitted or emitted[-1]["revision"] == current_revision


@pytest.mark.asyncio
async def test_rpc_rejects_async_change_notifier_without_starting_or_waiting_for_it():
    registry = WebMessageQueueRegistry(max_scopes=1)
    server = RpcServer()
    started = asyncio.Event()

    async def hanging_notifier(_metadata: dict) -> None:
        started.set()
        await asyncio.Future()

    with pytest.raises(TypeError, match="bounded asyncio.Queue.put_nowait"):
        register_web_message_queue_handlers(
            server,
            registry,
            resolve_scope=lambda _params: "authorized-scope-a",
            on_change=hanging_notifier,
        )

    assert not started.is_set()
    assert registry.scope_count == 0


def test_rpc_rejects_unbounded_change_event_queue():
    with pytest.raises(ValueError, match="finite maxsize"):
        register_web_message_queue_handlers(
            RpcServer(),
            WebMessageQueueRegistry(max_scopes=1),
            resolve_scope=lambda _params: "authorized-scope-a",
            on_change=asyncio.Queue().put_nowait,
        )


@pytest.mark.asyncio
async def test_full_change_buffer_drops_hints_and_snapshot_remains_authoritative():
    registry = WebMessageQueueRegistry(max_scopes=1)
    server = RpcServer()
    changes = asyncio.Queue(maxsize=1)
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=lambda _params: "authorized-scope-a",
        on_change=changes.put_nowait,
    )

    async def enqueue_one() -> str:
        minted = await _rpc_call(
            server,
            "state.web_queue.allocate_ids",
            {"item_count": 1, "operation_count": 1},
        )
        assert minted["error"] is None
        ids = minted["result"]
        enqueued = await _rpc_call(
            server,
            "state.web_queue.enqueue_batch",
            {
                "operation_id": ids["operation_ids"][0],
                "batch_id": ids["batch_id"],
                "items": [
                    {
                        "request_id": ids["request_ids"][0],
                        "payload_b64": base64.b64encode(b"queued").decode("ascii"),
                    }
                ],
            },
        )
        assert enqueued["error"] is None
        return ids["batch_id"]

    first_batch = await enqueue_one()
    second_batch = await enqueue_one()
    assert changes.qsize() == 1
    assert changes.get_nowait()["revision"] == 1

    snapshot = await _rpc_call(server, "state.web_queue.snapshot", {})
    assert snapshot["result"]["revision"] == 2
    assert [batch["batch_id"] for batch in snapshot["result"]["batches"]] == [
        first_batch,
        second_batch,
    ]
