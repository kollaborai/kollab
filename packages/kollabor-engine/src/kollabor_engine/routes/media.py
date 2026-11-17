"""Serve and list the images sent through the web UI (#1088).

A ref is opaque: the strict id shape is validated before the engine derives
any path from it, so request data never reaches the filesystem. Media lives
under this engine's project data dir (the same conversations dir the history
store reads), so both endpoints resolve one root. The same bearer-token auth
as every other engine route covers these reads.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query  # type: ignore[import-not-found]
from fastapi.responses import Response  # type: ignore[import-not-found]

from .. import media_store

router = APIRouter(prefix="/media", tags=["media"])


def _conversations_dir() -> Path:
    from kollabor_config.config_utils import get_project_data_dir

    return get_project_data_dir() / "conversations"


@router.get("/{ref}")
async def get_media(ref: str) -> Response:
    try:
        payload, mime = media_store.open_media(_conversations_dir(), ref)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Unknown media ref") from None
    return Response(
        content=payload,
        media_type=mime,
        headers={"Cache-Control": "immutable, max-age=31536000"},
    )


@router.get("")
async def list_media(session_id: str = Query(...)) -> dict:
    try:
        media_store._safe_component(session_id)
    except media_store.MediaError:
        raise HTTPException(status_code=400, detail="Bad session id") from None
    return {
        "session_id": session_id,
        "media": media_store.list_for(_conversations_dir(), session_id),
    }
