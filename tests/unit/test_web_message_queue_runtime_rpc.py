"""Composition tests for the daemon-owned WebUI queue management RPC."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from kollabor.state.web_message_queue_runtime import register_web_message_queue_runtime
from kollabor_rpc import RpcCallerContext, RpcServer


class _ConversationState:
    def __init__(self, uid: str | None):
        self.uid = uid

    def get_conversation_uid(self) -> str | None:
        return self.uid


class _DispatchState(_ConversationState):
    def __init__(self, uid: str):
        super().__init__(uid)
        from kollabor.state.web_message_queue_worker import ConversationIdentity

        self.identity = ConversationIdentity(uid, 7)
        self.is_processing = True
        self.dispatches = []
        self.batch_starts = []
        self.dispatched = asyncio.Event()

    def get_conversation_identity(self):
        return self.identity

    async def submit_queue_message(self, message, *, queue_handoff, batch_lease=None):
        self.dispatches.append((message, queue_handoff, batch_lease))
        queue_handoff.provider_custody_id = f"test:{queue_handoff.request_id}"
        queue_handoff.recording_status = "recorded"
        self.dispatched.set()
        return {"status": "queued"}

    async def begin_queue_handoff_batch(self, request):
        self.batch_starts.append(request)
        return object()

    def finish_queue_handoff_batch(self, batch_lease, *, accepted_count, uncertain):
        return accepted_count == len(self.batch_starts[-1].request_ids) and not uncertain


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

    image = b"\x89PNG\r\n\x1a\nbounded image bytes"
    staged = await _call(
        server,
        "state.web_queue.stage_attachment",
        {
            **binding,
            "operation_id": ids["attachment_operation_ids"][0],
            "mime": "image/png",
            "filename": "../private/queue résumé.png",
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
    assert item["text_preview"] == text
    assert item["attachment_labels"] == ["queue résumé.png"]
    assert "payload_b64" not in item and "attachment_refs" not in item

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
async def test_runtime_rejects_declared_image_mime_that_disagrees_with_bytes():
    server = RpcServer()
    state = _ConversationState("conversation-a")
    runtime = register_web_message_queue_runtime(server, state)
    binding = {"expected_conversation_uid": "conversation-a"}

    allocated = await _call(
        server,
        "state.web_queue.allocate_ids",
        {**binding, "item_count": 1, "operation_count": 1, "attachment_operation_count": 1},
    )
    operation_id = allocated["result"]["attachment_operation_ids"][0]
    png_bytes = b"\x89PNG\r\n\x1a\nnot-a-jpeg"
    staged = await _call(
        server,
        "state.web_queue.stage_attachment",
        {
            **binding,
            "operation_id": operation_id,
            "mime": "image/jpeg",
            "payload_b64": base64.b64encode(png_bytes).decode("ascii"),
        },
    )

    assert staged["error_kind"] == "handler"
    assert runtime.attachments.attachment_count == 0


@pytest.mark.asyncio
async def test_runtime_registers_trusted_send_now_dispatch_for_a_busy_batch():
    server = RpcServer()
    state = _DispatchState("conversation-a")
    runtime = register_web_message_queue_runtime(server, state)
    binding = {"expected_conversation_uid": "conversation-a"}

    ids = await _call(
        server,
        "state.web_queue.allocate_ids",
        {**binding, "item_count": 2},
    )
    assert ids["error"] is None
    minted = ids["result"]
    texts = ("forced B", "forced C")
    batch = await _call(
        server,
        "state.web_queue.enqueue_batch",
        {
            **binding,
            "operation_id": minted["operation_ids"][0],
            "batch_id": minted["batch_id"],
            "items": [
                {
                    "request_id": request_id,
                    "payload_b64": base64.b64encode(
                        json.dumps({"text": text}, separators=(",", ":")).encode()
                    ).decode("ascii"),
                }
                for request_id, text in zip(minted["request_ids"], texts)
            ],
        },
    )
    assert batch["error"] is None

    result = await _call(
        server,
        "state.web_queue.send_now",
        {**binding, "batch_id": minted["batch_id"]},
    )

    assert runtime.dispatch_available is True
    assert "state.web_queue.send_now" in server.list_methods()
    assert result["error"] is None
    assert result["result"]["status"] == "admitted"
    assert result["result"]["accepted_count"] == 2
    assert state.batch_starts[0].request_ids == tuple(minted["request_ids"])
    assert [call[1].request_id for call in state.dispatches] == minted["request_ids"]
    assert all(call[2] is not None for call in state.dispatches)
    assert (await runtime.close()) is True


@pytest.mark.asyncio
async def test_runtime_defers_normal_dispatch_until_the_current_turn_is_idle():
    server = RpcServer()
    state = _DispatchState("conversation-a")
    runtime = register_web_message_queue_runtime(server, state)
    binding = {"expected_conversation_uid": "conversation-a"}
    ids = await _call(
        server,
        "state.web_queue.allocate_ids",
        {**binding, "item_count": 1},
    )
    minted = ids["result"]
    await _call(
        server,
        "state.web_queue.enqueue_batch",
        {
            **binding,
            "operation_id": minted["operation_ids"][0],
            "batch_id": minted["batch_id"],
            "items": [
                {
                    "request_id": minted["request_ids"][0],
                    "payload_b64": base64.b64encode(
                        json.dumps({"text": "ordinary queue"}, separators=(",", ":")).encode()
                    ).decode("ascii"),
                }
            ],
        },
    )

    await asyncio.sleep(0.01)
    assert state.dispatches == []
    state.is_processing = False
    runtime.wake_all()
    await asyncio.wait_for(state.dispatched.wait(), timeout=1.0)
    assert [call[1].request_id for call in state.dispatches] == minted["request_ids"]
    assert state.dispatches[0][1].force_send is False
    assert await runtime.close() is True


@pytest.mark.asyncio
async def test_runtime_coalesces_invalidations_and_dispatches_all_queued_batches_fifo():
    server = RpcServer()
    state = _DispatchState("conversation-a")
    runtime = register_web_message_queue_runtime(server, state)
    binding = {"expected_conversation_uid": "conversation-a"}
    minted = []
    for text in ("first", "second"):
        ids = await _call(
            server,
            "state.web_queue.allocate_ids",
            {**binding, "item_count": 1},
        )
        values = ids["result"]
        minted.append(values["request_ids"][0])
        await _call(
            server,
            "state.web_queue.enqueue_batch",
            {
                **binding,
                "operation_id": values["operation_ids"][0],
                "batch_id": values["batch_id"],
                "items": [
                    {
                        "request_id": values["request_ids"][0],
                        "payload_b64": base64.b64encode(
                            json.dumps({"text": text}, separators=(",", ":")).encode()
                        ).decode("ascii"),
                    }
                ],
            },
        )
    await asyncio.sleep(0.01)
    assert state.dispatches == []
    assert runtime._changes.qsize() <= 1

    state.is_processing = False
    runtime.wake_all()

    async def wait_for_two_dispatches():
        while len(state.dispatches) < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_two_dispatches(), timeout=1.0)
    assert [call[1].request_id for call in state.dispatches] == minted
    assert await runtime.close() is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime", "image"),
    [
        ("image/png", b"\x89PNG\r\n\x1a\nimage"),
        ("image/jpeg", b"\xff\xd8\xff\xe0image"),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBPimage"),
        ("image/gif", b"GIF89aimage"),
    ],
)
async def test_runtime_accepts_each_supported_image_signature(mime, image):
    server = RpcServer()
    state = _ConversationState("conversation-a")
    runtime = register_web_message_queue_runtime(server, state)
    binding = {"expected_conversation_uid": "conversation-a"}
    allocated = await _call(
        server,
        "state.web_queue.allocate_ids",
        {**binding, "item_count": 1, "operation_count": 1, "attachment_operation_count": 1},
    )

    staged = await _call(
        server,
        "state.web_queue.stage_attachment",
        {
            **binding,
            "operation_id": allocated["result"]["attachment_operation_ids"][0],
            "mime": mime,
            "payload_b64": base64.b64encode(image).decode("ascii"),
        },
    )

    assert staged["error"] is None
    assert staged["result"]["mime"] == mime
    assert runtime.attachments.attachment_count == 1


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
