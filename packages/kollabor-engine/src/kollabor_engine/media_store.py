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
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple, TypeVar

from kollabor_config.file_lock import locked_file

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
_IndexResult = TypeVar("_IndexResult")


class MediaError(ValueError):
    """The image cannot be stored as asked (bad data URL, too large)."""


@dataclass(frozen=True)
class MediaRecord:
    ref: str
    mime: str
    ts: float
    sha256: str


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


def _modify_index(
    conversations_dir: Path,
    session_id: str,
    update: Callable[[List[Dict[str, object]]], _IndexResult],
) -> _IndexResult:
    """Serialize one read/modify/replace of a session media index."""
    index_path = _index_path(conversations_dir, session_id)
    index_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = index_path.with_name(f".{INDEX_NAME}.lock")
    with locked_file(lock_path, "a+", private=True):
        entries = _read_index(conversations_dir, session_id)
        result = update(entries)
        _write_index(conversations_dir, session_id, entries)
        return result


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

    record = MediaRecord(
        ref=ref,
        mime=MIME_BY_EXT[ext],
        ts=time.time(),
        sha256=hashlib.sha256(decoded).hexdigest(),
    )
    def add_record(entries: List[Dict[str, object]]) -> None:
        entries.append(
            {
                "ref": record.ref,
                "mime": record.mime,
                "ts": record.ts,
                "sha256": record.sha256,
            }
        )

    _modify_index(conversations_dir, session_id, add_record)
    return record


