"""Permission response endpoint."""

import logging

from fastapi import APIRouter, HTTPException  # type: ignore[import-not-found]
from pydantic import BaseModel

from ..server import get_session_registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["permissions"])


class PermissionResponseRequest(BaseModel):
    tool_id: str
    decision: str  # "approve" | "deny"
    scope: str = (
        "once"  # "once" | "session" | "project" | "always_edits" | "trust_tool"
    )


@router.post("/{session_id}/permission")
async def respond_to_permission(session_id: str, body: PermissionResponseRequest):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if body.decision not in ("approve", "deny"):
        raise HTTPException(
            status_code=400, detail="decision must be 'approve' or 'deny'"
        )

    resolved = await session.resolve_permission(
        tool_id=body.tool_id,
        decision=body.decision,
        scope=body.scope,
    )
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No pending permission for tool_id '{body.tool_id}'",
        )

    return {"ok": True, "tool_id": body.tool_id, "decision": body.decision}


@router.get("/{session_id}/permissions")
async def get_permissions(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot = await session.state.get_permission_state()
    return {
        "session_id": session_id,
        "approval_mode": snapshot.approval_mode,
        "pending": list(session._pending_permissions.keys()),
        # Full prompt payloads so a client that reloaded mid-prompt can
        # re-render it. The daemon is still blocked waiting for an answer and
        # will not re-send the request.
        "pending_prompts": list(session._pending_permissions.values()),
        "stats": snapshot.stats,
        "session_approvals": snapshot.session_approvals_count,
        "project_approvals": snapshot.project_approvals_count,
    }


class SetModeRequest(BaseModel):
    mode: str


@router.post("/{session_id}/permissions/mode")
async def set_permissions_mode(session_id: str, body: SetModeRequest):
    from ..session import _APPROVAL_MODE_MAP

    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    mode = _APPROVAL_MODE_MAP.get(body.mode)
    if not mode:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown mode '{body.mode}'. Valid: confirm_all, default, auto_approve_edits, trust_all",
        )

    # The daemon owns the permission manager; state.set_approval_mode takes the
    # canonical enum name.
    snapshot = await session.state.set_approval_mode(mode.name)
    session.approval_mode = body.mode
    return {"ok": True, "mode": body.mode, "approval_mode": snapshot.approval_mode}
