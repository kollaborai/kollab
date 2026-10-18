"""Persona roster routes: the JSON libraries behind agent names (docs/specs/rosters-and-teams.md)."""

import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query  # type: ignore[import-not-found]
from fastapi.responses import FileResponse  # type: ignore[import-not-found]

router = APIRouter(prefix="/rosters", tags=["rosters"])

# What an avatar file may be. Anything else in a roster's folder (the roster
# JSON itself, notes) is never served.
AVATAR_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def folder_of(workspace: Optional[str]) -> str:
    """The folder whose project rosters and teams apply: ``workspace``, else this engine's."""
    folder = workspace or os.getcwd()
    if not Path(folder).is_dir():
        raise HTTPException(status_code=400, detail=f"workspace does not exist: {folder}")
    return os.path.realpath(folder)


def load(folder: str):
    from plugins.hub.rosters import load_library

    return load_library(folder)


def avatar_descriptor(avatar: Dict[str, str], roster: str) -> Dict[str, str]:
    """An avatar as the UI draws it. An image gets a ``url`` (a served file or an https URL)."""
    if avatar.get("kind") != "image":
        return dict(avatar)
    src = avatar["src"]
    url = src if src.startswith("https://") else f"/rosters/{roster}/avatars/{src}"
    return {"kind": "image", "url": url}


def persona_dict(persona: Any) -> Dict[str, Any]:
    return {
        "name": persona.name,
        "roster": persona.roster,
        "display_name": persona.display_name,
        "avatar": avatar_descriptor(persona.avatar, persona.roster),
        "color": list(persona.color),
        "agent": persona.agent,
        "role": persona.role,
        "personality": persona.personality,
        "skills": list(persona.skills),
        "profile": persona.profile,
        "prompt": persona.prompt,
        "caste": persona.caste,
        "role_aliases": list(persona.role_aliases),
    }


def apply_persona(body: Any) -> None:
    """Fill a session request from the persona its ``identity`` names. What the caller set wins."""
    if not body.identity:
        return
    try:
        from plugins.hub.rosters import find_persona
    except ImportError:
        return
    persona = find_persona(body.identity, body.workspace)
    if persona is None:
        return
    body.agent = body.agent or persona.agent or None
    if body.profile == "default" and persona.profile:
        body.profile = persona.profile


@router.get("")
async def list_rosters(
    workspace: Optional[str] = Query(None, description="Folder whose project rosters apply"),
) -> Dict[str, Any]:
    """Every roster with its personas, plus the one this folder draws agent names from."""
    from plugins.hub.rosters import folder_roster_name

    folder = folder_of(workspace)
    lib = load(folder)
    return {
        "workspace": folder,
        "default": folder_roster_name(folder),
        "rosters": [
            {
                "name": roster.name,
                "display_name": roster.display_name,
                "description": roster.description,
                "avatar": avatar_descriptor(roster.avatar, roster.name),
                "source": roster.source,
                "path": str(roster.path),
                "personas": [persona_dict(p) for p in roster.personas],
            }
            for roster in lib.rosters.values()
        ],
        "problems": [p for p in lib.problems],
    }


@router.get("/{name}/avatars/{file:path}")
async def get_avatar(
    name: str,
    file: str,
    workspace: Optional[str] = Query(None),
) -> FileResponse:
    """An image avatar, served from the folder the roster file lives in (nothing outside it)."""
    roster = load(folder_of(workspace)).rosters.get(name)
    if roster is None:
        raise HTTPException(status_code=404, detail=f"roster '{name}' not found")
    base = roster.path.parent.resolve()
    target = (base / file).resolve()
    if (
        target.suffix.lower() not in AVATAR_TYPES
        or base not in target.parents
        or not target.is_file()
    ):
        raise HTTPException(status_code=404, detail="avatar not found")
    return FileResponse(
        target,
        media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        # An SVG opened as a page must not run script.
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"},
    )
