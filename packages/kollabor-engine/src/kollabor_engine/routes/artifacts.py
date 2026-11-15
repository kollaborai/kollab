"""Session artifacts: the tool outputs the budget spilled to disk (#1088).

Spilled outputs land in ``<conversations_dir>/tool_outputs/<session>/`` as
private ``.output`` text files; history rows carry their paths, but nothing
could list or read them back. This router lists a session's artifacts and
serves their text, rooted at one directory computed from the session id, so
no row-supplied path is ever trusted.
"""

import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .media import _conversations_dir

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Mirrors tool_output_budget._safe_name so the root matches the store's.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ARTIFACT_REF_RE = re.compile(r"^[A-Za-z0-9._-]+\.output$")
_SERVE_BYTES = 2_000_000


def _session_name(session_id: str) -> str:
    text = _SAFE_NAME_RE.sub("-", session_id).strip("-.")
    return (text or "session")[:80]


def _artifacts_root(session_id: str) -> Path:
    return _conversations_dir() / "tool_outputs" / _session_name(session_id)


def _artifact_path(session_id: str, ref: str) -> Path:
    if not _ARTIFACT_REF_RE.match(ref):
        raise HTTPException(status_code=400, detail="Invalid artifact reference")
    path = _artifacts_root(session_id) / ref
    if path.parent != _artifacts_root(session_id) or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return path


def _listing(session_id: str) -> list[dict[str, Any]]:
    root = _artifacts_root(session_id)
    if not root.is_dir():
        return []
    entries = []
    for path in root.glob("*.output"):
        if not path.is_file():
            continue
        stat = path.stat()
        entries.append(
            {
                "ref": path.name,
                "name": path.name.removesuffix(".output"),
                "bytes": stat.st_size,
                "modified": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)
                ),
            }
        )
    entries.sort(key=lambda entry: entry["modified"], reverse=True)
    return entries


@router.get("/{session_id}/artifacts")
async def list_artifacts(session_id: str) -> dict[str, Any]:
    """The session's spilled tool outputs, newest first."""
    return {"session_id": session_id, "artifacts": _listing(session_id)}


@router.get("/{session_id}/artifacts/{ref}")
async def read_artifact(session_id: str, ref: str) -> dict[str, Any]:
    """One artifact's text, capped; rows only ever reference text outputs."""
    path = _artifact_path(session_id, ref)
    try:
        raw = path.read_bytes()[:_SERVE_BYTES]
    except OSError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None
    return {
        "session_id": session_id,
        "ref": ref,
        "truncated": path.stat().st_size > len(raw),
        "content": raw.decode("utf-8", errors="replace"),
    }


__all__ = ["router"]

