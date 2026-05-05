"""Wire-safe RPC request/response models.

All types round-trip through JSON via to_wire/from_wire. These are plain
dataclasses with no external serialization library dependency.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


def new_request_id() -> str:
    """Generate a new RPC request id.

    Returns a stateless 32-character hex string derived from uuid4. Collision
    probability is negligible in practice, so callers can treat each id as
    globally unique without coordination.
    """
    return uuid.uuid4().hex


@dataclass(slots=True)
class RpcRequest:
    """A single RPC call from client to server."""

    request_id: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the wire dict carried over the hub socket."""
        return {
            "action": "rpc_request",
            "request_id": self.request_id,
            "method": self.method,
            "params": self.params,
            "timeout": self.timeout,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> RpcRequest:
        """Reconstruct from a wire dict.

        Tolerant of missing ``action`` field (the dispatcher usually strips
        it before calling this). ``params`` and ``timeout`` fall back to
        empty dict and 30.0 respectively.
        """
        return cls(
            request_id=data["request_id"],
            method=data["method"],
            params=data.get("params") or {},
            timeout=float(data.get("timeout", 30.0)),
        )


@dataclass(slots=True)
class RpcResponse:
    """A single RPC reply from server to client.

    ``error_kind`` is one of the literal strings "not_found", "handler",
    or "serialization", or None for successful replies. Kept as plain str
    (not typing.Literal) to avoid python-version-specific type hint issues.
    """

    request_id: str
    result: Any | None = None
    error: str | None = None
    error_kind: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the wire dict carried over the hub socket."""
        return {
            "action": "rpc_reply",
            "request_id": self.request_id,
            "result": self.result,
            "error": self.error,
            "error_kind": self.error_kind,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> RpcResponse:
        """Reconstruct from a wire dict. Only ``request_id`` is required."""
        return cls(
            request_id=data["request_id"],
            result=data.get("result"),
            error=data.get("error"),
            error_kind=data.get("error_kind"),
        )

    @property
    def is_success(self) -> bool:
        """True when both ``error`` and ``error_kind`` are None."""
        return self.error is None and self.error_kind is None
