"""RPC exception hierarchy. Raised by RpcClient when calls fail."""

from __future__ import annotations

__all__ = [
    "RpcError",
    "RpcTimeoutError",
    "RpcMethodNotFound",
    "RpcHandlerError",
]


class RpcError(Exception):
    """Base class for all RPC failures."""


class RpcTimeoutError(RpcError):
    """Raised when no response arrives before the timeout."""


class RpcMethodNotFound(RpcError):
    """Raised when the server has no handler registered for the requested method."""


class RpcHandlerError(RpcError):
    """Raised when a remote handler raised an exception or returned non-serializable data.

    ``remote_message`` carries the server-side error string when available,
    so callers can surface the original failure reason without losing the
    client-side context in ``args[0]``.
    """

    def __init__(self, message: str, remote_message: str | None = None) -> None:
        super().__init__(message)
        self.remote_message = remote_message
