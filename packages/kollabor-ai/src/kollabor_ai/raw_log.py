"""Typed model for raw LLM interaction logs.

A single ``RawInteraction`` describes one full request/response round-trip
between Kollab and an LLM provider. The shape is uniform across providers
so anyone debugging a ``_raw.jsonl`` file always finds the same fields in
the same place — what we held locally, what went on the wire, what came
back, and how the two relate.

The wire payload is captured as a provider-native dict (deepcopied at log
time) rather than a tagged union, so upstream API changes flow through
automatically. The ``wire_provider`` field tags the dict's shape for
consumers that need to route on it.

Multi-call user turns (auto-continuations, retries) share a ``turn_id``
so a single user message produces a stitchable trace even when the
provider truncated mid-response and we issued follow-ups.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


@dataclass
class LocalMessage:
    """A conversation message as Kollab holds it before provider transform.

    Mirrors ``ConversationMessage`` but is provider-agnostic and lives in
    the log model so the request/response halves of a ``RawInteraction``
    can be read without importing event types.
    """

    role: str
    content: Any  # str in nearly all cases; provider transforms produce richer shapes
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfileSnapshot:
    """Frozen view of the profile that served this interaction."""

    provider: str
    model: str
    base_url: str = ""
    streaming: bool = False


@dataclass
class RawRequest:
    """Both views of the request: our intent and what the provider saw.

    ``conversation_local`` is the conversation history as
    ``api_communication_service`` prepared it — useful for understanding
    what Kollab thought it was sending. ``wire_request`` is the exact
    dict handed to the HTTP client (or SDK) immediately before transport.
    A consumer who wants byte-level reproducibility should rely on
    ``wire_request``; a consumer debugging Kollab's own pipeline should
    rely on ``conversation_local``.
    """

    conversation_local: List[LocalMessage] = field(default_factory=list)
    wire_request: Optional[Dict[str, Any]] = None
    wire_provider: str = ""
    tools: Optional[List[Dict[str, Any]]] = None


@dataclass
class RawResponse:
    """Everything the provider returned, post-parse but pre-business-logic."""

    content: Optional[str] = None
    token_usage: Dict[str, int] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    thinking: Optional[str] = None
    raw_chunks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RawInteraction:
    """A complete request/response round-trip."""

    schema_version: int = SCHEMA_VERSION
    turn_id: str = ""
    continuation_of: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    session_id: str = ""
    duration_s: float = 0.0
    cancelled: bool = False
    error: Optional[str] = None

    profile: ProfileSnapshot = field(
        default_factory=lambda: ProfileSnapshot(provider="", model="")
    )
    request: RawRequest = field(default_factory=RawRequest)
    response: RawResponse = field(default_factory=RawResponse)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-ready dict."""
        return asdict(self)
