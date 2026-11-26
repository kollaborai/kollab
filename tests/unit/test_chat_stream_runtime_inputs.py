"""Client input IDs stay intact through the daemon queue actions (#1451)."""

from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from kollabor.state.chat_stream import ChatStreamService, register_chat_stream_handlers
from kollabor.state.web_message_queue_runtime import register_web_message_queue_runtime
from kollabor.state.web_message_queue_worker import ConversationIdentity
from kollabor_rpc import RpcCallerContext, RpcServer


class _DisplayTap:
    def __init__(self):
        self.events = []
        self.listeners = []

    def publish(self, event):
        self.events.append(event)

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)


class _Bus:
    def __init__(self):
        self.services = {"display_tap": _DisplayTap()}

    def register_service(self, name, service):
        self.services[name] = service

    def get_service(self, name):
        return self.services.get(name)


class _State:
    def __init__(self):
        self._event_bus = _Bus()
        self._llm_service = type("LLM", (), {"conversation_history": []})()
        self.identity = ConversationIdentity("conversation-a", 7)
        self.is_processing = True
        self.dispatches = []

    def get_conversation_uid(self):
        return self.identity.uid

    def get_conversation_identity(self):
        return self.identity

    def chat_stream_snapshot(self):
        return {"rows": [], "turns": [], "inputs": [], "older": None}

    async def submit_queue_message(self, message, *, queue_handoff, batch_lease=None):
        self.dispatches.append((message, queue_handoff, batch_lease))
        queue_handoff.provider_custody_id = f"test:{queue_handoff.request_id}"
        queue_handoff.recording_status = "recorded"
        return {"status": "queued"}

    async def begin_queue_handoff_batch(self, request):
        return object()

    def finish_queue_handoff_batch(self, batch_lease, *, accepted_count, uncertain):
        return accepted_count == 1 and not uncertain


async def _call(server: RpcServer, method: str, params: dict):
    return await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": f"chat-stream-{method}",
            "method": method,
            "params": params,
        },
        caller_context=RpcCallerContext.from_unix_peer(501),
    )


def _server_and_runtime():
    server = RpcServer()
    state = _State()
    runtime = register_web_message_queue_runtime(server, state)
    stream = ChatStreamService(state)
    register_chat_stream_handlers(server, stream)
    return server, state, runtime


@pytest.mark.asyncio
async def test_input_admission_and_cancel_keep_the_clients_id_in_the_queue():
    server, state, runtime = _server_and_runtime()

    added = await _call(
        server,
        "state.chat_stream.add_input",
        {"id": "web-a1", "text": "hello", "attachments": []},
    )

    assert added["error"] is None
    assert added["result"] == {"id": "web-a1", "state": "queued"}
    scopes = await runtime.registry.known_scopes()
    assert len(scopes) == 1
    async with runtime.registry.acquire_existing_scope(scopes[0]) as queue:
        snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == ("web-a1",)

    cancelled = await _call(
        server,
        "state.chat_stream.cancel_queued_input",
        {"id": "web-a1"},
    )

    assert cancelled["error"] is None
    assert cancelled["result"] == {"found": True, "cancelled": True}
    async with runtime.registry.acquire_existing_scope(scopes[0]) as queue:
        assert (await queue.snapshot()).batches == ()
    assert state._event_bus.get_service("display_tap").events


@pytest.mark.asyncio
async def test_send_now_after_cancel_reports_the_owned_ended_input():
    server, state, _runtime = _server_and_runtime()
    added = await _call(
        server,
        "state.chat_stream.add_input",
        {"id": "web-cancelled", "text": "hello", "attachments": []},
    )
    cancelled = await _call(
        server,
        "state.chat_stream.cancel_queued_input",
        {"id": "web-cancelled"},
    )

    sent = await _call(
        server,
        "state.chat_stream.send_now",
        {"id": "web-cancelled"},
    )

    assert added["result"] == {"id": "web-cancelled", "state": "queued"}
    assert cancelled["result"] == {"found": True, "cancelled": True}
    assert sent["error"] is None
    assert sent["result"] == {
        "found": True,
        "accepted": False,
        "state": "ended",
    }
    assert state.dispatches == []


