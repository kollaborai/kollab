"""Code editor routes: files, git and a terminal, served by the daemon of the session being used (#987).

Every route names a session, and the engine only passes the request on (``session.state.code_call``): the files,
the git repository and the shell belong to that session's daemon, so a session opened from another computer shows
that computer's. The daemon resolves the selected folder on its own computer; the engine's folder rule does not
apply (it guards folders the engine itself opens).

Success is ``{"success": true, "data": ...}``; a failure is a non-2xx ``{"success": false, "error": {"code",
"message"}}`` (docs/specs/floating-code-shell.md). Bodies and terminal input are never logged. Every answer carries
``Cache-Control: no-store``: it holds file contents.

``/sessions/{id}/code/terminal`` is outside the HTTP bearer middleware (WebSockets do not pass through it), so it
checks its own token: the first message must be ``{"type": "auth", "token": ...}`` within ``AUTH_TIMEOUT_SECONDS``,
else the socket closes with 4401. The socket is a bridge: each client message goes to the daemon, and the daemon's
output comes back through short polls.
"""

import asyncio
import contextlib
import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect  # type: ignore[import-not-found]
from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

from ..auth import validate_token
from ..server import get_session_registry
from ..session import EngineSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions/{session_id}/code", tags=["code"])

_NO_STORE = {"Cache-Control": "no-store"}
MAX_BODY_BYTES = 16 * 1024 * 1024  # a default-size 2 MiB file can expand to 12 MiB when JSON-escaped
AUTH_TIMEOUT_SECONDS = 5.0
# The daemon answers one connection's requests in order, so each poll waits only this long there; the pump then
# sleeps (IDLE_SLEEP_MAX at most, less after output, and none after the client typed) before it asks again.
POLL_SECONDS = 0.1
IDLE_SLEEP_STEP = 0.05
IDLE_SLEEP_MAX = 0.4
CLOSE_UNAUTHORIZED = 4401
CLOSE_NO_SESSION = 4404
CLOSE_DAEMON_LOST = 1011
OUTDATED_MESSAGE = "This agent runs older code. Restart it to use Code, Git and Terminal."
# What GET /git answers: read-only, no body.
GET_GIT_ACTIONS = ("status", "current_branch", "list_branches")


def _fail(status: int, code: str, message: str) -> JSONResponse:
    # `detail` repeats the message so the client's generic error path reads it like the engine's other routes.
    body = {"success": False, "error": {"code": code, "message": message}, "detail": message}
    return JSONResponse(body, status_code=status, headers=_NO_STORE)


def _live_session(session_id: str) -> EngineSession | JSONResponse:
    session = get_session_registry().get(session_id)
    if session is None:
        return _fail(404, "session_not_found", "Session not found.")
    if not session.alive:
        return _fail(409, "daemon_not_running", "The session's daemon is not running.")
    return session


def _is_outdated(exc: BaseException) -> bool:
    """A daemon that started before this feature has no ``state.code_call`` method (``RpcMethodNotFound``)."""
    return type(exc).__name__ == "RpcMethodNotFound"


async def _run(session_id: str, op: str, params: dict[str, Any]) -> JSONResponse:
    """Ask the session's daemon to run ``op`` and pass its answer on unchanged."""
    session = _live_session(session_id)
    if isinstance(session, JSONResponse):
        return session
    try:
        answer = await session.state.code_call(op, params)
    except Exception as exc:
        # The type name only: the exception text can carry a path or file content.
        logger.error("Session %s code call %s failed: %s", session_id, op, type(exc).__name__)
        if _is_outdated(exc):
            return _fail(502, "daemon_outdated", OUTDATED_MESSAGE)
        return _fail(502, "daemon_unreachable", f"The session's daemon did not answer ({type(exc).__name__}).")
    return JSONResponse(answer["body"], status_code=answer["status"], headers=_NO_STORE)


async def _body(request: Request) -> dict[str, Any] | JSONResponse:
    """The JSON object a POST, PUT or DELETE sends. Its text is never logged."""
    length = request.headers.get("content-length")
    if (
        length and length.isascii() and length.isdecimal()
        and (len(length.lstrip("0")) > 10 or int(length[-10:]) > MAX_BODY_BYTES)
    ):
        return _fail(413, "body_too_large", "The request body is too large.")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY_BYTES:
            return _fail(413, "body_too_large", "The request body is too large.")
        raw.extend(chunk)
    try:
        body = json.loads(raw)
    except ValueError:
        return _fail(400, "invalid_json", "The request body must be JSON.")
    if not isinstance(body, dict):
        return _fail(400, "invalid_json", "The request body must be a JSON object.")
    return body


