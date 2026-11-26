"""HTTP/SSE bridge to a session daemon's ordered chat stream (#1451)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sse_starlette.sse import EventSourceResponse

from kollabor_events.chat_stream import ROLES, ROW_KINDS
from kollabor_rpc import RpcError, RpcHandlerError, RpcMethodNotFound

from ..server import get_session_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["chat-stream"])
_ROW_FIELDS = frozenset(
    {"id", "seq", "kind", "role", "turn_id", "status", "parts", "created_at"}
)


class InputAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=256)
    content_type: StrictStr = Field(min_length=1, max_length=128)
    data_url: StrictStr = Field(min_length=1)


class InputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr = Field(min_length=1, max_length=256)
    text: StrictStr
    attachments: list[InputAttachment] = Field(default_factory=list, max_length=20)
    goal_reply_id: StrictStr | None = Field(
        default=None, pattern=r"^goal-[0-9a-f]{32}$"
    )


class GoalReplyPollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_reply_id: StrictStr = Field(pattern=r"^goal-[0-9a-f]{32}$")


def _get_session(session_id: str) -> Any:
    session = get_session_registry().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _get_rpc(session_id: str) -> tuple[Any, Any]:
    session = _get_session(session_id)
    daemon = getattr(session, "daemon", None)
    rpc = getattr(daemon, "rpc", None) if daemon is not None else None
    if not callable(getattr(rpc, "call", None)):
        raise HTTPException(
            status_code=503, detail="The session daemon is unavailable."
        )
    return session, rpc


async def _call_daemon(session_id: str, method: str, params: dict[str, Any]) -> Any:
    _session, rpc = _get_rpc(session_id)
    try:
        return await rpc.call(method, params, timeout=15.0)
    except RpcMethodNotFound:
        raise HTTPException(
            status_code=503,
            detail="This agent's local chat stream is unavailable.",
        ) from None
    except RpcHandlerError as exc:
        logger.debug("chat stream daemon action %s failed: %s", method, exc)
        raise HTTPException(
            status_code=502,
            detail="The local daemon could not complete the chat action.",
        ) from None
    except RpcError:
        raise HTTPException(
            status_code=503, detail="The session daemon is unavailable."
        ) from None


def _event_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    event = value.get("event") if value.get("type") == "chat_stream" else None
    if not isinstance(event, dict):
        return None
    if (
        not isinstance(event.get("seq"), int)
        or isinstance(event.get("seq"), bool)
        or not isinstance(event.get("conversation_uid"), str)
        or not isinstance(event.get("type"), str)
        or not isinstance(event.get("data"), dict)
    ):
        return None
    return event


def _sse_event(event: dict[str, Any]) -> dict[str, str]:
    # The data line is the whole protocol event, including its contract type.
    return {"data": json.dumps(event, ensure_ascii=False, separators=(",", ":"))}


def _cursor_after(event: dict[str, Any]) -> int:
    data = event.get("data")
    if event.get("type") == "snapshot" and isinstance(data, dict):
        next_seq = data.get("next_seq")
        if (
            isinstance(next_seq, int)
            and not isinstance(next_seq, bool)
            and next_seq >= 0
        ):
            return next_seq - 1
    seq = event.get("seq")
    return seq if isinstance(seq, int) and not isinstance(seq, bool) else -1


async def _read_events(
    session_id: str,
    *,
    after: int | None,
    conversation_uid: str | None,
) -> list[dict[str, Any]]:
    result = await _call_daemon(
        session_id,
        "state.chat_stream.subscribe",
        {"after": after, "conversation_uid": conversation_uid},
    )
    events = result.get("events") if isinstance(result, dict) else None
    if not isinstance(events, list) or any(
        not isinstance(event, dict) for event in events
    ):
        raise HTTPException(
            status_code=502, detail="The local daemon returned an invalid chat stream."
        )
    return events


def _valid_row(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _ROW_FIELDS:
        return False
    seq = value.get("seq")
    kind = value.get("kind")
    role = value.get("role")
    return (
        isinstance(value.get("id"), str)
        and bool(value["id"])
        and isinstance(seq, int)
        and not isinstance(seq, bool)
        and seq >= 0
        and isinstance(kind, str)
        and kind in ROW_KINDS
        and isinstance(role, str)
        and role in ROLES
        and (value.get("turn_id") is None or isinstance(value["turn_id"], str))
        and isinstance(value.get("status"), str)
        and isinstance(value.get("parts"), list)
        and all(isinstance(part, dict) for part in value["parts"])
        and isinstance(value.get("created_at"), str)
    )


@router.get("/{session_id}/rows")
async def older_rows(
    session_id: str,
    before: str | None = Query(default=None, max_length=8192),
    limit: int = Query(default=50, ge=1, le=100),
):
    """Return an older page in the daemon's chat stream row shape."""
    result = await _call_daemon(
        session_id,
        "state.chat_stream.rows",
        {"before": before, "limit": limit},
    )
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, str) and error in {
            "stale_cursor",
            "conversation_changed",
        }:
            raise HTTPException(
                status_code=409,
                detail="The conversation changed. Reload recent rows and try again.",
            )
        if error == "invalid_cursor":
            raise HTTPException(status_code=400, detail="Invalid row cursor.")
        if error == "invalid_request":
            raise HTTPException(status_code=400, detail="Invalid row request.")

    rows = result.get("rows") if isinstance(result, dict) else None
    older = result.get("older") if isinstance(result, dict) else None
    if (
        not isinstance(rows, list)
        or any(not _valid_row(row) for row in rows)
        or (
            older is not None
            and (not isinstance(older, str) or not older or len(older) > 8192)
        )
    ):
        raise HTTPException(
            status_code=502, detail="The local daemon returned invalid chat rows."
        )
    return {"rows": rows, "older": older}