@pytest.mark.asyncio
async def test_send_now_dispatches_only_the_input_named_by_the_client():
    server, state, _runtime = _server_and_runtime()
    body = {"id": "web-a1", "text": "send now", "attachments": []}

    added = await _call(server, "state.chat_stream.add_input", body)
    sent = await _call(
        server,
        "state.chat_stream.send_now",
        {"id": "web-a1"},
    )

    assert added["result"] == {"id": "web-a1", "state": "queued"}
    assert sent["error"] is None
    assert sent["result"]["found"] is True
    assert sent["result"]["accepted"] is True
    assert state.dispatches[0][1].request_id == "web-a1"
    assert state.dispatches[0][1].force_send is True


@pytest.mark.asyncio
async def test_input_attachments_are_staged_and_bound_to_the_same_client_id():
    server, state, runtime = _server_and_runtime()
    stream = state._event_bus.get_service("chat_stream")
    image = b"\x89PNG\r\n\x1a\nchat stream test image"
    data_url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")

    added = await _call(
        server,
        "state.chat_stream.add_input",
        {
            "id": "web-a1",
            "text": "look",
            "attachments": [
                {
                    "name": "image.png",
                    "content_type": "image/png",
                    "data_url": data_url,
                }
            ],
        },
    )

    assert added["error"] is None
    assert added["result"] == {"id": "web-a1", "state": "queued"}
    scopes = await runtime.registry.known_scopes()
    async with runtime.registry.acquire_existing_scope(scopes[0]) as queue:
        item = (await queue.snapshot()).batches[0].items[0]
    assert item.request_id == "web-a1"
    assert len(item.attachment_refs) == 1
    attachment_scope = await runtime.attachments.bind_scope(
        {"expected_conversation_uid": "conversation-a"},
        RpcCallerContext.from_unix_peer(501),
    )
    staged = await attachment_scope.open_for_owner(
        item.request_id, item.attachment_refs[0]
    )
    assert staged.data == image
    row = next(
        row
        for row in stream._snapshot("conversation-a")["rows"]
        if row["id"] == "web-a1"
    )
    assert row["parts"][1]["url"].startswith("kollab-queue-image:?")
    assert "data:" not in row["parts"][1]["url"]


@pytest.mark.asyncio
async def test_send_now_selects_one_input_from_a_shared_batch():
    from kollabor.state.web_message_queue import WebMessage

    server, state, runtime = _server_and_runtime()
    await _call(
        server,
        "state.chat_stream.add_input",
        {"id": "web-seed", "text": "seed", "attachments": []},
    )
    scopes = await runtime.registry.known_scopes()
    async with runtime.registry.acquire_existing_scope(scopes[0]) as queue:
        await queue.enqueue_batch(
            queue.new_operation_id(),
            queue.new_batch_id(),
            tuple(
                WebMessage(f"web-{name}", ('{"text":"' + name + '"}').encode())
                for name in ("first", "selected", "last")
            ),
        )

    sent = await _call(server, "state.chat_stream.send_now", {"id": "web-selected"})

    assert [handoff.request_id for _, handoff, _ in state.dispatches] == [
        "web-selected"
    ]


@pytest.mark.asyncio
async def test_send_now_reports_dispatch_unavailable_without_ending_queued_input():
    server, state, runtime = _server_and_runtime()
    admitted = await _call(
        server,
        "state.chat_stream.add_input",
        {"id": "web-still-queued", "text": "keep queued", "attachments": []},
    )
    assert admitted["result"] == {
        "id": "web-still-queued",
        "state": "queued",
    }
    runtime.dispatch_available = False

    sent = await _call(
        server, "state.chat_stream.send_now", {"id": "web-still-queued"}
    )

    assert sent["result"] == {
        "found": True,
        "accepted": False,
        "state": "queued",
        "reason": "dispatch_unavailable",
    }
    assert state.dispatches == []
    scopes = await runtime.registry.known_scopes()
    async with runtime.registry.acquire_existing_scope(scopes[0]) as queue:
        snapshot = await queue.snapshot()
    assert snapshot.batches[0].request_ids == ("web-still-queued",)
    assert sent["result"]["accepted"] is True
    async with runtime.registry.acquire_existing_scope(scopes[0]) as queue:
        snapshot = await queue.snapshot()
    assert [item.request_id for batch in snapshot.batches for item in batch.items] == [
        "web-seed",
        "web-first",
        "web-last",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["started", "joined", "ended"])