async def _with_body(session_id: str, op: str, request: Request) -> JSONResponse:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    return await _run(session_id, op, {**dict(request.query_params), **body})


# ----------------------------------------------------------------------------------------------------- files


@router.get("/fs/tree")
async def fs_tree(session_id: str, request: Request) -> JSONResponse:
    return await _run(session_id, "fs.tree", dict(request.query_params))


@router.get("/fs/list")
async def fs_list(session_id: str, request: Request) -> JSONResponse:
    return await _run(session_id, "fs.list", dict(request.query_params))


@router.get("/fs/file")
async def fs_read(session_id: str, request: Request) -> JSONResponse:
    return await _run(session_id, "fs.file", dict(request.query_params))


@router.put("/fs/file")
async def fs_write(session_id: str, request: Request) -> JSONResponse:
    return await _with_body(session_id, "fs.write_file", request)


@router.post("/fs/create")
async def fs_create(session_id: str, request: Request) -> JSONResponse:
    return await _with_body(session_id, "fs.create", request)


@router.post("/fs/mkdir")
async def fs_mkdir(session_id: str, request: Request) -> JSONResponse:
    return await _with_body(session_id, "fs.mkdir", request)


@router.post("/fs/rename")
async def fs_rename(session_id: str, request: Request) -> JSONResponse:
    return await _with_body(session_id, "fs.rename", request)


@router.delete("/fs/delete")
@router.post("/fs/delete")
async def fs_delete(session_id: str, request: Request) -> JSONResponse:
    # mentiko's route is a POST; both verbs reach this one handler so the ported callers keep working.
    return await _with_body(session_id, "fs.delete", request)


@router.get("/fs/search")
async def fs_search(session_id: str, request: Request) -> JSONResponse:
    return await _run(session_id, "fs.search", dict(request.query_params))


@router.get("/fs/git-status")
async def fs_git_status(session_id: str, request: Request) -> JSONResponse:
    return await _run(session_id, "fs.git-status", dict(request.query_params))


# ------------------------------------------------------------------------------------------------------- git


@router.post("/git")
async def git_action(session_id: str, request: Request) -> JSONResponse:
    """Run one git action: ``{"action": ..., ...}``; the other fields belong to the action."""
    return await _with_body(session_id, "git", request)


@router.get("/git")
async def git_read(session_id: str, request: Request) -> JSONResponse:
    """The read actions that take no body."""
    params = dict(request.query_params)
    params.setdefault("action", "status")
    if params["action"] not in GET_GIT_ACTIONS:
        return _fail(400, "unknown_action", "That git action is not available here.")
    return await _run(session_id, "git", params)


# ---------------------------------------------------------------------------------------------------- settings


@router.get("/folders")
async def code_folders(session_id: str) -> JSONResponse:
    """Folders the daemon has discovered for its code editor."""
    return await _run(session_id, "folders", {})


@router.get("/folders/browse")
async def browse_folders(session_id: str, request: Request) -> JSONResponse:
    return await _run(session_id, "folders.browse", dict(request.query_params))


@router.get("/settings")
async def code_settings(session_id: str) -> JSONResponse:
    """The Code, Git and Terminal settings in effect on the session's daemon (``env_set`` shows names only)."""
    return await _run(session_id, "settings", {})


@router.get("/terminal/env")
async def terminal_env(session_id: str) -> JSONResponse:
    """The variable names a new shell on the session's daemon would not get, by reason. Names only."""
    return await _run(session_id, "terminal.env_report", {})


# -------------------------------------------------------------------------------------------------- terminal


async def _authenticated(ws: WebSocket) -> bool:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except TimeoutError, WebSocketDisconnect:
        return False
    try:
        message = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(message, dict) or message.get("type") != "auth":
        return False
    token = message.get("token")
    if not isinstance(token, str):
        return False
    try:
        return validate_token(token)
    except TypeError:  # secrets.compare_digest refuses text with non-ASCII characters
        return False


class _DaemonLost(Exception):
    """The session's daemon failed or refused a terminal call. Carries no text: that could hold shell output.

    ``outdated`` is set when the daemon has no ``state.code_call`` at all: it needs a restart, not a retry.
    """

    def __init__(self, outdated: bool = False, message: str | None = None) -> None:
        super().__init__()
        self.outdated = outdated
        self.message = message  # the daemon's own refusal text when a setting said no, else None


