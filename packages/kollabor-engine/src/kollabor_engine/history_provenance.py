"""Recover display provenance from existing, explicitly typed history records."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

from kollabor_ai.conversation_manager import matches_system_initialization_provenance

_BASE_BYTES = 16 * 1024 * 1024
_ORIGINAL_BYTES = 2 * 1024 * 1024
_LOOKUP_BYTES = 16 * 1024 * 1024
_LOOKUP_FILES = 128
_HEAD_RECORDS = 8
_CACHE_SIZE = 128
_verified: OrderedDict[tuple, tuple[str, str]] = OrderedDict()
_provenance_verified: OrderedDict[tuple, bool] = OrderedDict()
_misses: OrderedDict[tuple, float] = OrderedDict()
_MISS_SECONDS = 2.0
_cache_lock = Lock()


def _remember(cache: OrderedDict, key: tuple, value: Any) -> None:
    """Caller holds _cache_lock."""
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_SIZE:
        cache.popitem(last=False)


def _session_file(directory: Path, session_id: Any) -> Path | None:
    if not isinstance(session_id, str) or not session_id or len(session_id) > 200 or session_id in {".", ".."}:
        return None
    if "/" in session_id or "\\" in session_id:
        return None
    path = directory / f"{session_id}.jsonl"
    try:
        return path if path.resolve().parent == directory else None
    except (OSError, RuntimeError, ValueError):
        return None


def _head(path: Path, limit: int, *, skip_saved_base: bool = False) -> tuple[dict | None, int]:
    """Read only a bounded leading base/first user record, never the full log."""
    consumed = 0
    try:
        with path.open("rb") as stream:
            for _ in range(_HEAD_RECORDS):
                remaining = limit - consumed
                if remaining <= 0:
                    break
                line = stream.readline(min(512, remaining))
                consumed += len(line)
                if not line:
                    break
                # save_session emits this canonical envelope. It has no logger
                # user_context, so do not read its potentially enormous body
                # while looking for explicitly typed original log records.
                if skip_saved_base and line.lstrip().startswith(b'{"session_id":'):
                    break
                if not line.endswith(b"\n"):
                    tail = stream.readline(limit - consumed + 1)
                    consumed += len(tail)
                    line += tail
                if consumed > limit:
                    break
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(record, dict) and (
                    isinstance(record.get("messages"), list) or record.get("type") == "user"
                ):
                    return record, consumed
    except OSError:
        pass
    return None, consumed


def _marked(message: dict) -> dict:
    metadata = message.get("metadata")
    return {**message, "metadata": {**(metadata if isinstance(metadata, dict) else {}),
                                  "system_initialization": True}}


def mark_system_initialization(
    conversations_dir: Path, native_session_id: str, first_dto: dict,
) -> dict:
    """Copy/enrich an aligned first DTO only after UUID + exact-text proof.

    ``native_session_id`` is the daemon snapshot's id, not the engine handle.
    No file or model state is changed. Missing, malformed or bounded-
    out provenance leaves the DTO untouched; user prose is never a classifier.
    """
    if not isinstance(first_dto, dict) or first_dto.get("role") != "user":
        return first_dto
    content = first_dto.get("content")
    if not isinstance(content, str) or not content:
        return first_dto
    metadata = first_dto.get("metadata")
    if isinstance(metadata, dict) and metadata.get("system_initialization") is True:
        return first_dto
    try:
        directory = Path(conversations_dir).resolve()
    except (OSError, RuntimeError, ValueError):
        return first_dto
    path = _session_file(directory, native_session_id)
    if path is None:
        return first_dto
    try:
        info = path.stat()
    except OSError:
        return first_dto
    digest = hashlib.sha256(content.encode()).hexdigest()
    key = (str(directory), native_session_id, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, digest)
    with _cache_lock:
        if key in _verified:
            _verified.move_to_end(key)
            return _marked(first_dto)
    base, _ = _head(path, _BASE_BYTES)
    if base is None:
        return first_dto
    if "messages" in base:
        saved = (base.get("messages") or [None])[0]
    else:
        logged = base.get("message")
        saved = {"uuid": base.get("uuid"), **logged} if isinstance(logged, dict) else None
    if not isinstance(saved, dict) or saved.get("role") != "user" or saved.get("content") != content:
        return first_dto
    identifier = saved.get("uuid")
    if not isinstance(identifier, str) or not identifier:
        return first_dto

    original = base if matches_system_initialization_provenance(first_dto, saved, base) else None
    if base.get("type") == "user" and original is None:
        # This is an original logger record, not a saved base that lost its
        # metadata. Its explicit lack of initialization provenance is final.
        return first_dto
    provenance_key = (str(directory), identifier, digest)
    try:
        directory_revision = directory.stat().st_mtime_ns
    except OSError:
        return first_dto
    miss_key = (*provenance_key, directory_revision)
    with _cache_lock:
        if provenance_key in _provenance_verified:
            _provenance_verified.move_to_end(provenance_key)
            _remember(_verified, key, (identifier, digest))
            return _marked(first_dto)
        if _misses.get(miss_key, 0) > time.monotonic():
            _misses.move_to_end(miss_key)
            return first_dto
    if original is None:
        hinted = _session_file(directory, saved.get("session_id"))
        candidates = ([hinted] if hinted is not None else []) + sorted(directory.glob("*.jsonl"), reverse=True)
        seen = {path}
        budget = _LOOKUP_BYTES
        for candidate in candidates:
            if candidate in seen or candidate.resolve().parent != directory:
                continue
            if len(seen) > _LOOKUP_FILES or budget <= 0:
                break
            seen.add(candidate)
            record, consumed = _head(candidate, min(_ORIGINAL_BYTES, budget), skip_saved_base=True)
            budget -= consumed
            if matches_system_initialization_provenance(first_dto, saved, record):
                original = record
                break
    if original is None:
        with _cache_lock:
            # Existing files can acquire recoverable provenance without a
            # directory change. Misses are only a short lookup cooldown.
            _remember(_misses, miss_key, time.monotonic() + _MISS_SECONDS)
        return first_dto
    with _cache_lock:
        # Cache only successful immutable UUID/content provenance. The native
        # file's identity/revision fences replacement or edited saved bases.
        _remember(_verified, key, (identifier, digest))
        _remember(_provenance_verified, provenance_key, True)
        _misses.pop(miss_key, None)
    return _marked(first_dto)