def _verified_mime(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    raise MediaError("queue attachment bytes have no supported image signature")


def save_queue_handoff_media(
    conversations_dir: Path,
    session_id: str,
    *,
    request_id: str,
    attachments: Sequence[Tuple[str, str, bytes]],
) -> Tuple[Tuple[str, str], ...]:
    """Durably stage one exact ordered queue request image set idempotently.

    Each index row is tied to the daemon-owned source ref and request ID before
    the history row is written. A retry of the same request and bytes reuses
    its refs; changing order, source refs, MIME, or bytes conflicts. The later
    ``bind_queue_handoff`` call attaches the exact JSONL row UUID.
    """
    if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
        raise MediaError("queue handoff request id is invalid")
    if not isinstance(attachments, (tuple, list)) or len(attachments) > 20:
        raise MediaError("queue handoff attachments are invalid")

    checked: List[Tuple[str, str, bytes]] = []
    seen_sources = set()
    for attachment in attachments:
        if not isinstance(attachment, tuple) or len(attachment) != 3:
            raise MediaError("queue handoff attachment is invalid")
        source_ref, mime, payload = attachment
        if (
            not isinstance(source_ref, str)
            or not source_ref
            or len(source_ref) > 128
            or source_ref in seen_sources
        ):
            raise MediaError("queue handoff attachment refs are invalid")
        if mime not in MIME_BY_EXT.values():
            raise MediaError("queue handoff attachment MIME is unsupported")
        if type(payload) is not bytes or not payload or len(payload) > MAX_DECODED_BYTES:
            raise MediaError("queue handoff attachment bytes are invalid")
        if _verified_mime(payload) != mime:
            raise MediaError("queue handoff attachment bytes do not match MIME")
        seen_sources.add(source_ref)
        checked.append((source_ref, mime, payload))

    created_paths: List[Path] = []

    def ensure(entries: List[Dict[str, object]]) -> Tuple[Tuple[str, str], ...]:
        if any(not isinstance(entry, dict) for entry in entries):
            raise MediaError("queue handoff media index is malformed")
        existing = [
            entry for entry in entries
            if entry.get("queue_request_id") == request_id
        ]
        if existing:
            try:
                if any(type(entry.get("queue_attachment_index")) is not int for entry in existing):
                    raise MediaError("queue handoff attachment index is malformed")
                existing.sort(key=lambda entry: entry["queue_attachment_index"])
                indices = [entry["queue_attachment_index"] for entry in existing]
                if indices != list(range(len(existing))) or len(existing) != len(checked):
                    raise MediaError("queue handoff attachment set conflicts")
                result = []
                for index, (entry, (source_ref, mime, payload)) in enumerate(
                    zip(existing, checked, strict=True)
                ):
                    ref = entry.get("ref")
                    digest = entry.get("sha256")
                    if (
                        entry.get("queue_source_ref") != source_ref
                        or entry.get("mime") != mime
                        or not isinstance(ref, str)
                        or not isinstance(digest, str)
                        or hashlib.sha256(payload).hexdigest() != digest
                    ):
                        raise MediaError("queue handoff attachment set conflicts")
                    stored, actual_mime = open_media(
                        conversations_dir, ref, session_id=session_id
                    )
                    if actual_mime != mime or stored != payload:
                        raise MediaError("queue handoff attachment integrity failed")
                    result.append((source_ref, ref))
                return tuple(result)
            except (KeyError, TypeError):
                raise MediaError("queue handoff attachment set conflicts") from None

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        media_root = Path(conversations_dir) / "media"
        result = []
        new_entries = []
        for index, (source_ref, mime, payload) in enumerate(checked):
            ext = next(ext for ext, candidate in MIME_BY_EXT.items() if candidate == mime)
            while True:
                ref = timestamp + "-" + secrets.token_hex(2)
                if not any(media_root.glob(f"*/{ref}.*")) and not any(
                    ref == entry.get("ref") for entry in entries + new_entries
                ):
                    break
            target = session_media_dir(conversations_dir, session_id) / f"{ref}.{ext}"
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_bytes(payload)
                temporary.replace(target)
            except OSError as exc:
                try:
                    temporary.unlink()
                except OSError:
                    pass
                raise MediaError("queue attachment could not be stored") from exc
            created_paths.append(target)
            digest = hashlib.sha256(payload).hexdigest()
            new_entries.append(
                {
                    "ref": ref,
                    "mime": mime,
                    "ts": time.time(),
                    "sha256": digest,
                    "queue_request_id": request_id,
                    "queue_source_ref": source_ref,
                    "queue_attachment_index": index,
                }
            )
            result.append((source_ref, ref))
        entries.extend(new_entries)
        return tuple(result)

    try:
        return _modify_index(conversations_dir, session_id, ensure)
    except BaseException:
        for path in created_paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


def record_failed(
    conversations_dir: Path,
    session_id: str,
    mime: str = "application/octet-stream",
) -> None:
    """Record a send-time storage failure as an index slot.

    The overlay binds by counting a row's image parts against the session's
    dense index. A dropped slot shifts every later row's ref (the failed
    row consumes its neighbor's image). The tombstone keeps one index slot
    per attempted part, so count and store stay aligned; listing and
    binding skip it.
    """
    def add_failure(entries: List[Dict[str, object]]) -> None:
        entries.append(
            {"ref": "", "mime": mime, "ts": time.time(), "failed": True}
        )

    _modify_index(conversations_dir, session_id, add_failure)


def bind_queue_handoff(
    conversations_dir: Path,
    session_id: str,
    *,
    request_id: str,
    history_row_uuid: str,
    source_media_refs: Tuple[Tuple[str, str], ...],
) -> Tuple[Dict[str, object], ...]:
    """Bind verified media refs to one exact queued request and history row.

    This updates the existing per-session media index. It is idempotent for an
    identical binding and rejects legacy or damaged entries without a digest.
    """
    if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
        raise MediaError("queue handoff request id is invalid")
    if (
        not isinstance(history_row_uuid, str)
        or not history_row_uuid
        or len(history_row_uuid) > 256
    ):
        raise MediaError("queue handoff history row id is invalid")
    if (
        not isinstance(source_media_refs, tuple)
        or not source_media_refs
        or len(source_media_refs) > 20
    ):
        raise MediaError("queue handoff media references are invalid")
    pairs: List[Tuple[str, str]] = []
    for pair in source_media_refs:
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(not isinstance(value, str) or not value or len(value) > 128 for value in pair)
        ):
            raise MediaError("queue handoff media references are invalid")
        pairs.append((pair[0], pair[1]))
    source_refs = tuple(source for source, _media in pairs)
    media_refs = tuple(media for _source, media in pairs)
    if len(set(source_refs)) != len(source_refs) or len(set(media_refs)) != len(media_refs):
        raise MediaError("queue handoff media references are duplicated")

    def bind(entries: List[Dict[str, object]]) -> Tuple[Dict[str, object], ...]:
        if any(not isinstance(entry, dict) for entry in entries):
            raise MediaError("queue handoff media index is malformed")
        entry_positions: Dict[str, int] = {}
        for index, entry in enumerate(entries):
            ref = entry.get("ref")
            if isinstance(ref, str) and ref:
                if ref in entry_positions:
                    raise MediaError("queue handoff media index has duplicate refs")
                entry_positions[ref] = index

        # A request owns one complete, ordered attachment set. A retry of the
        # exact bind is safe, but extending or replacing that set would leave
        # multiple media entries claiming the same single history token.
        request_entries = [
            entry for entry in entries if entry.get("queue_request_id") == request_id
        ]
        if request_entries:
            if any(
                entry.get("history_row_uuid") not in (None, history_row_uuid)
                for entry in request_entries
            ):
                raise MediaError("queue handoff request is bound to another row")
            try:
                indices = [entry["queue_attachment_index"] for entry in request_entries]
                if any(type(index) is not int for index in indices):
                    raise MediaError("queue handoff media binding is malformed")
                request_entries.sort(key=lambda entry: entry["queue_attachment_index"])
                indices = [entry["queue_attachment_index"] for entry in request_entries]
                existing_pairs = tuple(
                    (entry["queue_source_ref"], entry["ref"])
                    for entry in request_entries
                )
            except (KeyError, TypeError):
                raise MediaError("queue handoff media binding is malformed") from None
            if existing_pairs != tuple(pairs):
                raise MediaError("queue handoff request binding conflicts")
            if indices != list(range(len(request_entries))):
                raise MediaError("queue handoff media binding is malformed")

        bound: List[Dict[str, object]] = []
        pending_updates: List[Tuple[Dict[str, object], Dict[str, object]]] = []
        for order, (source_ref, media_ref) in enumerate(pairs):
            position = entry_positions.get(media_ref)
            if position is None:
                raise MediaError("queue handoff media ref is missing")
            entry = entries[position]
            digest = entry.get("sha256")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise MediaError("queue handoff media digest is unavailable")
            try:
                data, actual_mime = open_media(
                    conversations_dir, media_ref, session_id=session_id
                )
            except FileNotFoundError:
                raise MediaError(
                    "queue handoff media ref is missing from its session"
                ) from None
            if hashlib.sha256(data).hexdigest() != digest:
                raise MediaError("queue handoff media integrity check failed")
            if entry.get("mime") != actual_mime:
                raise MediaError("queue handoff media mime does not match stored file")
            expected = {
                "queue_request_id": request_id,
                "queue_source_ref": source_ref,
                "history_row_uuid": history_row_uuid,
                "queue_attachment_index": order,
            }
            for key, value in expected.items():
                if key in entry and entry[key] not in (None, value):
                    raise MediaError("queue handoff media binding conflicts")
            pending_updates.append((entry, expected))
            bound.append(
                {
                    "source_ref": source_ref,
                    "ref": media_ref,
                    "mime": actual_mime,
                    "sha256": digest,
                    "index": order,
                }
            )
        for entry in entries:
            if (
                entry.get("queue_request_id") == request_id
                and entry.get("history_row_uuid") not in (None, history_row_uuid)
            ):
                raise MediaError("queue handoff request is bound to another row")
        for entry, expected in pending_updates:
            entry.update(expected)
        return tuple(bound)

    return _modify_index(conversations_dir, session_id, bind)