class _Refused(_DaemonLost):
    """The daemon answered, and a setting says no (terminal turned off, for one). ``message`` is the daemon's own
    refusal text, which names the setting; it is built from fixed text and never holds shell output."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message)


async def _ask(session: EngineSession, op: str, params: dict[str, Any]) -> dict[str, Any]:
    """One terminal call to the daemon; its data, or ``_DaemonLost`` for any failure (kept apart from socket errors)."""
    try:
        return _data(await session.state.code_call(op, params))
    except _Refused:
        raise
    except Exception as exc:
        raise _DaemonLost(_is_outdated(exc)) from None


def _data(answer: dict[str, Any]) -> dict[str, Any]:
    """The ``data`` of a daemon answer; a refusal or an unknown shape is a lost daemon."""
    body = answer.get("body")
    data = body.get("data") if isinstance(body, dict) else None
    error = body.get("error") if isinstance(body, dict) else None
    if (
        answer.get("status") != 200
        and isinstance(error, dict)
        and isinstance(error.get("setting"), str)
        and isinstance(error.get("message"), str)
    ):
        raise _Refused(error["message"])
    if answer.get("status") != 200 or not isinstance(data, dict):
        raise ValueError("terminal call refused")
    return data


async def _forward(ws: WebSocket, session: EngineSession, link: str, wake: asyncio.Event) -> None:
    """Client messages to the daemon, one at a time and in order; its immediate answers back to the client."""
    while True:
        raw = await ws.receive_text()  # raises WebSocketDisconnect once the client has gone
        data = await _ask(session, "terminal.message", {"link": link, "raw": raw})
        wake.set()  # the shell is about to answer: look for output now
        for message in data.get("messages", []):
            await ws.send_json(message)


async def _pump(ws: WebSocket, session: EngineSession, link: str, wake: asyncio.Event) -> None:
    """The daemon's output to the client, by short polls; stops after the shell's exit message."""
    ack: int | None = None
    idle = 0
    while True:
        params: dict[str, Any] = {"link": link, "wait": POLL_SECONDS}
        if ack is not None:
            params["ack"] = ack
        data = await _ask(session, "terminal.poll", params)
        ack = data["ack"] if isinstance(data.get("ack"), int) else ack
        exited = False
        for message in data.get("messages", []):
            await ws.send_json(message)
            exited = exited or message.get("type") == "exit"
        if exited:
            return
        if data.get("messages"):
            idle = 0
            continue
        idle += 1
        wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), min(IDLE_SLEEP_MAX, IDLE_SLEEP_STEP * idle))


async def _lost(ws: WebSocket, session_id: str, outdated: bool = False, refusal: str | None = None) -> None:
    """The daemon stopped answering, or a setting refused the terminal: say so on the socket and close it."""
    if refusal is None:
        logger.error("Session %s terminal bridge lost its daemon", session_id)
    message = refusal or (OUTDATED_MESSAGE if outdated else "The session's daemon stopped answering.")
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        await ws.send_json({"type": "error", "message": message, **({"code": "daemon_outdated"} if outdated else {})})
        await ws.close(code=CLOSE_DAEMON_LOST)


async def _guarded_pump(
    ws: WebSocket, session: EngineSession, link: str, session_id: str, wake: asyncio.Event
) -> None:
    try:
        await _pump(ws, session, link, wake)
    except _DaemonLost as lost:
        await _lost(ws, session_id, lost.outdated, lost.message)  # closing the socket ends the forwarder too
    except (WebSocketDisconnect, RuntimeError):
        pass  # the client has gone


@router.websocket("/terminal")
async def code_terminal_ws(ws: WebSocket, session_id: str) -> None:
    """Terminal socket. Send ``auth`` first, then ``open``, ``input``, ``resize`` or ``close``.

    The server answers ``ready`` (with the ``sessionId`` to reattach by), ``output``, ``exit`` and ``error``.
    """
    await ws.accept()
    if not await _authenticated(ws):
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    session = get_session_registry().get(session_id)
    if session is None or not session.alive:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await ws.send_json({"type": "error", "message": "That session is not running."})
        await ws.close(code=CLOSE_NO_SESSION)
        return
    link = secrets.token_urlsafe(16)
    wake = asyncio.Event()
    pump = asyncio.ensure_future(_guarded_pump(ws, session, link, session_id, wake))
    try:
        # The socket stays open after a shell exits, until the client closes it.
        await _forward(ws, session, link, wake)
    except _DaemonLost as lost:
        await _lost(ws, session_id, lost.outdated, lost.message)
    except (WebSocketDisconnect, RuntimeError):
        pass  # the client has gone
    finally:
        pump.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(session.state.code_call("terminal.detach", {"link": link}), timeout=5.0)
