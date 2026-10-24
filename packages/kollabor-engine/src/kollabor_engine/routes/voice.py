"""Voice session control API, voice-session SSE and the two worker routes (/turns, /events)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from ..voice_control import VoiceControl, WorkerEvent, encode_cursor
from ..voice_runtime_host import RuntimeHostError
from ..voice_session_router import MAX_FINAL_TRANSCRIPT_BYTES
from ..voice_types import (
    CreateVoiceSessionRequest,
    JoinVoiceSessionRequest,
    StopVoiceSessionRequest,
    VoiceTranscriptSnapshot,
    VoiceTurnRequest,
)

router = APIRouter(tags=["voice"])


def _control(request: Request) -> VoiceControl:
    control = getattr(request.app.state, "voice", None)
    if control is None:
        raise RuntimeHostError(
            "runtime_unavailable", 503, "Voice is not available in this engine."
        )
    return control


def error_response(exc: RuntimeHostError) -> JSONResponse:
    headers = {"Retry-After": "2"} if exc.status_code == 409 and exc.retryable else None
    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={
            "detail": exc.detail,
            "code": exc.code,
            "message": exc.detail,
            "retryable": exc.retryable,
            "next_action": None,
        },
    )


async def _guard(fn):
    try:
        return await fn()
    except RuntimeHostError as exc:
        return error_response(exc)


def _settings(request: Request):
    settings = _control(request).settings
    if settings is None:
        raise RuntimeHostError(
            "runtime_unavailable", 503, "Voice settings are not available in this engine."
        )
    return settings


def _prompt_agent(request: Request, session_id: str | None, thread_id: str | None):
    """Agent whose voice prompt is addressed; None addresses the default prompt."""
    if session_id is None:
        return None
    session = _control(request)._registry().get(session_id)  # noqa: SLF001
    if session is None:
        raise RuntimeHostError("session_not_found", 404, "Session was not found.")
    if thread_id is not None and thread_id not in getattr(session, "known_thread_ids", ()):
        raise RuntimeHostError(
            "target_mismatch", 409, "The selected thread does not belong to this session."
        )
    daemon = getattr(session, "daemon", None)
    return session.agent or getattr(daemon, "agent_name", "") or "default"


@router.get("/voice/settings")
async def get_voice_settings(request: Request):
    async def run():
        return JSONResponse(await _settings(request).get_settings())

    return await _guard(run)


@router.put("/voice/settings")
async def put_voice_settings(request: Request, payload: dict = Body(...)):
    async def run():
        return JSONResponse(await _settings(request).update_settings(payload))

    return await _guard(run)


@router.get("/voice/settings/prompt")
async def get_voice_prompt(
    request: Request,
    session_id: str | None = Query(default=None),
    thread_id: str | None = Query(default=None),
):
    async def run():
        agent = _prompt_agent(request, session_id, thread_id)
        return JSONResponse(_settings(request).get_prompt(agent))

    return await _guard(run)


@router.put("/voice/settings/prompt")
async def put_voice_prompt(
    request: Request,
    payload: dict = Body(...),
    session_id: str | None = Query(default=None),
    thread_id: str | None = Query(default=None),
):
    async def run():
        agent = _prompt_agent(request, session_id, thread_id)
        return JSONResponse(_settings(request).update_prompt(agent, payload))

    return await _guard(run)


@router.post("/sessions/{control_session_id}/voice", status_code=202)
async def create_voice_session(
    request: Request,
    control_session_id: str,
    body: CreateVoiceSessionRequest = Body(default_factory=CreateVoiceSessionRequest),
):
    async def run():
        view = await _control(request).create(control_session_id, body)
        return JSONResponse(view.model_dump(mode="json"), status_code=202)

    return await _guard(run)


@router.post("/voice/sessions/{voice_session_id}/join")
async def join_voice_session(
    request: Request, voice_session_id: str, body: JoinVoiceSessionRequest
):
    async def run():
        ticket = await _control(request).join(voice_session_id, body)
        return JSONResponse(
            ticket.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
        )

    return await _guard(run)


@router.get("/voice/sessions/{voice_session_id}")
async def voice_session_status(request: Request, voice_session_id: str):
    async def run():
        return JSONResponse(
            (await _control(request).status(voice_session_id)).model_dump(mode="json")
        )

    return await _guard(run)


@router.delete("/voice/sessions/{voice_session_id}")
async def stop_voice_session(
    request: Request, voice_session_id: str, body: StopVoiceSessionRequest
):
    async def run():
        return JSONResponse(
            (await _control(request).stop(voice_session_id, body)).model_dump(
                mode="json"
            )
        )

    return await _guard(run)


@router.get("/voice/sessions/{voice_session_id}/events")
async def voice_session_events(
    request: Request, voice_session_id: str, cursor: str | None = Query(default=None)
):
    try:
        stream = await _control(request).subscribe(
            voice_session_id, cursor or request.headers.get("last-event-id")
        )
    except RuntimeHostError as exc:
        return error_response(exc)

    async def generator():
        async for envelope in stream:
            yield {
                "id": encode_cursor(
                    envelope["voice_session_id"],
                    envelope["generation"],
                    envelope["sequence"],
                ),
                "data": json.dumps(envelope, ensure_ascii=False),
            }

    return EventSourceResponse(generator())


# --- worker -> engine (bearer engine token, same auth middleware) ------------


@router.post("/voice/sessions/{voice_session_id}/turns")
async def worker_turn(request: Request, voice_session_id: str, body: VoiceTurnRequest):
    """Admit a turn and stream the reply as NDJSON speech sentences (see VoiceControl.open_turn)."""
    try:
        if body.kind == "speech" and len(body.display_text.encode()) > MAX_FINAL_TRANSCRIPT_BYTES:
            raise RuntimeHostError(
                "transcript_too_large", 413, "Voice transcript exceeds the size limit."
            )
        if body.voice_session_id != voice_session_id:
            raise RuntimeHostError(
                "target_mismatch", 409, "Transcript belongs to another voice session."
            )
        turn = await _control(request).open_turn(
            voice_session_id,
            VoiceTranscriptSnapshot(**body.model_dump(exclude={"kind"})),
            kind=body.kind,
        )
    except RuntimeHostError as exc:
        return error_response(exc)

    async def lines():
        try:
            async for item in turn:
                yield json.dumps(item, ensure_ascii=False) + "\n"
        finally:
            # A client that goes away mid-reply closes the turn: that is a barge-in.
            await turn.aclose()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.post("/voice/sessions/{voice_session_id}/events")
async def worker_event(
    request: Request, voice_session_id: str, payload: dict = Body(...)
):
    try:
        body = WorkerEvent(**payload)
    except ValidationError, TypeError:
        raise HTTPException(400, "Invalid voice event") from None

    async def run():
        await _control(request).ingest_event(voice_session_id, body)
        return JSONResponse({"ok": True})

    return await _guard(run)
