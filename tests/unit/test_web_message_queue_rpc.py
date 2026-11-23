"""Tests for the daemon RPC adapter around the in-memory queue state."""

from __future__ import annotations

import asyncio
import base64

import pytest

from kollabor.state import web_message_queue as queue_module
from kollabor.state.web_message_queue import WebMessage
from kollabor.state.web_message_queue_rpc import (
    QueueRpcAuthorizationError,
    QueueScopeBinding,
    QueueScopeCapacityExceeded,
    WebMessageQueueRegistry,
    daemon_conversation_scope_resolver,
    register_web_message_queue_handlers,
)
from kollabor_rpc import RpcCallerContext, RpcServer


async def _rpc_call(server: RpcServer, method: str, params: dict) -> dict:
    return await _rpc_call_as(
        server,
        method,
        params,
        caller_context=RpcCallerContext.from_unix_peer(501),
    )


async def _rpc_call_as(
    server: RpcServer,
    method: str,
    params: dict,
    *,
    caller_context: RpcCallerContext,
) -> dict:
    return await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "queue-rpc-test",
            "method": method,
            "params": params,
        },
        caller_context=caller_context,
    )


class _ConversationState:
    def __init__(self, uid: str | None, revision: int = 1):
        self.uid = uid
        self.revision = revision

    def get_conversation_uid(self) -> str | None:
        return self.uid

    def get_conversation_revision(self) -> int:
        return self.revision


def _expected_binding(state: _ConversationState) -> dict[str, object]:
    return {
        "expected_conversation_uid": state.uid,
        "expected_conversation_revision": state.revision,
    }


@pytest.mark.asyncio
async def test_daemon_scope_binding_rejects_missing_mismatch_and_spoofed_scope():
    registry = WebMessageQueueRegistry(max_scopes=3)
    server = RpcServer()
    state = _ConversationState("conversation-a")
    resolver = daemon_conversation_scope_resolver(state)
    register_web_message_queue_handlers(server, registry, resolve_scope=resolver)
    principal = RpcCallerContext.from_unix_peer(501)

    allocated = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "allocate-a",
            "method": "state.web_queue.allocate_ids",
            "params": {
                "expected_conversation_uid": "conversation-a",
                "expected_conversation_revision": state.revision,
                "scope_id": "caller-chosen-scope",
                "principal_id": "uid:attacker",
                "session_id": "caller-chosen-session",
            },
        },
        caller_context=principal,
    )
    assert allocated["error"] is None
    scope_a = allocated["result"]["scope_id"]
    assert scope_a != "caller-chosen-scope"

    missing_binding = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "missing-binding",
            "method": "state.web_queue.snapshot",
            "params": {"scope_id": scope_a},
        },
        caller_context=principal,
    )
    assert missing_binding["error_kind"] == "handler"
    assert registry.scope_count == 1

    state.uid = "conversation-b"
    stale_binding = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "stale-binding",
            "method": "state.web_queue.snapshot",
            "params": {
                "expected_conversation_uid": "conversation-a",
                "expected_conversation_revision": state.revision,
                "scope_id": scope_a,
            },
        },
        caller_context=principal,
    )
    assert stale_binding["error_kind"] == "handler"
    assert registry.scope_count == 1

    current_binding = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "current-binding",
            "method": "state.web_queue.snapshot",
            "params": {
                "expected_conversation_uid": "conversation-b",
                "expected_conversation_revision": state.revision,
                "scope_id": scope_a,
            },
        },
        caller_context=principal,
    )
    assert current_binding["error"] is None
    assert current_binding["result"]["scope_id"] != scope_a
    assert current_binding["result"]["batches"] == []
    assert registry.scope_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["settle", "release"])