@pytest.mark.parametrize("during_lookup", [False, True])
async def test_send_now_retry_does_not_disclose_another_principals_input(
    lifecycle, during_lookup, monkeypatch
):
    server, state, runtime = _server_and_runtime()
    stream = state._event_bus.get_service("chat_stream")
    await _call(
        server,
        "state.chat_stream.add_input",
        {"id": "web-private", "text": "private", "attachments": []},
    )
    # Give the other principal a real queue in the same conversation.
    request = {
        "action": "rpc_request",
        "request_id": "other-add",
        "method": "state.chat_stream.add_input",
        "params": {"id": "web-other", "text": "other", "attachments": []},
    }
    assert (
        await server.handle_wire(
            request, caller_context=RpcCallerContext.from_unix_peer(502)
        )
    )["error"] is None

    def move():
        stream._set_input_state(
            "conversation-a",
            "web-private",
            lifecycle,
            turn_id="turn-private",
            outcome="completed" if lifecycle == "ended" else None,
        )

    if during_lookup:
        original = type(runtime).send_now

        async def move_during_lookup(self, params):
            result = await original(self, params)
            move()
            return result

        monkeypatch.setattr(type(runtime), "send_now", move_during_lookup)
    else:
        move()

    retry = {
        "action": "rpc_request",
        "request_id": "other-retry",
        "method": "state.chat_stream.send_now",
        "params": {"id": "web-private"},
    }
    for caller in (None, RpcCallerContext.from_unix_peer(502)):
        response = await server.handle_wire(retry, caller_context=caller)
        assert response.get("result") in (None, {"found": False})

    owner = await _call(server, "state.chat_stream.send_now", {"id": "web-private"})
    assert owner["result"] == {
        "found": True,
        "accepted": False,
        "state": lifecycle,
        "turn_id": "turn-private",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("during_dispatch", [False, True])
async def test_queue_batch_send_now_retains_owner_for_lifecycle_retry(during_dispatch):
    from kollabor.state.web_message_queue import WebMessage

    server, state, runtime = _server_and_runtime()
    stream = state._event_bus.get_service("chat_stream")
    await _call(
        server,
        "state.chat_stream.add_input",
        {"id": "web-seed", "text": "web-seed", "attachments": []},
    )
    scope = (await runtime.registry.known_scopes())[0]
    async with runtime.registry.acquire_existing_scope(scope) as queue:
        await queue.enqueue_batch(
            queue.new_operation_id(),
            queue.new_batch_id(),
            (
                WebMessage("web-sibling", b'{"text":"web-sibling"}'),
                WebMessage("web-selected", b'{"text":"web-selected"}'),
            ),
        )

    def started():
        stream._set_input_state(
            "conversation-a", "web-selected", "started", turn_id="turn-selected"
        )

    original = state.submit_queue_message

    async def dispatch(*args, **kwargs):
        result = await original(*args, **kwargs)
        if during_dispatch:
            started()
        return result

    state.submit_queue_message = dispatch
    sent = await _call(server, "state.chat_stream.send_now", {"id": "web-selected"})
    assert sent["result"]["found"] is True
    assert sent["result"]["accepted"] is True
    if not during_dispatch:
        started()
    retry = await _call(server, "state.chat_stream.send_now", {"id": "web-selected"})
    assert retry["result"] == {
        "found": True,
        "accepted": False,
        "state": "started",
        "turn_id": "turn-selected",
    }
    other = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "other",
            "method": "state.chat_stream.send_now",
            "params": {"id": "web-selected"},
        },
        caller_context=RpcCallerContext.from_unix_peer(502),
    )
    assert other.get("result") in (None, {"found": False})
    assert [handoff.request_id for _, handoff, _ in state.dispatches] == [
        "web-selected"
    ]


