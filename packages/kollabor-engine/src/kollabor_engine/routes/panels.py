"""Panel routes: describe one daemon panel and run its actions.

Spec: ``docs/specs/webui-unified-config.md`` ("rpc + routes"). Every response,
errors included, carries ``Cache-Control: no-store``: a panel body can hold a
one-time join code. Nothing from a request body or a daemon error is logged
or echoed in a 502; payloads can hold secrets.
"""

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request  # type: ignore[import-not-found]
from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

from kollabor.panels import PanelError

from ..server import get_session_registry
from ..session import EngineSession
from .sessions import apply_profile_mirror

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["panels"])

_NO_STORE = {"Cache-Control": "no-store"}
_MAX_PARAMS = 16
_MAX_PARAM_CHARS = 256
_MAX_BODY_BYTES = 256 * 1024


def _live_session(session_id: str) -> EngineSession:
    session = get_session_registry().get(session_id)
    if not session:
        raise HTTPException(
            status_code=404, detail="Session not found", headers=_NO_STORE
        )
    if not session.alive:
        raise HTTPException(
            status_code=409, detail="Session daemon is not running", headers=_NO_STORE
        )
    return session


def _http_status(status: int) -> int:
    """PanelError.status as an HTTP code. 409 is reserved for a stopped daemon."""
    if status == 503 or (400 <= status < 500 and status != 409):
        return status
    return 502


def _failure(session_id: str, exc: Exception) -> JSONResponse:
    if isinstance(exc, PanelError):
        status, message, errors = _http_status(exc.status), exc.message, exc.errors
    else:
        # type name only: the exception text can carry a payload value
        logger.error("Session %s panel call failed: %s", session_id, type(exc).__name__)
        status, message, errors = 502, f"daemon unreachable ({type(exc).__name__})", {}
    # `message`+`errors` feed the form; `detail` is what the client shows for any
    # non-400 failure.
    body = {"ok": False, "message": message, "errors": errors, "detail": message}
    return JSONResponse(body, status_code=status, headers=_NO_STORE)


def _query_params(request: Request) -> Dict[str, str]:
    params = dict(request.query_params)
    if len(params) > _MAX_PARAMS or any(
        len(value) > _MAX_PARAM_CHARS for value in params.values()
    ):
        raise PanelError(
            f"at most {_MAX_PARAMS} query parameters of {_MAX_PARAM_CHARS} characters"
        )
    return params


async def _json_body(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise PanelError("request body too large", status=413)
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise PanelError("request body must be a JSON object")
    return payload


async def _refresh_profile_mirror(session: EngineSession) -> None:
    """Best effort: a model or profile change must not leave the list stale."""
    try:
        snap = await session.state.get_active_profile()
        if snap.name:
            apply_profile_mirror(
                session, snap.name, snap.model or None, getattr(snap, "effort", None)
            )
    except Exception as exc:
        logger.debug(
            "Session %s profile mirror refresh failed: %s",
            session.session_id,
            type(exc).__name__,
        )


def _network_panel(name: str) -> bool:
    """`connect` and `connect-*`: the network's screens (kollabor/panels/connect.py)."""
    return name == "connect" or name.startswith("connect-")


async def _network_owner(
    session: EngineSession,
) -> Optional[Tuple[EngineSession, Dict[str, Any]]]:
    """The live session in this one's folder whose daemon runs the network, with
    its Network panel.

    One process per folder holds the network (the workspace lock); every other
    chat in that folder only gets a read-only Network panel. None when none of
    this engine's sessions runs it (a terminal window does, or an agent an
    earlier engine left running): the chat keeps its read-only panel.
    """
    def folder(s: EngineSession) -> str:
        return getattr(s, "workspace", None) or os.getcwd()  # the daemon's cwd

    peers = [
        other
        for other in get_session_registry().values()
        if other is not session and other.alive and folder(other) == folder(session)
    ]
    for candidate in (session, *peers):
        try:
            panel = await candidate.state.get_panel("connect", {})
        except Exception:
            continue
        if panel.get("read_only"):
            continue
        # A peer that shows no network (still starting, no hub) does not run it.
        if candidate is session or panel.get("summary"):
            return candidate, panel
    return None


@router.get("/{session_id}/panels/{name}")
async def describe_panel(session_id: str, name: str, request: Request):
    session = _live_session(session_id)
    try:
        params = _query_params(request)
        target = session
        if _network_panel(name):
            owner = await _network_owner(session)
            if owner is not None:
                target, probe = owner
                if name == "connect" and not params:
                    return JSONResponse(probe, headers=_NO_STORE)
        panel = await target.state.get_panel(name, params)
    except Exception as exc:
        return _failure(session_id, exc)
    return JSONResponse(panel, headers=_NO_STORE)


@router.post("/{session_id}/panels/{name}/actions/{action}")
async def panel_action(session_id: str, name: str, action: str, request: Request):
    session = _live_session(session_id)
    try:
        payload = await _json_body(request)
        target = session
        if _network_panel(name):
            owner = await _network_owner(session)
            if owner is not None:
                target = owner[0]
        outcome = await target.state.panel_action(name, action, payload)
    except Exception as exc:
        return _failure(session_id, exc)
    await _refresh_profile_mirror(session)
    # per-field errors are a rejected request; ok False without errors is an
    # outcome (a failed connection test) and stays 200
    status = 400 if not outcome.get("ok") and outcome.get("errors") else 200
    return JSONResponse(outcome, status_code=status, headers=_NO_STORE)