def open_media(
    conversations_dir: Path,
    ref: str,
    *,
    session_id: str | None = None,
) -> Tuple[bytes, str]:
    """Return (bytes, mime) for a ref, optionally scoped to one session."""
    if not REF_RE.match(ref or ""):
        raise FileNotFoundError(ref)
    if session_id is None:
        directory = Path(conversations_dir) / "media"
        matches = sorted(directory.glob(f"*/{ref}.*"))
    else:
        directory = session_media_dir(conversations_dir, session_id)
        matches = sorted(directory.glob(f"{ref}.*"))
    if not matches:
        raise FileNotFoundError(ref)
    if len(matches) > 1:
        raise MediaError("media integrity check failed: duplicate media files")
    path = matches[0]
    ext = path.suffix.lstrip(".").lower()
    mime = MIME_BY_EXT.get(ext)
    if not mime:
        raise FileNotFoundError(ref)
    data = path.read_bytes()
    # Older indexes predate content digests; continue to serve those media, but
    # every record written by this version is checked before it can be used as
    # durable attachment evidence.
    indexed = [
        entry
        for entry in _read_index(
            Path(conversations_dir), session_id or path.parent.name
        )
        if entry.get("ref") == ref
    ]
    if len(indexed) > 1:
        raise MediaError("media integrity check failed: duplicate index refs")
    if indexed and "sha256" in indexed[0]:
        expected = indexed[0].get("sha256")
        if (
            not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
            or hashlib.sha256(data).hexdigest() != expected
        ):
            raise MediaError("media integrity check failed")
    return data, mime


def list_for(conversations_dir: Path, session_id: str) -> List[Dict[str, object]]:
    """The session's images oldest first: the viewer's and gallery's source."""
    return [e for e in _read_index(conversations_dir, session_id) if e.get("ref")]