async def test_old_scope_cleanup_works_after_switch_without_reopening_payload(cleanup):
    registry = WebMessageQueueRegistry(max_scopes=3)
    server = RpcServer()
    state = _ConversationState("conversation-a", revision=7)
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=daemon_conversation_scope_resolver(state),
    )
    binding = _expected_binding(state)

    allocated = await _rpc_call(
        server,
        "state.web_queue.allocate_ids",
        {**binding, "item_count": 1, "operation_count": 5},
    )
    ids = allocated["result"]
    enqueued = await _rpc_call(
        server,
        "state.web_queue.enqueue_batch",
        {
            **binding,
            "operation_id": ids["operation_ids"][0],
            "batch_id": ids["batch_id"],
            "items": [
                {
                    "request_id": ids["request_ids"][0],
                    "payload_b64": base64.b64encode(b"private old conversation").decode(
                        "ascii"
                    ),
                }
            ],
        },
    )
    assert enqueued["error"] is None
    claim = await _rpc_call(
        server,
        "state.web_queue.claim_batch",
        {
            **binding,
            "operation_id": ids["operation_ids"][1],
            "batch_id": ids["batch_id"],
        },
    )
    if cleanup == "settle":
        item = await _rpc_call(
            server,
            "state.web_queue.claim_next_item",
            {
                **binding,
                "operation_id": ids["operation_ids"][2],
                "claim_id": claim["result"]["claim_id"],
            },
        )
        attempt_id = item["result"]["attempt_id"]
    else:
        attempt_id = None

    old_scope = claim["result"]["scope_id"]
    state.uid = "conversation-b"
    state.revision += 1
    new_binding = _expected_binding(state)

    # Payload and new work stay fenced to the active binding. Cleanup uses a
    # daemon-issued operation token to find its old queue, never a caller scope.
    payload = await _rpc_call(
        server,
        "state.web_queue.item_payload",
        {**new_binding, "attempt_id": attempt_id or claim["result"]["claim_id"]},
    )
    assert payload["error_kind"] == "handler"
    scope_count_before_cleanup = registry.scope_count
    if cleanup == "settle":
        settled = await _rpc_call(
            server,
            "state.web_queue.settle_item",
            {
                **binding,
                "operation_id": ids["operation_ids"][3],
                "attempt_id": attempt_id,
                "outcome": "in_doubt",
            },
        )
        assert settled["error"] is None
        assert settled["result"]["status"] == "in_doubt"
    else:
        released = await _rpc_call(
            server,
            "state.web_queue.release_claim",
            {
                **binding,
                "operation_id": ids["operation_ids"][2],
                "claim_id": claim["result"]["claim_id"],
            },
        )
        assert released["error"] is None
        assert released["result"]["status"] == "queued"
    assert registry.scope_count == scope_count_before_cleanup
    assert old_scope != ""


@pytest.mark.asyncio
async def test_scope_resolver_uses_daemon_state_uid_not_rotating_session_id():
    from types import SimpleNamespace

    from kollabor.state.local import LocalStateService

    registry = WebMessageQueueRegistry(max_scopes=2)
    server = RpcServer()
    conversation_manager = SimpleNamespace(
        current_session_id="session-before-resume",
        conversation_metadata={"conversation_uid": "conversation-stable"},
    )
    state_service = LocalStateService(
        SimpleNamespace(conversation_manager=conversation_manager),
        profile_manager=object(),
        event_bus=None,
    )
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=daemon_conversation_scope_resolver(state_service),
    )
    context = RpcCallerContext.from_unix_peer(501)

    async def snapshot(request_id: str, expected_uid: str) -> dict:
        return await server.handle_wire(
            {
                "action": "rpc_request",
                "request_id": request_id,
                "method": "state.web_queue.snapshot",
                "params": {
                    "expected_conversation_uid": expected_uid,
                    "session_id": "session-spoofed-by-caller",
                },
            },
            caller_context=context,
        )

    before = await snapshot("before-resume", "conversation-stable")
    conversation_manager.current_session_id = "session-after-resume"
    after = await snapshot("after-resume", "conversation-stable")

    assert before["error"] is None
    assert after["error"] is None
    assert after["result"]["scope_id"] == before["result"]["scope_id"]


@pytest.mark.asyncio
async def test_queue_scope_resolver_requires_authenticated_rpc_context_and_state_uid():
    registry = WebMessageQueueRegistry(max_scopes=2)
    server = RpcServer()
    state = _ConversationState(None)
    register_web_message_queue_handlers(
        server, registry, resolve_scope=daemon_conversation_scope_resolver(state)
    )

    async def call(params: dict, *, context=None):
        return await server.handle_wire(
            {
                "action": "rpc_request",
                "request_id": "scope-auth-test",
                "method": "state.web_queue.snapshot",
                "params": params,
            },
            caller_context=context,
        )

    no_context = await call(
        {"expected_conversation_uid": "conversation-a"}, context=None
    )
    assert no_context["error_kind"] == "handler"
    state.uid = None
    no_state_uid = await call(
        {"expected_conversation_uid": "conversation-a"},
        context=RpcCallerContext.from_unix_peer(501),
    )
    assert no_state_uid["error_kind"] == "handler"
    assert registry.scope_count == 0