@pytest.mark.asyncio
async def test_colliding_queue_ids_never_dispatch_for_two_principals():
    from kollabor.state.web_message_queue import WebMessage

    server, state, runtime = _server_and_runtime()
    stream = state._event_bus.get_service("chat_stream")
    for principal in (501, 502):
        response = await server.handle_wire(
            {
                "action": "rpc_request",
                "request_id": str(principal),
                "method": "state.chat_stream.add_input",
                "params": {
                    "id": f"seed-{principal}",
                    "text": "seed",
                    "attachments": [],
                },
            },
            caller_context=RpcCallerContext.from_unix_peer(principal),
        )
        assert response["error"] is None
    for scope in await runtime.registry.known_scopes():
        async with runtime.registry.acquire_existing_scope(scope) as queue:
            await queue.enqueue_batch(
                queue.new_operation_id(),
                queue.new_batch_id(),
                (WebMessage("web-same", b'{"text":"collision"}'),),
            )

    original = state.submit_queue_message

    async def dispatch(message, *, queue_handoff, batch_lease=None):
        result = await original(
            message, queue_handoff=queue_handoff, batch_lease=batch_lease
        )
        if len(state.dispatches) > 1:
            stream._set_input_state(
                "conversation-a", "web-same", "started", turn_id="turn-1"
            )
        return result

    state.submit_queue_message = dispatch

    async def send(principal):
        return await server.handle_wire(
            {
                "action": "rpc_request",
                "request_id": str(principal),
                "method": "state.chat_stream.send_now",
                "params": {"id": "web-same"},
            },
            caller_context=RpcCallerContext.from_unix_peer(principal),
        )

    results = await asyncio.gather(send(501), send(502))
    assert (
        sum(result.get("result", {}).get("accepted") is True for result in results) == 1
    )
    assert len(state.dispatches) == 1
    owner = 501 if results[0]["result"].get("accepted") else 502
    other = 502 if owner == 501 else 501
    stream._set_input_state("conversation-a", "web-same", "started", turn_id="turn-1")
    assert (await send(owner))["result"]["turn_id"] == "turn-1"
    assert (await send(other))["result"] == {"found": False}
    assert len(state.dispatches) == 1


@pytest.mark.asyncio
async def test_queue_rejects_cross_principal_batch_collision_before_draining():
    server, state, runtime = _server_and_runtime()

    async def call(principal, method, params):
        return await server.handle_wire(
            {
                "action": "rpc_request",
                "request_id": method,
                "method": method,
                "params": params,
            },
            caller_context=RpcCallerContext.from_unix_peer(principal),
        )

    binding = {
        "expected_conversation_uid": "conversation-a",
        "expected_conversation_revision": 7,
    }
    assert (
        await call(
            501,
            "state.chat_stream.add_input",
            {"id": "web-same", "text": "first", "attachments": []},
        )
    )["error"] is None
    ids = (
        await call(
            502,
            "state.web_queue.allocate_ids",
            {**binding, "item_count": 2, "operation_count": 2},
        )
    )["result"]

    def batch(operation, batch_id, names):
        return {
            **binding,
            "operation_id": operation,
            "batch_id": batch_id,
            "items": [
                {
                    "request_id": name,
                    "payload_b64": base64.b64encode(b'{"text":"queued"}').decode(),
                }
                for name in names
            ],
        }

    rejected = await call(
        502,
        "state.web_queue.enqueue_batch",
        batch(ids["operation_ids"][0], ids["batch_id"], ["web-same", "web-unique-1"]),
    )
    assert rejected["error"] is not None
    scopes = await runtime.registry.known_scopes()
    second = next(
        scope
        for scope in scopes
        if scope.principal_id == RpcCallerContext.from_unix_peer(502).principal_id
    )
    async with runtime.registry.acquire_existing_scope(second) as queue:
        assert (await queue.snapshot()).batches == ()
    later = await call(
        502,
        "state.web_queue.enqueue_batch",
        batch(ids["operation_ids"][1], ids["batch_id"], ["web-unique-1"]),
    )
    assert later["error"] is None
    state.is_processing = False
    await runtime._dispatch_known_scopes()
    assert [handoff.request_id for _, handoff, _ in state.dispatches] == [
        "web-same",
        "web-unique-1",
    ]
    await runtime.close()


