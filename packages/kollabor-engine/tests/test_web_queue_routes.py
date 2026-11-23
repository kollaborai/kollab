"""HTTP bridge tests for daemon-owned WebUI queue management."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kollabor_engine.routes import web_queue


class _Rpc:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method: str, params: dict, *, timeout: float | None = None):
        self.calls.append((method, params))
        if method == "state.web_queue.capabilities":
            return {"dispatch_available": False}
        if method == "state.web_queue.allocate_ids":
            return {
                "scope_id": "scope-safe",
                "incarnation": "inc-1",
                "operation_ids": ["op-1", "op-2"],
                "batch_id": "batch-1",
                "request_ids": ["item-1"],
                "attachment_operation_ids": ["att-op-1"],
            }
        if method == "state.web_queue.enqueue_batch":
            return {"status": "queued", "batch_id": "batch-1"}
        if method == "state.web_queue.snapshot":
            return {"scope_id": "scope-safe", "incarnation": "inc-1", "revision": 0, "batches": []}
        if method == "state.web_queue.stage_attachment":
            return {"ref": "att-ref", "mime": "image/png", "size": len(base64.b64decode(params["payload_b64"]))}
        if method == "state.web_queue.remove_item":
            return {"status": "removed", "request_ids": [params["request_id"]]}
        if method == "state.web_queue.open_attachment":
            return {"mime": "image/png", "payload_b64": base64.b64encode(b"image").decode("ascii")}
        raise AssertionError(f"unexpected daemon RPC method: {method}")


class _Registry(dict):
    pass


def _install_session(monkeypatch):
    rpc = _Rpc()
    session = SimpleNamespace(daemon=SimpleNamespace(rpc=rpc), session_id="s1")
    monkeypatch.setattr(web_queue, "get_session_registry", lambda: _Registry(s1=session))
    return rpc


@pytest.mark.asyncio
async def test_snapshot_forwards_only_conversation_precondition_and_reports_dispatch_state(monkeypatch):
    rpc = _install_session(monkeypatch)

    result = await web_queue.get_queue_snapshot("s1", "conversation-a")

    assert result == {
        "scope_id": "scope-safe",
        "incarnation": "inc-1",
        "revision": 0,
        "batches": [],
        "dispatch_available": False,
    }
    assert rpc.calls == [
        ("state.web_queue.snapshot", {"expected_conversation_uid": "conversation-a"}),
        ("state.web_queue.capabilities", {"expected_conversation_uid": "conversation-a"}),
    ]


@pytest.mark.asyncio
async def test_enqueue_serializes_unicode_text_and_attachment_refs_without_scope_fields(monkeypatch):
    rpc = _install_session(monkeypatch)
    body = web_queue.QueueEnqueueRequest(
        conversation_uid="conversation-a",
        operation_id="op-1",
        batch_id="batch-1",
        items=[
            web_queue.QueueEnqueueItemRequest(
                request_id="item-1",
                text="café 你好",
                attachment_refs=["att-ref"],
            )
        ],
    )

    result = await web_queue.enqueue_queue_batch("s1", body)

    assert result["status"] == "queued"
    method, params = rpc.calls[0]
    assert method == "state.web_queue.enqueue_batch"
    assert params["expected_conversation_uid"] == "conversation-a"
    assert set(params).isdisjoint({"scope_id", "principal_id", "session_id"})
    item = params["items"][0]
    assert json.loads(base64.b64decode(item["payload_b64"])) == {"text": "café 你好"}
    assert item["attachment_refs"] == ["att-ref"]


@pytest.mark.asyncio
async def test_send_now_fails_closed_without_touching_queue_or_dispatch(monkeypatch):
    rpc = _install_session(monkeypatch)

    with pytest.raises(HTTPException) as error:
        await web_queue.send_queue_batch_now("s1", "conversation-a", "batch-1")

    assert error.value.status_code == 503
    assert "remains queued" in str(error.value.detail)
    assert rpc.calls == []


@pytest.mark.asyncio
async def test_attachment_upload_enforces_the_streamed_image_bound_before_rpc(monkeypatch):
    rpc = _install_session(monkeypatch)
    chunks = [b"a" * (10 * 1024 * 1024), b"b"]
    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}
    from starlette.requests import Request
    request = Request(
        {"type": "http", "headers": [(b"content-type", b"image/png")], "method": "PUT"},
        receive,
    )

    with pytest.raises(HTTPException) as error:
        await web_queue.stage_queue_attachment("s1", "att-op-1", "conversation-a", request)

    assert error.value.status_code == 413
    assert rpc.calls == []


@pytest.mark.asyncio
async def test_route_returns_media_bytes_from_the_daemon_owner(monkeypatch):
    rpc = _install_session(monkeypatch)

    response = await web_queue.get_queue_attachment(
        "s1", "conversation-a", "item-1", "att-ref"
    )

    assert response.body == b"image"
    assert response.media_type == "image/png"
    assert rpc.calls[0] == (
        "state.web_queue.open_attachment",
        {
            "expected_conversation_uid": "conversation-a",
            "request_id": "item-1",
            "ref": "att-ref",
        },
    )
