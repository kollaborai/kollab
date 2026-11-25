"""Client input IDs stay intact through the daemon queue actions (#1451)."""

from __future__ import annotations

import base64

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
    assert state.dispatches[0][1].request_id == "web-a1"
    assert state.dispatches[0][1].force_send is True


@pytest.mark.asyncio
async def test_input_attachments_are_staged_and_bound_to_the_same_client_id():
    server, _state, runtime = _server_and_runtime()
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