@pytest.mark.asyncio
async def test_web_queue_batch_publishes_inputs_and_cancel_lifecycle():
    server, state, runtime = _server_and_runtime()
    stream = state._event_bus.get_service("chat_stream")
    initial = stream.subscribe({})["events"][0]
    cursor = initial["data"]["next_seq"] - 1
    binding = {"expected_conversation_uid": "conversation-a"}
    allocated = await _call(
        server,
        "state.web_queue.allocate_ids",
        {
            **binding,
            "item_count": 2,
            "operation_count": 1,
            "attachment_operation_count": 1,
        },
    )
    assert allocated["error"] is None
    ids = allocated["result"]
    image = b"\x89PNG\r\n\x1a\nqueued image bytes"
    staged = await _call(
        server,
        "state.web_queue.stage_attachment",
        {
            **binding,
            "operation_id": ids["attachment_operation_ids"][0],
            "mime": "image/png",
            "filename": "queued.png",
            "payload_b64": base64.b64encode(image).decode("ascii"),
        },
    )
    assert staged["error"] is None
    first_id, second_id = ids["request_ids"]
    batch_params = {
        **binding,
        "operation_id": ids["operation_ids"][0],
        "batch_id": ids["batch_id"],
        "items": [
            {
                "request_id": first_id,
                "payload_b64": base64.b64encode(
                    json.dumps({"text": "first queued"}).encode()
                ).decode("ascii"),
            },
            {
                "request_id": second_id,
                "payload_b64": base64.b64encode(
                    json.dumps({"text": "second queued"}).encode()
                ).decode("ascii"),
                "attachment_refs": [staged["result"]["ref"]],
            },
        ],
    }
    queued = await _call(server, "state.web_queue.enqueue_batch", batch_params)
    assert queued["error"] is None

    admitted = stream.subscribe(
        {
            "after": cursor,
            "conversation_uid": "conversation-a",
            "generation": initial["generation"],
        }
    )["events"]
    states = [event["data"] for event in admitted if event["type"] == "input.state"]
    rows = [event["data"] for event in admitted if event["type"] == "row.upsert"]
    assert {state["id"] for state in states} == {first_id, second_id}
    assert all(state["state"] == "queued" for state in states)
    rows_by_id = {row["id"]: row for row in rows}
    assert rows_by_id[first_id]["parts"] == [
        {"type": "text", "text": "first queued"}
    ]
    assert rows_by_id[second_id]["parts"][0] == {
        "type": "text",
        "text": "second queued",
    }
    assert rows_by_id[second_id]["parts"][1]["type"] == "image"
    image_url = rows_by_id[second_id]["parts"][1]["url"]
    assert image_url.startswith("kollab-queue-image:?")
    image_ref = parse_qs(urlsplit(image_url).query)
    assert image_ref == {
        "conversation_uid": ["conversation-a"],
        "request_id": [second_id],
        "ref": [staged["result"]["ref"]],
    }

    cancelled = await _call(
        server,
        "state.chat_stream.cancel_queued_input",
        {"id": second_id},
    )
    assert cancelled["result"] == {"found": True, "cancelled": True}
    final_cursor = admitted[-1]["seq"]
    terminal = stream.subscribe(
        {
            "after": final_cursor,
            "conversation_uid": "conversation-a",
            "generation": initial["generation"],
        }
    )["events"]
    terminal_states = [
        event["data"] for event in terminal if event["type"] == "input.state"
    ]
    terminal_rows = [
        event["data"] for event in terminal if event["type"] == "row.upsert"
    ]
    assert terminal_states == [
        {
            "id": second_id,
            "state": "ended",
            "turn_id": None,
            "outcome": "cancelled",
        }
    ]
    assert terminal_rows[0]["id"] == second_id
    assert terminal_rows[0]["status"] == "cancelled"

    replay = await _call(server, "state.web_queue.enqueue_batch", batch_params)
    assert replay["error"] is None
    assert stream.subscribe(
        {
            "after": terminal[-1]["seq"],
            "conversation_uid": "conversation-a",
            "generation": initial["generation"],
        }
    )["events"] == []
    await runtime.close()
