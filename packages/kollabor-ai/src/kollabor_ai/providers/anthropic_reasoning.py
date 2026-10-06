"""Signed-thinking continuity for the Anthropic provider.

Thinking blocks (``thinking`` + ``signature``, ``redacted_thinking`` + ``data``)
are opaque artifacts the API wants back, complete and unmodified, on later
turns (https://platform.claude.com/docs/en/build-with-claude/thinking).
Each block is also bound to everything sent before it: when the top-level
``system``, the ``tools`` or an earlier message changes, the block is invalid
and accounts with prefix enforcement get a 400
(https://platform.claude.com/docs/en/build-with-claude/preserved-thinking).

So a captured block carries a fingerprint of the exact request that produced
it, and is replayed only when the new request still starts with that same
prefix. Anything else is dropped, which the API allows outside a tool turn and
which adaptive-thinking models accept inside one.
"""

import copy
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from .models import ProviderType

THINKING_BLOCK_TYPES = ("thinking", "redacted_thinking")


def _canon(obj: Any) -> bytes:
    """Canonical JSON of ``obj`` without ``cache_control`` (not part of the prompt)."""

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k != "cache_control"}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return json.dumps(
        clean(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class PrefixHasher:
    """Running hash of ``system`` + ``tools`` + the messages fed so far.

    A string ``content`` and its one-text-block list render identically, so
    both hash the same; a breakpoint toggling between the two forms must not
    change the fingerprint.
    """

    def __init__(self, system: Any, tools: Any):
        self._hash = hashlib.sha256()
        self._hash.update(_canon([system, tools]))

    def add(self, message: Dict[str, Any]) -> None:
        content = message.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        self._hash.update(_canon({"role": message.get("role"), "content": content}))

    def hexdigest(self) -> str:
        return self._hash.copy().hexdigest()


def request_fingerprint(request: Dict[str, Any]) -> str:
    """Fingerprint of the full request: the prefix of the reply it produces."""
    hasher = PrefixHasher(request.get("system"), request.get("tools"))
    for message in request.get("messages", []):
        hasher.add(message)
    return hasher.hexdigest()


def build_reasoning(
    blocks: List[Dict[str, Any]], model: str, fingerprint: Callable[[], str]
) -> Optional[Dict[str, Any]]:
    """``provider_reasoning`` dict for the thinking blocks of one response.

    ``fingerprint`` is only called when there are blocks to carry, so a
    response without thinking never pays for hashing the whole history.
    """
    items = [
        copy.deepcopy(b)
        for b in blocks
        if isinstance(b, dict) and b.get("type") in THINKING_BLOCK_TYPES
    ]
    if not items:
        return None
    return {
        "provider": ProviderType.ANTHROPIC.value,
        "model": model,
        "items": items,
        "prefix_sha256": fingerprint(),
    }


def replayable_items(payload: Any, model: str) -> List[Dict[str, Any]]:
    """Thinking blocks to resend, or ``[]`` when provider/model don't match."""
    if not isinstance(payload, dict):
        return []
    if payload.get("provider") != ProviderType.ANTHROPIC.value:
        return []
    if payload.get("model") != model:
        return []
    return [
        copy.deepcopy(item)
        for item in payload.get("items") or []
        if isinstance(item, dict) and item.get("type") in THINKING_BLOCK_TYPES
    ]


class ThinkingStreamCollector:
    """Rebuilds the thinking blocks of one streamed response from raw SSE events.

    ``signature`` arrives as a ``signature_delta`` just before
    ``content_block_stop``; ``redacted_thinking`` arrives whole in
    ``content_block_start``. With ``display: omitted`` the thinking text stays
    empty but the signature is still there and still has to go back.
    """

    def __init__(self) -> None:
        self._blocks: Dict[int, Dict[str, Any]] = {}

    def feed(self, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        index = event.get("index", 0)
        if etype == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") in THINKING_BLOCK_TYPES:
                self._blocks[index] = copy.deepcopy(block)
        elif etype == "content_block_delta" and index in self._blocks:
            delta = event.get("delta") or {}
            block = self._blocks[index]
            if delta.get("type") == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta.get(
                    "thinking", ""
                )
            elif delta.get("type") == "signature_delta":
                block["signature"] = block.get("signature", "") + delta.get(
                    "signature", ""
                )

    def reasoning(
        self, model: str, fingerprint: Callable[[], str]
    ) -> Optional[Dict[str, Any]]:
        ordered = [self._blocks[i] for i in sorted(self._blocks)]
        return build_reasoning(ordered, model, fingerprint)
