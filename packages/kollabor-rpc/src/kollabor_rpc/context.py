"""Trusted, in-process caller context for RPC handlers.

The transport must construct this object only after authenticating a local
operator connection. It is never decoded from an RPC frame. The context names
the OS-level operator trust domain; it does not identify a browser, engine
client, or independent user sharing that OS account.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RpcCallerContext:
    """Immutable identity established by the daemon's local RPC transport."""

    principal_id: str
    transport: str

    @classmethod
    def from_unix_peer(cls, uid: int) -> "RpcCallerContext":
        """Build context from a Unix peer UID already verified by the server."""
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise ValueError("Unix peer UID must be a non-negative integer")
        return cls(principal_id=f"uid:{uid}", transport="unix-peer-uid")

    @classmethod
    def from_windows_local_ipc(cls) -> "RpcCallerContext":
        """Build context after the local IPC endpoint-key handshake succeeds."""
        return cls(principal_id="local-owner", transport="windows-local-ipc")

    @property
    def is_local_operator(self) -> bool:
        """Whether this context names an authenticated local operator channel."""
        return self.transport in {"unix-peer-uid", "windows-local-ipc"}


_CURRENT_RPC_CALLER: ContextVar[RpcCallerContext | None] = ContextVar(
    "kollabor_rpc_caller_context", default=None
)


def current_rpc_caller_context() -> RpcCallerContext | None:
    """Return the trusted context for the currently executing RPC handler."""
    return _CURRENT_RPC_CALLER.get()


def _set_rpc_caller_context(
    context: RpcCallerContext | None,
) -> Token[RpcCallerContext | None]:
    return _CURRENT_RPC_CALLER.set(context)


def _reset_rpc_caller_context(token: Token[RpcCallerContext | None]) -> None:
    _CURRENT_RPC_CALLER.reset(token)


__all__ = ["RpcCallerContext", "current_rpc_caller_context"]
