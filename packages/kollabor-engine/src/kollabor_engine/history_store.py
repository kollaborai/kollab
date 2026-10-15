"""Session history from disk: what the daemon has already written.

The daemon writes its conversation as it goes -- a JSONL line per message
under ``~/.kollab/projects/<workspace>/conversations/<id>.jsonl`` and a
snapshot under ``conversations/snapshots/<id>_snapshot.json`` -- so an open
request never needs to ask the daemon for what is already on this disk.
Reading happens off the event loop: a large file must not stall every other
session's route.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Guards against pathological one-line files (a truncated tail on a busy
# writer): reading stops early rather than buffering an unbounded line.
_MAX_LINE_BYTES = 8 * 1024 * 1024


def _jsonl_path(conversations_dir: Path, conversation_id: str) -> Path:
    return conversations_dir / f"{conversation_id}.jsonl"


def _snapshot_path(conversations_dir: Path, conversation_id: str) -> Path:
    return conversations_dir / "snapshots" / f"{conversation_id}_snapshot.json"


def _wire_message(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One JSONL log record -> the engine mirror wire shape.

    Keep provider/display fields intact.  In particular, ACP/tool turns may
    carry content blocks, tool calls, provenance, snapshots, and IDs that are
    needed to reconstruct the chat after an engine restart.
    """
    if not isinstance(raw, dict):
        return None
    # The conversation logger stores user turns as {"type": ..., "message":
    # {"role", "content"}}; assistant completions store the fields inline.
    inner = raw.get("message") if isinstance(raw.get("message"), dict) else raw
    role = str(inner.get("role") or raw.get("type") or "")
    if role not in ("user", "assistant"):
        return None
    message: Dict[str, Any] = dict(inner)
    message["role"] = role
    content = inner.get("content")
    if isinstance(content, str):
        message["content"] = content
    elif isinstance(content, list):
        # Preserve structured blocks while retaining the established display
        # contract: plain text content is what older clients render.
        message["content_blocks"] = list(content)
        text_parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        message["content"] = "".join(text_parts)
    elif content is None:
        message["content"] = ""
    else:
        message["content"] = str(content)
    message["timestamp"] = str(raw.get("timestamp", inner.get("timestamp", "")) or "")
    metadata = inner.get("metadata")
    message["metadata"] = dict(metadata) if isinstance(metadata, dict) else {}
    message["thinking"] = inner.get("thinking")
    return message


def _messages_dto_like(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Raw dict -> MessageDto.to_dict() shape, for anchor arithmetic."""
    wire = _wire_message(raw)
    if wire is None:
        return None
    return {
        "role": wire["role"],
        "content": wire["content"],
        "timestamp": wire["timestamp"],
        "metadata": wire["metadata"],
        "thinking": wire["thinking"],
    }


def _read_jsonl(path: Path, byte_limit: int) -> List[Dict[str, Any]]:
    """Read at most the last ``byte_limit`` bytes and parse whole lines.

    A partially written trailing line (the writer is mid-append) is dropped:
    a torn line at the very end beats blocking the open on a file lock the
    daemon does not take.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return []
    with path.open("rb") as handle:
        if size > byte_limit:
            handle.seek(-byte_limit, 2)
            handle.readline()  # discard the partial line the seek lands in
        chunk = handle.read()
    raw_lines = chunk.decode("utf-8", errors="replace").splitlines()
    messages: List[Dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        shaped = _wire_message(parsed)
        if shaped is not None:
            messages.append(shaped)
    return messages


def _read_snapshot(path: Path) -> List[Dict[str, Any]]:
    """Snapshot messages, newest file wins when several carry the id."""
    try:
        candidates = (
            sorted(
                path.parent.glob(path.name.replace(path.stem, path.stem + "*")),
                key=lambda p: p.stat().st_mtime,
            )
            if path.exists()
            else []
        )
        source = candidates[-1] if candidates else path
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return []
    out: List[Dict[str, Any]] = []
    for raw in messages:
        shaped = _wire_message(raw) if isinstance(raw, dict) else None
        if shaped is not None:
            out.append(shaped)
    return out


def read_history_from_disk(
    conversations_dir: Path,
    conversation_id: str,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The saved conversation, jsonl first and snapshot as fallback.

    ``limit`` bounds the jsonl read window (tail semantics), so a huge log
    costs a bounded read, not the whole file. The snapshot fallback is
    bounded by the same window after the fact.
    """
    messages: List[Dict[str, Any]] = []
    jsonl = _jsonl_path(conversations_dir, conversation_id)
    if jsonl.exists():
        byte_limit = _MAX_LINE_BYTES
        if limit:
            byte_limit = min(_MAX_LINE_BYTES, max(limit, 64) * 4096)
        messages = _read_jsonl(jsonl, byte_limit)
    if not messages:
        messages = _read_snapshot(_snapshot_path(conversations_dir, conversation_id))
    if limit and len(messages) > limit:
        messages = messages[-limit:]
    return messages


async def read_history_from_disk_async(
    conversations_dir: Path,
    conversation_id: str,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Disk read off the event loop."""
    return await asyncio.to_thread(
        read_history_from_disk, conversations_dir, conversation_id, limit
    )


def anchor_digest(messages: List[Dict[str, Any]]) -> str:
    """The daemon's anchor rule (snapshots.ConversationSnapshot.tail): a
    SHA-256 chain over each message's canonical wire dict. Kept byte-for-byte
    compatible so a disk-seeded mirror reconciles against a live daemon."""
    digest = ""
    for message in messages:
        raw = json.dumps(message, sort_keys=True, default=str, separators=(",", ":"))
        digest = hashlib.sha256((digest + raw).encode()).hexdigest()
    return digest
