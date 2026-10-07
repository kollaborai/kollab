"""Panel routes: describe one daemon panel and run its actions.

Spec: ``docs/specs/webui-unified-config.md`` ("rpc + routes"). Every response,
errors included, carries ``Cache-Control: no-store``: a panel body can hold a
one-time join code. Nothing from a request body or a daemon error is logged
or echoed in a 502; payloads can hold secrets.
"""

import json
import logging
from typing import Any, Dict

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


@router.get("/{session_id}/panels/{name}")
async def describe_panel(session_id: str, name: str, request: Request):
    session = _live_session(session_id)
    try:
        panel = await session.state.get_panel(name, _query_params(request))
    except Exception as exc:
        return _failure(session_id, exc)
    return JSONResponse(panel, headers=_NO_STORE)


@router.post("/{session_id}/panels/{name}/actions/{action}")
async def panel_action(session_id: str, name: str, action: str, request: Request):
    session = _live_session(session_id)
    try:
        outcome = await session.state.panel_action(
            name, action, await _json_body(request)
        )
    except Exception as exc:
        return _failure(session_id, exc)
    await _refresh_profile_mirror(session)
    # per-field errors are a rejected request; ok False without errors is an
    # outcome (a failed connection test) and stays 200
    status = 400 if not outcome.get("ok") and outcome.get("errors") else 200
    return JSONResponse(outcome, status_code=status, headers=_NO_STORE)