def slot_count(conversations_dir: Path, session_id: str) -> int:
    """The raw number of index slots, tombstones included.

    The history overlay walks the raw index, so the page-offset skip must
    count the same sequence. ``list_for`` is the filtered view for the
    gallery and viewer; binding arithmetic never uses it (#1124 review).
    """
    return len(_read_index(conversations_dir, session_id))


# [[imageN]] (double brackets) is the escaped literal form - text, not a
# token (#1124 P2a) - so it neither consumes a slot nor counts toward the
# page-offset skip.
# The token branch's digit run must contain a nonzero digit (value > 0):
# [image0] and [image00] are text, matching the frontend's near-miss
# grammar; [image01] is a token (value 1).
# One alternation mirrors the frontend splitter (#1140 review): a
# well-formed escape matches whole; a one-sided bracket run such as
# "[[image1]" or "[image1]]" yields its inner token plus stray-bracket
# text, so the inner token counts. findall carries only the token group,
# and escape-only matches come back as "".
# ASCII digits only (#1140 R3): Python's Unicode \d used to count
# non-ASCII digit runs (e.g. Arabic-Indic) as tokens while the JS splitter
# read them as literal text, misbinding stored images to prose rows.
_IMAGE_TOKEN_RE = re.compile(r"\[\[image[0-9]+\]\]|(\[image[0-9]*[1-9][0-9]*\])")


def _count_token_matches(content: str) -> int:
    return sum(1 for token in _IMAGE_TOKEN_RE.findall(content) if token)


def count_image_tokens(content: object) -> int:
    """How many sent images a row's content carries (str or parts).

    Two shapes say "one image" (#1088): a ``[imageN]`` token in text — the
    ``content_to_text`` rendering the disk jsonl keeps — and an image part
    with no bytes, which is how the daemon's conversation mirror stores a
    sent picture (``{"type": "image", "image": None}``, JSON-safe). History
    pages mix both shapes depending on their source, so the counter and the
    page-offset skip must count them identically.
    """
    if isinstance(content, str):
        return _count_token_matches(content)
    if isinstance(content, list):
        tokens = 0
        parts = 0
        for part in content:
            if isinstance(part, str):
                tokens += _count_token_matches(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    tokens += _count_token_matches(text)
                if part.get("type") == "image":
                    parts += 1
        # Image parts are authoritative: _save_sent_media stores exactly one
        # ref per part, so a row that also carries token text (the same
        # images rendered) must count once, not twice — counting both made
        # the binding slice another row's ref (#1124 clean-room review P2).
        # Token-only rows (the disk jsonl rendering) count their tokens.
        return parts if parts else tokens
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
        metadata = row.get("metadata")
        handoff = metadata.get("queue_handoff") if isinstance(metadata, dict) else None
        if isinstance(handoff, dict):
            request_id = handoff.get("request_id")
            if (
                not isinstance(request_id, str)
                or not request_id
                or (row.get("sessionId") is not None and row.get("sessionId") != session_id)
            ):
                continue
            handoff_entries = [
                entry for entry in entries
                if entry.get("queue_request_id") == request_id
            ]
            handoff_entries.sort(
                key=lambda entry: entry.get("queue_attachment_index", -1)
                if isinstance(entry.get("queue_attachment_index"), int)
                and not isinstance(entry.get("queue_attachment_index"), bool)
                else -1
            )
            if (
                len(handoff_entries) != tokens
                or any(
                    entry.get("history_row_uuid") != row.get("uuid")
                    or not isinstance(entry.get("queue_source_ref"), str)
                    or not entry.get("queue_source_ref")
                    or not isinstance(entry.get("sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
                    for entry in handoff_entries
                )
            ):
                continue
            row["media"] = [
                {"ref": entry.get("ref"), "mime": entry.get("mime")}
                for entry in handoff_entries
            ]
            continue
        taken = entries[cursor : cursor + tokens]
        cursor += len(taken)
        if not taken:
            break
        # Dense typed slot union (#1124 P2a): every consumed slot emits one
        # entry, so media[i] is the [image{i+1}] token's image even when the
        # row mixes stored and failed parts. Tombstones (a part whose
        # storage failed) emit {ref: "", mime: "", failed: true} - they bind
        # nothing, are never requested, and list_for still hides them from
        # the gallery and viewer. Compacting them out would shift every
        # later token's ordinal within the row.
        row["media"] = [
            {"ref": "", "mime": "", "failed": True}
            if not e.get("ref")
            else {"ref": e.get("ref"), "mime": e.get("mime")}
            for e in taken
        ]