@pytest.mark.asyncio
async def test_queue_rpc_scope_is_bound_to_canonical_conversation_not_rpc_fields():
    registry = WebMessageQueueRegistry(max_scopes=3)
    server = RpcServer()
    state = _ConversationState("conversation-a")
    register_web_message_queue_handlers(
        server, registry, resolve_scope=daemon_conversation_scope_resolver(state)
    )

    first = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "session-a",
            "method": "state.web_queue.snapshot",
            "params": {
                "expected_conversation_uid": "conversation-a",
                "scope_id": "conversation-b",
                "principal_id": "uid:other",
            },
        },
        caller_context=RpcCallerContext.from_unix_peer(501),
    )
    state.uid = "conversation-b"
    second = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "other-principal",
            "method": "state.web_queue.snapshot",
            "params": {
                "expected_conversation_uid": "conversation-b",
                "scope_id": "conversation-a",
                "principal_id": "uid:other",
            },
        },
        caller_context=RpcCallerContext.from_unix_peer(501),
    )

    assert first["error"] is None
    assert second["error"] is None
    assert first["result"]["scope_id"] != "conversation-b"
    assert second["result"]["scope_id"] != first["result"]["scope_id"]


@pytest.mark.asyncio
async def test_rpc_round_trips_ordered_batch_and_only_dispatch_payload():
    registry = WebMessageQueueRegistry(max_scopes=4)
    server = RpcServer()
    changes = asyncio.Queue(maxsize=32)
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=lambda _params, _context: "authorized-scope-a",
        on_change=changes.put_nowait,
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
    change = changes.get_nowait()
    assert change == {
        "scope_id": "authorized-scope-a",
        "incarnation": ids["incarnation"],
        "revision": 1,
    }
    assert "first" not in repr(change)

    snapshot = await _rpc_call(server, "state.web_queue.snapshot", {})
    batch = snapshot["result"]["batches"][0]
    assert batch["request_ids"] == ids["request_ids"]
    assert [item["text_preview"] for item in batch["items"]] == ["", ""]
    assert all("payload_b64" not in item for item in batch["items"])
    assert all("attachment_refs" not in item for item in batch["items"])

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
    assert [
        item["request_id"] for item in snapshot["result"]["batches"][0]["items"]
    ] == [ids["request_ids"][1]]


@pytest.mark.asyncio
async def test_rpc_uses_authorized_scope_resolver_not_caller_scope_parameter():
    registry = WebMessageQueueRegistry(max_scopes=2)
    server = RpcServer()

    def resolve_scope(params: dict, _context: RpcCallerContext) -> str:
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
async def test_registry_rejects_scope_rebinding_to_another_principal():
    registry = WebMessageQueueRegistry(max_scopes=2)
    server = RpcServer()
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=lambda _params, _context: QueueScopeBinding(
            "shared-scope", "conversation-a"
        ),
    )
    params = {"expected_conversation_uid": "conversation-a"}

    first = await _rpc_call_as(
        server,
        "state.web_queue.snapshot",
        params,
        caller_context=RpcCallerContext.from_unix_peer(501),
    )
    second = await _rpc_call_as(
        server,
        "state.web_queue.snapshot",
        params,
        caller_context=RpcCallerContext.from_unix_peer(502),
    )

    assert first["error"] is None
    assert second["error_kind"] == "handler"
    assert second["error"] == "QueueRpcAuthorizationError('queue access denied')"
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
async def test_snapshot_only_scope_retires_without_waiting_for_an_id_lease():
    registry = WebMessageQueueRegistry(max_scopes=1)
    server = RpcServer()
    current_scope = "scope-snapshot-only"
    register_web_message_queue_handlers(
        server, registry, resolve_scope=lambda _params, _context: current_scope
    )

    first = await _rpc_call(server, "state.web_queue.snapshot", {})
    assert first["result"]["batches"] == []
    current_scope = "scope-next-conversation"
    next_snapshot = await _rpc_call(server, "state.web_queue.snapshot", {})

    assert next_snapshot["result"]["scope_id"] == current_scope
    assert next_snapshot["result"]["batches"] == []
    assert registry.scope_count == 1


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
        resolve_scope=lambda _params, _context: "authorized-scope-a",
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
            resolve_scope=lambda _params, _context: "authorized-scope-a",
            on_change=hanging_notifier,
        )

    assert not started.is_set()
    assert registry.scope_count == 0


