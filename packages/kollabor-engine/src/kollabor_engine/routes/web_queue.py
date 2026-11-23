"""HTTP bridge to the daemon-owned WebUI message queue."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from kollabor_ai.message_content import DEFAULT_MAX_IMAGE_BYTES
from kollabor_rpc import (
    WEB_QUEUE_CAPACITY_REJECTED,
    RpcError,
    RpcHandlerError,
    RpcMethodNotFound,
)

from ..server import get_session_registry

router = APIRouter(prefix="/sessions", tags=["web-message-queue"])

_QUEUE_PATH = "/web-message-queue"
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_MAX_QUEUE_ITEMS = 20
_MAX_QUEUE_PAYLOAD_BYTES = 1024 * 1024


class QueueIdsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_uid: str = Field(min_length=1, max_length=256)
    item_count: int = Field(ge=1, le=_MAX_QUEUE_ITEMS)
    operation_count: int = Field(default=4, ge=1, le=64)
    attachment_operation_count: int = Field(default=0, ge=0, le=64)


class QueueEnqueueItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=256)
    text: str
    attachment_refs: list[str] = Field(default_factory=list, max_length=20)


class QueueEnqueueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_uid: str = Field(min_length=1, max_length=256)
    operation_id: str = Field(min_length=1, max_length=256)
    batch_id: str = Field(min_length=1, max_length=256)
    items: list[QueueEnqueueItemRequest] = Field(
        min_length=1,
        max_length=_MAX_QUEUE_ITEMS,
    )


def _session_rpc(session_id: str):
    session = get_session_registry().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    daemon = getattr(session, "daemon", None)
    rpc = getattr(daemon, "rpc", None) if daemon is not None else None
    call = getattr(rpc, "call", None)
    if not callable(call):
        raise HTTPException(
            status_code=503,
            detail="This agent's local daemon queue is unavailable.",
        )
    return rpc


async def _call_queue_rpc(session_id: str, method: str, params: dict[str, Any]) -> Any:
    rpc = _session_rpc(session_id)
    try:
        result = await rpc.call(method, params, timeout=15.0)
    except RpcMethodNotFound:
        raise HTTPException(
            status_code=503,
            detail="This agent's local daemon queue is unavailable.",
        ) from None
    except RpcHandlerError as exc:
        if exc.remote_message == WEB_QUEUE_CAPACITY_REJECTED:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "queue_capacity",
                    "message": "The queue is full. No message was admitted.",
                },
            ) from None
        raise HTTPException(
            status_code=409,
            detail="The daemon rejected this queue operation.",
        ) from None
    except RpcError:
        raise HTTPException(
            status_code=503,
            detail="The local daemon did not complete this queue operation.",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="The local daemon did not complete this queue operation.",
        ) from None
    if not isinstance(result, dict):
        raise HTTPException(
            status_code=502,
            detail="The local daemon returned an invalid queue response.",
        )
    return result


@router.get("/{session_id}/web-message-queue")
async def get_queue_snapshot(session_id: str, conversation_uid: str):
    """Read a daemon snapshot; the UID is only a CAS precondition."""
    params = {"expected_conversation_uid": conversation_uid}
    snapshot = await _call_queue_rpc(
        session_id,
        "state.web_queue.snapshot",
        params,
    )
    try:
        capability = await _call_queue_rpc(
            session_id,
            "state.web_queue.capabilities",
            params,
        )
    except HTTPException:
        capability = {"dispatch_available": False}
    snapshot["dispatch_available"] = capability.get("dispatch_available") is True
    return snapshot


@router.post("/{session_id}/web-message-queue/ids")
async def allocate_queue_ids(session_id: str, body: QueueIdsRequest):
    return await _call_queue_rpc(
        session_id,
        "state.web_queue.allocate_ids",
        {
            "expected_conversation_uid": body.conversation_uid,
            "item_count": body.item_count,
            "operation_count": body.operation_count,
            "attachment_operation_count": body.attachment_operation_count,
        },
    )


@router.put("/{session_id}/web-message-queue/attachments/{operation_id}")
async def stage_queue_attachment(
    session_id: str,
    operation_id: str,
    conversation_uid: str,
    request: Request,
):
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in _IMAGE_MIMES:
        raise HTTPException(status_code=415, detail="Unsupported image type.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > DEFAULT_MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413, detail="Image exceeds the 10 MiB limit."
            )
        data.extend(chunk)
    if not data:
        raise HTTPException(status_code=400, detail="Image data is empty.")
    return await _call_queue_rpc(
        session_id,
        "state.web_queue.stage_attachment",
        {
            "expected_conversation_uid": conversation_uid,
            "operation_id": operation_id,
            "mime": mime,
            "filename": unquote(request.headers.get("x-file-name", "Image")),
            "payload_b64": base64.b64encode(data).decode("ascii"),
        },
    )


@router.post("/{session_id}/web-message-queue/batches")
async def enqueue_queue_batch(session_id: str, body: QueueEnqueueRequest):
    items = []
    for item in body.items:
        payload = json.dumps(
            {"text": item.text},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _MAX_QUEUE_PAYLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail="Message exceeds the queue item limit."
            )
        items.append(
            {
                "request_id": item.request_id,
                "payload_b64": base64.b64encode(payload).decode("ascii"),
                "attachment_refs": item.attachment_refs,
            }
        )
    return await _call_queue_rpc(
        session_id,
        "state.web_queue.enqueue_batch",
        {
            "expected_conversation_uid": body.conversation_uid,
            "operation_id": body.operation_id,
            "batch_id": body.batch_id,
            "items": items,
        },
    )


@router.delete("/{session_id}/web-message-queue/items/{request_id}")
async def remove_queue_item(
    session_id: str,
    request_id: str,
    conversation_uid: str,
    operation_id: str,
):
    return await _call_queue_rpc(
        session_id,
        "state.web_queue.remove_item",
        {
            "expected_conversation_uid": conversation_uid,
            "operation_id": operation_id,
            "request_id": request_id,
        },
    )


@router.get("/{session_id}/web-message-queue/items/{request_id}/attachments/{ref}")
async def get_queue_attachment(
    session_id: str,
    conversation_uid: str,
    request_id: str,
    ref: str,
):
    result = await _call_queue_rpc(
        session_id,
        "state.web_queue.open_attachment",
        {
            "expected_conversation_uid": conversation_uid,
            "request_id": request_id,
            "ref": ref,
        },
    )
    mime = result.get("mime")
    encoded = result.get("payload_b64")
    if mime not in _IMAGE_MIMES or not isinstance(encoded, str):
        raise HTTPException(
            status_code=502, detail="The daemon returned invalid image data."
        )
    try:
        data = base64.b64decode(encoded, validate=True)
    except binascii.Error, ValueError:
        raise HTTPException(
            status_code=502, detail="The daemon returned invalid image data."
        ) from None
    if len(data) > DEFAULT_MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=502, detail="The daemon returned oversized image data."
        )
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{session_id}/web-message-queue/batches/{batch_id}/send-now")
async def send_queue_batch_now(session_id: str, conversation_uid: str, batch_id: str):
    """Ask the authenticated daemon worker to dispatch one exact queued batch."""
    _session_rpc(session_id)
    if not conversation_uid or not batch_id:
        raise HTTPException(status_code=400, detail="Queue identity is required.")
    capability = await _call_queue_rpc(
        session_id,
        "state.web_queue.capabilities",
        {"expected_conversation_uid": conversation_uid},
    )
    if capability.get("dispatch_available") is not True:
        raise HTTPException(
            status_code=503,
            detail="Queue dispatch is unavailable; the message remains queued.",
        )
    return await _call_queue_rpc(
        session_id,
        "state.web_queue.send_now",
        {
            "expected_conversation_uid": conversation_uid,
            "batch_id": batch_id,
        },
    )
