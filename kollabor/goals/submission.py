"""Safe, deterministic identities for image-bearing goal submissions."""

from __future__ import annotations

import hashlib
from typing import Any


def submission_identity(client_id: str, text: str, parts: list[Any]) -> tuple[str, str]:
    """Hash a client key and its authored input without retaining raw media."""
    if not isinstance(client_id, str) or not client_id or len(client_id.encode("utf-8")) > 256:
        raise ValueError("invalid client request id")
    if any(ord(char) < 32 or ord(char) == 127 for char in client_id):
        raise ValueError("invalid client request id")
    key = hashlib.sha256(b"goal-client-id-v1\0" + client_id.encode("utf-8")).hexdigest()
    digest = hashlib.sha256(b"goal-submission-v1\0")

    def add(value: str) -> None:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    add(text)
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type") or "").strip().lower()
        if kind == "image":
            url = part.get("image")
        elif kind == "image_url":
            source = part.get("image_url")
            url = source.get("url") if isinstance(source, dict) else None
        else:
            continue
        if not isinstance(url, str):
            raise ValueError("invalid goal image")
        add(url)
    return key, digest.hexdigest()