def test_rpc_rejects_unbounded_change_event_queue():
    with pytest.raises(ValueError, match="finite maxsize"):
        register_web_message_queue_handlers(
            RpcServer(),
            WebMessageQueueRegistry(max_scopes=1),
            resolve_scope=lambda _params, _context: "authorized-scope-a",
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
        resolve_scope=lambda _params, _context: "authorized-scope-a",
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


@pytest.mark.asyncio
async def test_stale_scope_allows_only_same_principal_claim_cleanup():
    registry = WebMessageQueueRegistry(max_scopes=4)
    server = RpcServer()
    changes = asyncio.Queue(maxsize=64)
    state = _ConversationState("conversation-a")
    register_web_message_queue_handlers(
        server,
        registry,
        resolve_scope=daemon_conversation_scope_resolver(state),
        on_change=changes.put_nowait,
    )
    owner = RpcCallerContext.from_unix_peer(501)

    async def allocate(uid: str, payload: bytes) -> dict:
        response = await _rpc_call_as(
            server,
            "state.web_queue.allocate_ids",
            {"expected_conversation_uid": uid, "item_count": 1, "operation_count": 8},
            caller_context=owner,
        )
        assert response["error"] is None
        ids = response["result"]
        enqueued = await _rpc_call_as(
            server,
            "state.web_queue.enqueue_batch",
            {
                "expected_conversation_uid": uid,
                "operation_id": ids["operation_ids"][0],
                "batch_id": ids["batch_id"],
                "items": [
                    {
                        "request_id": ids["request_ids"][0],
                        "payload_b64": base64.b64encode(payload).decode("ascii"),
                    }
                ],
            },
            caller_context=owner,
        )
        assert enqueued["error"] is None
        return ids

    ids_a = await allocate("conversation-a", b"private-a")
    claim_a = await _rpc_call_as(
        server,
        "state.web_queue.claim_batch",
        {
            "expected_conversation_uid": "conversation-a",
            "operation_id": ids_a["operation_ids"][1],
            "batch_id": ids_a["batch_id"],
        },
        caller_context=owner,
    )
    item_a = await _rpc_call_as(
        server,
        "state.web_queue.claim_next_item",
        {
            "expected_conversation_uid": "conversation-a",
            "operation_id": ids_a["operation_ids"][2],
            "claim_id": claim_a["result"]["claim_id"],
        },
        caller_context=owner,
    )
    assert item_a["error"] is None
    old_scope_a = ids_a["scope_id"]

    state.uid = "conversation-b"
    ids_b = await allocate("conversation-b", b"private-b")
    claim_b = await _rpc_call_as(
        server,
        "state.web_queue.claim_batch",
        {
            "expected_conversation_uid": "conversation-b",
            "operation_id": ids_b["operation_ids"][1],
            "batch_id": ids_b["batch_id"],
        },
        caller_context=owner,
    )
    assert claim_b["error"] is None
    old_scope_b = ids_b["scope_id"]

    # Capture the target queue so rejected candidate probes can prove they do
    # not call its mutating replay path (which prunes expired receipts).
    queue_a = registry._queues[old_scope_a]
    prune_calls = 0
    original_prune = queue_a._prune_expired

    def count_prune() -> None:
        nonlocal prune_calls
        prune_calls += 1
        original_prune()

    queue_a._prune_expired = count_prune
    queue_a_before = (
        queue_a._revision,
        tuple(queue_a._operations),
        queue_a._active_item_attempt_id,
    )
    queue_b = registry._queues[old_scope_b]
    queue_b_before = (
        queue_b._revision,
        tuple(queue_b._operations),
        queue_b._active_claim,
    )
    while not changes.empty():
        changes.get_nowait()
    state.uid = "conversation-c"
    stale_a = {"expected_conversation_uid": "conversation-a"}
    stale_b = {"expected_conversation_uid": "conversation-b"}

    # Neither a stale UID nor a same-principal token from a different
    # conversation can select a queue for payload access or new work.
    denied_calls = [
        await _rpc_call_as(
            server,
            "state.web_queue.snapshot",
            stale_a,
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.item_payload",
            {**stale_a, "attempt_id": item_a["result"]["attempt_id"]},
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.claim_next",
            {**stale_a, "operation_id": ids_a["operation_ids"][4]},
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.claim_batch",
            {
                **stale_a,
                "operation_id": ids_a["operation_ids"][6],
                "batch_id": ids_a["batch_id"],
            },
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.claim_next_item",
            {
                **stale_a,
                "operation_id": ids_a["operation_ids"][7],
                "claim_id": claim_a["result"]["claim_id"],
            },
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.release_claim",
            {
                **stale_a,
                "operation_id": ids_a["operation_ids"][4],
                "claim_id": claim_a["result"]["claim_id"],
            },
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.allocate_ids",
            {**stale_a, "item_count": 1, "operation_count": 1},
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.enqueue_batch",
            {
                **stale_a,
                "operation_id": ids_a["operation_ids"][5],
                "batch_id": ids_a["batch_id"],
                "items": [
                    {
                        "request_id": ids_a["request_ids"][0],
                        "payload_b64": base64.b64encode(b"extra").decode("ascii"),
                    }
                ],
            },
            caller_context=owner,
        ),
        await _rpc_call_as(
            server,
            "state.web_queue.settle_item",
            {
                **stale_a,
                "operation_id": ids_b["operation_ids"][2],
                "attempt_id": item_a["result"]["attempt_id"],
                "outcome": "accepted",
            },
            caller_context=owner,
        ),
    ]
    assert all(response["error_kind"] == "handler" for response in denied_calls)
    assert all(
        response["error"] == denied_calls[0]["error"] for response in denied_calls
    )
    assert registry.scope_count == 2
    assert prune_calls == 0

    # A capability from a different authenticated local principal cannot
    # settle this owner's old queue.
    foreign_principal = await _rpc_call_as(
        server,
        "state.web_queue.settle_item",
        {
            **stale_a,
            "operation_id": ids_a["operation_ids"][3],
            "attempt_id": item_a["result"]["attempt_id"],
            "outcome": "accepted",
        },
        caller_context=RpcCallerContext.from_unix_peer(502),
    )
    assert foreign_principal["error_kind"] == denied_calls[0]["error_kind"]
    assert foreign_principal["error"] == denied_calls[0]["error"]

    # A valid HMAC from another queue incarnation is not a capability for the
    # registered old queue, even when it reuses the same opaque scope label.
    other_incarnation = queue_module.WebMessageQueue(scope_id=old_scope_a)
    wrong_incarnation = await _rpc_call_as(
        server,
        "state.web_queue.settle_item",
        {
            **stale_a,
            "operation_id": other_incarnation.new_operation_id(),
            "attempt_id": other_incarnation.new_operation_id(),
            "outcome": "accepted",
        },
        caller_context=owner,
    )
    assert wrong_incarnation["error_kind"] == denied_calls[0]["error_kind"]
    assert wrong_incarnation["error"] == denied_calls[0]["error"]
    assert prune_calls == 0
    assert changes.empty()
    assert (
        queue_a._revision,
        tuple(queue_a._operations),
        queue_a._active_item_attempt_id,
    ) == queue_a_before
    assert (queue_b._revision, tuple(queue_b._operations), queue_b._active_claim) == (
        queue_b_before
    )
    assert registry._leases == {old_scope_a: 0, old_scope_b: 0}

    # The two cleanup operations are accepted only with the authentic old
    # scope, the same principal, and pre-issued operation IDs.
    settled = await _rpc_call_as(
        server,
        "state.web_queue.settle_item",
        {
            **stale_a,
            "operation_id": ids_a["operation_ids"][3],
            "attempt_id": item_a["result"]["attempt_id"],
            "outcome": "accepted",
        },
        caller_context=owner,
    )
    released = await _rpc_call_as(
        server,
        "state.web_queue.release_claim",
        {
            **stale_b,
            "operation_id": ids_b["operation_ids"][2],
            "claim_id": claim_b["result"]["claim_id"],
        },
        caller_context=owner,
    )
    assert settled["error"] is None
    assert settled["result"]["status"] == "accepted"
    assert settled["result"]["scope_id"] == old_scope_a
    assert released["error"] is None
    assert released["result"]["status"] == "queued"
    assert released["result"]["scope_id"] == old_scope_b
    assert registry.scope_count == 2
    assert prune_calls > 0
    assert not changes.empty()
