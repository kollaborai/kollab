"""Bounded and resumable serialization of the session history mirror."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder

from kollabor_config.config_utils import get_config_directory

HISTORY_DEFAULT_PAGE_SIZE = 50
HISTORY_MAX_PAGE_SIZE = 100
HISTORY_MAX_PAGE_BYTES = 150_000
HISTORY_TOOL_OUTPUT_PREVIEW_BYTES = 4_096
_CURSOR_SECRET: bytes | None = None


def load_history_cursor_secret(path: Path | None = None) -> bytes:
    """Read or create the stable per-user HMAC key for history cursors.

    The file is separate from the rotating engine bearer token: old page
    cursors remain verifiable after an engine restart. Existing malformed keys
    fail closed rather than silently rotating and invalidating cached cursors.
    """
    key_path = path or (get_config_directory() / "history-cursor.key")
    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(key_path, os.O_RDONLY | nofollow)
    except FileNotFoundError:
        secret = secrets.token_bytes(32)
        try:
            fd = os.open(
                key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
            )
        except FileExistsError:
            fd = os.open(key_path, os.O_RDONLY | nofollow)
        else:
            with os.fdopen(fd, "wb") as key_file:
                key_file.write(secret)
                key_file.flush()
                os.fsync(key_file.fileno())
            return secret

    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError("History cursor key must be a regular file.")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        else:
            os.chmod(key_path, 0o600)
        with os.fdopen(fd, "rb") as key_file:
            secret = key_file.read(33)
        if len(secret) != 32:
            raise RuntimeError(
                f"History cursor key at {key_path} must contain exactly 32 bytes."
            )
        return secret
    finally:
        # fdopen closes it on the successful read path; close on validation errors.
        try:
            os.close(fd)
        except OSError:
            pass


def _get_cursor_secret() -> bytes:
    global _CURSOR_SECRET
    if _CURSOR_SECRET is None:
        _CURSOR_SECRET = load_history_cursor_secret()
    return _CURSOR_SECRET


class InvalidHistoryCursor(ValueError):
    """The supplied cursor is malformed or belongs to another session."""


class StaleHistoryCursor(ValueError):
    """History before the cursor boundary changed and must be refreshed."""


class HistoryPageTooLarge(ValueError):
    """Even the smallest representation of one message exceeds the page cap."""


def _canonical_bytes(value: Any) -> bytes:
    encoded = jsonable_encoder(value)
    return json.dumps(
        encoded,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _encode_cursor(
    session_id: str, kind: str, position: int, digest: str
) -> str:
    payload = _canonical_bytes(
        {"v": 1, "session": session_id, "kind": kind, "position": position, "digest": digest}
    )
    signature = hmac.digest(_get_cursor_secret(), payload, "sha256")
    return f"{_b64(payload)}.{_b64(signature)}"


def _decode_cursor(
    cursor: str,
    session_id: str,
    kind: str,
    history: list[dict[str, Any]],
) -> tuple[int, str]:
    if not isinstance(cursor, str) or len(cursor) > 8_192 or "." not in cursor:
        raise InvalidHistoryCursor("Invalid history cursor.")
    try:
        payload_part, signature_part = cursor.split(".", 1)
        payload = _unb64(payload_part)
        signature = _unb64(signature_part)
        expected = hmac.digest(_get_cursor_secret(), payload, "sha256")
        data = json.loads(payload)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise InvalidHistoryCursor("Invalid history cursor.") from error
    if not hmac.compare_digest(signature, expected):
        raise InvalidHistoryCursor("Invalid history cursor.")
    if data.get("v") != 1 or data.get("session") != session_id or data.get("kind") != kind:
        raise InvalidHistoryCursor("History cursor does not match this request.")
    position = data.get("position")
    if not isinstance(position, int) or position < 0 or position > len(history):
        raise StaleHistoryCursor("History changed. Refresh recent messages and retry.")
    if kind == "page":
        current_digest = _digest(history[:position])
    else:
        if position >= len(history):
            raise StaleHistoryCursor("Tool output changed. Refresh recent messages and retry.")
        current_digest = _digest(history[position])
    if not hmac.compare_digest(str(data.get("digest", "")), current_digest):
        raise StaleHistoryCursor("History changed. Refresh recent messages and retry.")
    return position, current_digest


def _page_cursor(session_id: str, history: list[dict[str, Any]], position: int) -> str:
    return _encode_cursor(session_id, "page", position, _digest(history[:position]))


def _copy_for_page(message: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(message)


def _truncated_tool_message(
    session_id: str, history: list[dict[str, Any]], index: int
) -> dict[str, Any]:
    message = _copy_for_page(history[index])
    content = history[index].get("content", "")
    content_bytes = content.encode("utf-8") if isinstance(content, str) else _canonical_bytes(content)
    preview = content_bytes[:HISTORY_TOOL_OUTPUT_PREVIEW_BYTES].decode("utf-8", "ignore")
    message["content"] = (
        preview
        + "\n\n[Tool output shortened to fit this page. Select Show more to fetch it.]"
    )
    metadata = message.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata["history_output_truncated"] = True
    metadata["history_output_bytes"] = len(content_bytes)
    metadata["history_output_ref"] = _encode_cursor(
        session_id, "output", index, _digest(history[index])
    )
    message["metadata"] = metadata
    return message


def _response(
    session_id: str,
    history: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    start: int,
    end: int,
    base_payload: dict[str, Any],
) -> dict[str, Any]:
    result = dict(base_payload)
    result.update(
        {
            "session_id": session_id,
            "history": messages,
            "total_messages": len(history),
            "start_index": start,
            "end_index": end,
            "has_more_older": start > 0,
            "older_cursor": _page_cursor(session_id, history, start) if start > 0 else None,
            "newer_cursor": _page_cursor(session_id, history, end),
            "has_more_newer": end < len(history),
        }
    )
    return result


def build_history_page(
    session_id: str,
    history: list[dict[str, Any]],
    *,
    limit: int | None = None,
    before: str | None = None,
    after: str | None = None,
    base_payload: dict[str, Any] | None = None,
    byte_limit: int = HISTORY_MAX_PAGE_BYTES,
) -> dict[str, Any]:
    """Return a chronological page while bounding count and JSON wire bytes.

    `before` pages toward older messages; `after` fetches messages appended
    since a cached cursor. Cursors are session-bound and validate the history
    prefix, so appends are safe while rewrites/compaction become typed stale
    cursor errors.
    """
    if before and after:
        raise InvalidHistoryCursor("Use either before or after, not both.")
    if byte_limit <= 0:
        raise ValueError("History page byte limit must be positive.")
    byte_limit = min(byte_limit, HISTORY_MAX_PAGE_BYTES)
    requested_limit = HISTORY_DEFAULT_PAGE_SIZE if limit is None else int(limit)
    page_limit = max(1, min(requested_limit, HISTORY_MAX_PAGE_SIZE))
    original = history
    base = dict(base_payload or {})

    if after:
        start, _ = _decode_cursor(after, session_id, "page", original)
        # On cache refresh the caller needs the latest slice of new messages,
        # not an arbitrarily long backlog after the cached cursor. Older
        # appended pages remain reachable through the returned before cursor.
        minimum_start = max(start, len(original) - page_limit)
        page_end = len(original)
        page_start = page_end
        reversed_selected: list[dict[str, Any]] = []
        for index in range(len(original) - 1, minimum_start - 1, -1):
            if len(reversed_selected) >= page_limit:
                break
            candidate = _copy_for_page(original[index])
            next_start = index
            proposed = list(reversed(reversed_selected + [candidate]))
            body = _response(
                session_id, original, proposed, next_start, page_end, base
            )
            if len(_canonical_bytes(body)) > byte_limit:
                if not reversed_selected and original[index].get("role") == "tool":
                    candidate = _truncated_tool_message(session_id, original, index)
                    body = _response(
                        session_id, original, [candidate], next_start, page_end, base
                    )
                if len(_canonical_bytes(body)) > byte_limit:
                    if not reversed_selected:
                        raise HistoryPageTooLarge("One history message exceeds the page byte cap.")
                    break
            reversed_selected.append(candidate)
            page_start = next_start
        selected = list(reversed(reversed_selected))
    else:
        page_end = len(original) if before is None else _decode_cursor(
            before, session_id, "page", original
        )[0]
        indexes = range(page_end - 1, -1, -1)
        reversed_selected: list[dict[str, Any]] = []
        page_start = page_end
        for index in indexes:
            if len(reversed_selected) >= page_limit:
                break
            candidate = _copy_for_page(original[index])
            next_start = index
            body = _response(
                session_id,
                original,
                list(reversed(reversed_selected + [candidate])),
                next_start,
                page_end,
                base,
            )
            if len(_canonical_bytes(body)) > byte_limit:
                if not reversed_selected and original[index].get("role") == "tool":
                    candidate = _truncated_tool_message(session_id, original, index)
                    body = _response(session_id, original, [candidate], next_start, page_end, base)
                if len(_canonical_bytes(body)) > byte_limit:
                    if not reversed_selected:
                        raise HistoryPageTooLarge("One history message exceeds the page byte cap.")
                    break
            reversed_selected.append(candidate)
            page_start = next_start
        selected = list(reversed(reversed_selected))

    if not selected and original and (before or after):
        # An exhausted cursor is a valid empty page; the cursor itself has
        # already passed signature and snapshot validation.
        pass
    result = _response(session_id, original, selected, page_start, page_end, base)
    if len(_canonical_bytes(result)) > byte_limit:
        raise HistoryPageTooLarge("History page metadata exceeds the byte cap.")
    return result


def resolve_history_output(
    session_id: str, history: list[dict[str, Any]], reference: str
) -> Any:
    """Fetch one deferred tool result after checking its snapshot reference."""
    index, _ = _decode_cursor(reference, session_id, "output", history)
    return copy.deepcopy(history[index].get("content"))