@router.get("/{session_id}/stream")
async def chat_stream(
    session_id: str,
    request: Request,
    after: int | None = Query(default=None, ge=0),
    conversation_uid: str | None = Query(default=None, min_length=1, max_length=256),
):
    """Relay initial replay and live daemon events as SSE data lines."""
    session, _rpc = _get_rpc(session_id)
    try:
        queue = session.subscribe()
    except AttributeError, RuntimeError:
        raise HTTPException(
            status_code=503, detail="The session event stream is unavailable."
        ) from None

    try:
        initial = await _read_events(
            session_id,
            after=after,
            conversation_uid=conversation_uid,
        )
    except Exception:
        session.unsubscribe(queue)
        raise

    async def event_generator():
        last_seq = -1
        last_uid = conversation_uid
        received_initial_cursor = False
        try:
            for event in initial:
                seq = event.get("seq")
                uid = event.get("conversation_uid")
                if (
                    not isinstance(seq, int)
                    or isinstance(seq, bool)
                    or not isinstance(uid, str)
                ):
                    continue
                if seq <= last_seq:
                    continue
                last_seq, last_uid = _cursor_after(event), uid
                received_initial_cursor = True
                yield _sse_event(event)
            if (
                not received_initial_cursor
                and isinstance(after, int)
                and not isinstance(after, bool)
                and after >= 0
            ):
                last_seq = after

            while not await request.is_disconnected():
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
                    continue
                if frame is None or getattr(frame, "name", None) == "daemon_closed":
                    return

                event = _event_payload(frame)
                if event is None:
                    continue
                seq = event["seq"]
                event_uid = event["conversation_uid"]
                if event_uid != last_uid:
                    snapshot = await _read_events(
                        session_id,
                        after=None,
                        conversation_uid=event_uid,
                    )
                    refreshed = False
                    for current in snapshot:
                        current_uid = current.get("conversation_uid")
                        current_seq = current.get("seq")
                        if (
                            current.get("type") != "snapshot"
                            or not isinstance(current_uid, str)
                            or not isinstance(current_seq, int)
                            or isinstance(current_seq, bool)
                        ):
                            continue
                        last_seq = _cursor_after(current)
                        last_uid = current_uid
                        yield _sse_event(current)
                        refreshed = True
                        break
                    if not refreshed or event_uid != last_uid:
                        continue
                if seq <= last_seq:
                    continue
                if last_seq >= 0 and seq > last_seq + 1:
                    replay = await _read_events(
                        session_id,
                        after=last_seq,
                        conversation_uid=last_uid,
                    )
                    for missed in replay:
                        missed_seq = missed.get("seq")
                        missed_uid = missed.get("conversation_uid")
                        if missed.get("type") == "snapshot":
                            if not isinstance(missed_uid, str):
                                continue
                            last_seq, last_uid = _cursor_after(missed), missed_uid
                            yield _sse_event(missed)
                            continue
                        if (
                            not isinstance(missed_seq, int)
                            or isinstance(missed_seq, bool)
                            or not isinstance(missed_uid, str)
                            or missed_seq <= last_seq
                        ):
                            continue
                        last_seq, last_uid = missed_seq, missed_uid
                        yield _sse_event(missed)
                    if seq <= last_seq:
                        continue
                last_seq, last_uid = _cursor_after(event), event["conversation_uid"]
                yield _sse_event(event)
        finally:
            session.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@router.post("/{session_id}/inputs", status_code=202)
