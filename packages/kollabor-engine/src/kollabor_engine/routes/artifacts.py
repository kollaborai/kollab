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

from kollabor_agent.tool_output_budget import session_output_dirname

from .media import _conversations_dir

router = APIRouter(prefix="/sessions", tags=["sessions"])

_ARTIFACT_REF_RE = re.compile(r"^[A-Za-z0-9._-]+\.output$")
_LEGACY_SESSION_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,78}[A-Za-z0-9])?$"
)
_SERVE_BYTES = 2_000_000


def _artifacts_root(session_id: str) -> Path:
    return _conversations_dir() / "tool_outputs" / session_output_dirname(session_id)


def _artifact_roots(session_id: str) -> tuple[Path, ...]:
    roots: list[Path] = []
    root = _artifacts_root(session_id)
    if root.is_dir():
        roots.append(root)
    # Read old directories only when the old sanitizer was injective for this
    # exact id. Sanitized or truncated legacy names can alias another session.
    if _LEGACY_SESSION_RE.fullmatch(session_id):
        legacy_root = _conversations_dir() / "tool_outputs" / session_id
        if legacy_root.is_dir() and legacy_root != root:
            roots.append(legacy_root)
    return tuple(roots) or (root,)


def _artifact_path(session_id: str, ref: str) -> Path:
    if not _ARTIFACT_REF_RE.match(ref):
        raise HTTPException(status_code=400, detail="Invalid artifact reference")
    for root in _artifact_roots(session_id):
        path = root / ref
        if path.parent == root and path.is_file():
            return path
    raise HTTPException(status_code=404, detail="Artifact not found")


def _listing(session_id: str) -> list[dict[str, Any]]:
    entries = []
    seen_refs: set[str] = set()
    for root in _artifact_roots(session_id):
        for path in root.glob("*.output"):
            if not path.is_file() or path.name in seen_refs:
                continue
            seen_refs.add(path.name)
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
