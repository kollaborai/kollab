"""Durable storage for images sent through the web UI (#1088).

The composer hands the engine a data URL per image. The daemon's conversation
log keeps only the ``[imageN]`` placeholder text, so without this store the
image is unreachable after the turn ends. Media files live beside the
conversation records under ``<conversations_dir>/media/<session_id>/`` and an
``index.json`` per session maps them to the rows that carry their placeholder
tokens, so a reloaded, restarted, or resumed session still resolves thumbnails.

Refs are opaque ids with a strict shape; the path is derived only from the
validated id, never from request data.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

# What a sent image may be, and how big decoded bytes may be. The cap guards
# the engine's disk and the browser's memory, not the LLM seam (the daemon
# already applied its own limits to what it accepted).
MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}
DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", re.DOTALL)
MAX_DECODED_BYTES = 10 * 1024 * 1024

REF_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
INDEX_NAME = "index.json"


class MediaError(ValueError):
    """The image cannot be stored as asked (bad data URL, too large)."""


@dataclass(frozen=True)
class MediaRecord:
    ref: str
    mime: str
    ts: float


def session_media_dir(conversations_dir: Path, session_id: str) -> Path:
    return Path(conversations_dir) / "media" / _safe_component(session_id)


def _safe_component(value: str) -> str:
    component = value.strip()
    if not component or "/" in component or "\\" in component or component in (".", ".."):
        raise MediaError(f"unsafe session id for media storage: {value!r}")
    return component


def _index_path(conversations_dir: Path, session_id: str) -> Path:
    return session_media_dir(conversations_dir, session_id) / INDEX_NAME


def _read_index(conversations_dir: Path, session_id: str) -> List[Dict[str, object]]:
    path = _index_path(conversations_dir, session_id)
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return entries if isinstance(entries, list) else []


def _write_index(
    conversations_dir: Path, session_id: str, entries: List[Dict[str, object]]
) -> None:
    path = _index_path(conversations_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, indent=1))
    temporary.replace(path)


def decode_data_url(data_url: str) -> Tuple[str, bytes]:
    """Validate a composer data URL and return (ext, decoded bytes)."""
    match = DATA_URL_RE.match((data_url or "").strip())
    if not match:
        raise MediaError("attachment is not a data:image/*;base64 URL")
    ext, encoded = match.group(1), match.group(2)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise MediaError("attachment base64 does not decode") from None
    if not decoded:
        raise MediaError("attachment decodes to zero bytes")
    if len(decoded) > MAX_DECODED_BYTES:
        raise MediaError(f"attachment decodes to {len(decoded)} bytes; cap is {MAX_DECODED_BYTES}")
    return ext, decoded


def save(
    conversations_dir: Path,
    session_id: str,
    data_url: str,
) -> MediaRecord:
    """Store one sent image and append it to the session's index (crash-safe).

    The index write lands after the file write with a temp-file rename, so a
    crash between the two leaves at most an unreferenced file, never a ref
    that points at nothing.
    """
    ext, decoded = decode_data_url(data_url)
    media_root = Path(conversations_dir) / "media"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    while True:
        ref = timestamp + "-" + secrets.token_hex(2)
        # Refs are looked up without a session id, so they must be unique
        # across sessions and extensions as well as within one directory.
        if not any(media_root.glob(f"*/{ref}.*")):
            break
    target = session_media_dir(conversations_dir, session_id) / f"{ref}.{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(decoded)
    temporary.replace(target)

    record = MediaRecord(ref=ref, mime=MIME_BY_EXT[ext], ts=time.time())
    entries = _read_index(conversations_dir, session_id)
    entries.append({"ref": record.ref, "mime": record.mime, "ts": record.ts})
    _write_index(conversations_dir, session_id, entries)
    return record


def open_media(conversations_dir: Path, ref: str) -> Tuple[bytes, str]:
    """Return (bytes, mime) for a ref, or raise FileNotFoundError."""
    if not REF_RE.match(ref or ""):
        raise FileNotFoundError(ref)
    directory = Path(conversations_dir) / "media"
    matches = sorted(directory.glob(f"*/{ref}.*"))
    if not matches:
        raise FileNotFoundError(ref)
    path = matches[0]
    ext = path.suffix.lstrip(".").lower()
    mime = MIME_BY_EXT.get(ext)
    if not mime:
        raise FileNotFoundError(ref)
    return path.read_bytes(), mime


def list_for(conversations_dir: Path, session_id: str) -> List[Dict[str, object]]:
    """The session's images oldest first: the viewer's and gallery's source."""
    return list(_read_index(conversations_dir, session_id))


_IMAGE_TOKEN_RE = re.compile(r"\[image\d+\]")


def count_image_tokens(content: object) -> int:
    """How many [imageN] tokens a row's content carries (str or parts)."""
    if isinstance(content, str):
        return len(_IMAGE_TOKEN_RE.findall(content))
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += len(_IMAGE_TOKEN_RE.findall(part))
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                total += len(_IMAGE_TOKEN_RE.findall(part["text"]))
        return total
    return 0


def attach_media_to_rows(
    conversations_dir: Path,
    session_id: str,
    rows: List[Dict[str, object]],
    skip: int = 0,
) -> None:
    """Bind stored image refs onto the history rows that sent them, in place.

    The store records images per session in submission order; that order is
    the only durable link to the sending row. A user row containing N
    [imageK] tokens sent N images, so the walk consumes the index the same
    way the send path appended it. Rows already carrying a media list keep
    it and consume nothing. Once the remaining rows hold fewer tokens than
    stored images (history compaction dropped senders), the leftover images
    bind nowhere; the gallery and viewer read list_for directly and are
    unaffected.

    `skip` advances past images already bound to rows outside this page
    (older pages: count the tokens in the full history before start_index).
    """
    entries = _read_index(conversations_dir, session_id)
    if not entries:
        return
    cursor = max(0, skip)
    for row in rows:
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        if isinstance(row.get("media"), list):
            continue
        tokens = count_image_tokens(row.get("content"))
        if not tokens:
            continue
        taken = entries[cursor : cursor + tokens]
        cursor += len(taken)
        if not taken:
            break
        row["media"] = [{"ref": e.get("ref"), "mime": e.get("mime")} for e in taken]