async def add_input(session_id: str, body: InputRequest):
    result = await _call_daemon(
        session_id,
        "state.chat_stream.add_input",
        {
            "id": body.id,
            "text": body.text,
            "attachments": [attachment.model_dump() for attachment in body.attachments],
            **(
                {"goal_reply_id": body.goal_reply_id}
                if body.goal_reply_id is not None
                else {}
            ),
        },
    )
    if isinstance(result, dict) and result.get("error") == (
        "input id is already bound to different content"
    ):
        raise HTTPException(
            status_code=409,
            detail="This input ID is already bound to different content.",
        )
    if isinstance(result, dict) and result.get("error") == "input was not admitted":
        raise HTTPException(status_code=422, detail="The input was not admitted.")
    if (
        not isinstance(result, dict)
        or result.get("id") != body.id
        or not isinstance(result.get("state"), str)
    ):
        raise HTTPException(
            status_code=502, detail="The local daemon returned an invalid input result."
        )
    return {"id": body.id, "state": result["state"]}


@router.post("/{session_id}/goal-replies/poll")
async def poll_goal_reply(session_id: str, body: GoalReplyPollRequest):
    session = _get_session(session_id)
    poll = getattr(session, "poll_goal_reply", None)
    if not callable(poll):
        raise HTTPException(status_code=503, detail="Goal replies are unavailable.")
    result = await poll(body.goal_reply_id)
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="The local daemon returned an invalid goal reply.")
    status = result.get("status")
    if status == "pending":
        return {"status": "pending"}
    if status == "missing":
        return {"status": "missing", "message": "goal reply expired or is unavailable"}
    if (
        status != "complete"
        or not isinstance(result.get("success"), bool)
        or not isinstance(result.get("message"), str)
    ):
        raise HTTPException(
            status_code=502,
            detail="The local daemon returned an invalid goal reply.",
        )
    reply = {
        "status": "complete",
        "success": result["success"],
        "message": result["message"],
    }
    for field in ("goal_id", "goal_request_id", "request_id"):
        value = result.get(field)
        if isinstance(value, str):
            reply[field] = value
    return reply


@router.post("/{session_id}/inputs/{input_id}/cancel")
async def cancel_input(session_id: str, input_id: str):
    result = await _call_daemon(
        session_id,
        "state.chat_stream.cancel_queued_input",
        {"id": input_id},
    )
    if not isinstance(result, dict) or result.get("found") is not True:
        raise HTTPException(status_code=404, detail="Input not found")
    if result.get("cancelled") is not True:
        raise HTTPException(status_code=409, detail="Input is no longer queued")
    return {"id": input_id, "cancelled": True}


@router.post("/{session_id}/inputs/{input_id}/send-now", status_code=202)
async def send_input_now(session_id: str, input_id: str):
    result = await _call_daemon(
        session_id,
        "state.chat_stream.send_now",
        {"id": input_id},
    )
    if not isinstance(result, dict) or result.get("found") is not True:
        raise HTTPException(status_code=404, detail="Input not found")
    state = result.get("state")
    accepted = result.get("accepted")
    turn_id = result.get("turn_id")
    if (
        not isinstance(state, str)
        or state not in {"queued", "started", "joined", "ended"}
        or not isinstance(accepted, bool)
        or (turn_id is not None and (not isinstance(turn_id, str) or not turn_id))
        or (state in {"started", "joined"} and not turn_id)
    ):
        raise HTTPException(
            status_code=502, detail="The local daemon returned an invalid input state."
        )
    already_moved = state in {"started", "joined"} or (
        state == "ended" and turn_id is not None
    )
    if not accepted and not already_moved:
        raise HTTPException(status_code=409, detail="Input is no longer queued")
    return JSONResponse(
        status_code=202 if accepted else 200,
        content={"id": input_id, "state": state, "turn_id": turn_id},
    )
