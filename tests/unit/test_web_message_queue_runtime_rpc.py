"""Composition tests for the daemon-owned WebUI queue management RPC."""

from __future__ import annotations

import base64
import json

import pytest

from kollabor.state.web_message_queue_rpc import WebMessageQueueRegistry
from kollabor.state.web_message_queue_runtime import register_web_message_queue_runtime
from kollabor_rpc import RpcCallerContext, RpcServer


class _ConversationState:
    def __init__(self, uid: str | None):
        self.uid = uid

    def get_conversation_uid(self) -> str | None:
        return self.uid


async def _call(server: RpcServer, method: str, params: dict) -> dict:
    return await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "queue-runtime-test",
            "method": method,
            "params": params,
        },
        caller_context=RpcCallerContext.from_unix_peer(501),
    )


@pytest.mark.asyncio
async def test_runtime_registers_safe_queue_and_attachment_operations_only():
    server = RpcServer()
    state = _ConversationState("conversation-a")

    runtime = register_web_message_queue_runtime(server, state)

    methods = set(server.list_methods())
    assert {
        "state.web_queue.allocate_ids",
        "state.web_queue.snapshot",
        "state.web_queue.enqueue_batch",
        "state.web_queue.remove_item",
        "state.web_queue.stage_attachment",
        "state.web_queue.open_attachment",
        "state.web_queue.capabilities",
    } <= methods
    assert not {
        "state.web_queue.claim_next",
        "state.web_queue.claim_batch",
        "state.web_queue.claim_next_item",
        "state.web_queue.item_payload",
        "state.web_queue.settle_item",
        "state.web_queue.release_claim",
    } & methods
    assert runtime.dispatch_available is False

    binding = {"expected_conversation_uid": "conversation-a"}
    allocated = await _call(
        server,
        "state.web_queue.allocate_ids",
        {
            **binding,
            "item_count": 1,
            "operation_count": 2,
            "attachment_operation_count": 1,
        },
    )
    assert allocated["error"] is None
    ids = allocated["result"]

    image = b"bounded image bytes"
    staged = await _call(
        server,
        "state.web_queue.stage_attachment",
        {
            **binding,
            "operation_id": ids["attachment_operation_ids"][0],
            "mime": "image/png",
            "payload_b64": base64.b64encode(image).decode("ascii"),
        },
    )
    assert staged["error"] is None
    ref = staged["result"]["ref"]

    text = "queue résumé 你好"
    enqueued = await _call(
        server,
        "state.web_queue.enqueue_batch",
        {
            **binding,
            "operation_id": ids["operation_ids"][0],
            "batch_id": ids["batch_id"],
            "items": [
                {
                    "request_id": ids["request_ids"][0],
                    "payload_b64": base64.b64encode(
                        json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
                    ).decode("ascii"),
                    "attachment_refs": [ref],
                }
            ],
        },
    )
    assert enqueued["error"] is None

    snapshot = await _call(server, "state.web_queue.snapshot", binding)
    item = snapshot["result"]["batches"][0]["items"][0]
    assert item["request_id"] == ids["request_ids"][0]
    assert item["attachment_refs"] == [ref]
    assert json.loads(base64.b64decode(item["payload_b64"])) == {"text": text}

    opened = await _call(
        server,
        "state.web_queue.open_attachment",
        {**binding, "request_id": ids["request_ids"][0], "ref": ref},
    )
    assert opened["error"] is None
    assert base64.b64decode(opened["result"]["payload_b64"]) == image

    denied_claim = await _call(
        server,
        "state.web_queue.claim_batch",
        {**binding, "operation_id": ids["operation_ids"][1], "batch_id": ids["batch_id"]},
    )
    assert denied_claim["error_kind"] == "not_found"

    removed = await _call(
        server,
        "state.web_queue.remove_item",
        {
            **binding,
            "operation_id": ids["operation_ids"][1],
            "request_id": ids["request_ids"][0],
        },
    )
    assert removed["error"] is None
    after_remove = await _call(
        server,
        "state.web_queue.open_attachment",
        {**binding, "request_id": ids["request_ids"][0], "ref": ref},
    )
    assert after_remove["error_kind"] == "handler"


@pytest.mark.asyncio
async def test_runtime_rejects_missing_uid_and_unauthenticated_transport():
    server = RpcServer()
    register_web_message_queue_runtime(server, _ConversationState("conversation-a"))

    missing_uid = await _call(server, "state.web_queue.snapshot", {})
    assert missing_uid["error_kind"] == "handler"

    unauthenticated = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "queue-runtime-unauthenticated",
            "method": "state.web_queue.snapshot",
            "params": {"expected_conversation_uid": "conversation-a"},
        },
        caller_context=None,
    )
    assert unauthenticated["error_kind"] == "handler"


@pytest.mark.asyncio
async def test_runtime_keeps_dispatch_capability_explicitly_false():
    server = RpcServer()
    register_web_message_queue_runtime(server, _ConversationState("conversation-a"))

    response = await _call(
        server,
        "state.web_queue.capabilities",
        {"expected_conversation_uid": "conversation-a"},
    )
    assert response["error"] is None
    assert response["result"] == {"dispatch_available": False}
