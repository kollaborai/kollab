"""HubStateClient: cross-agent StateService access over the hub socket.

Phase 6 of the daemon transparency refactor. Lets one kollab agent call
StateService methods on another agent by wrapping the phase-1 RPC
transport in a friendly context manager.

Usage (from inside an agent's plugin or command):

    from kollabor.state import HubStateClient

    async with HubStateClient.connect("lapis") as peer:
        stats = await peer.get_session_stats()
        profile = await peer.get_active_profile()
        content = await peer.save_conversation("markdown")
        print(f"lapis has {stats.messages} messages, profile={profile.name}")

Discovery:
    Peer agents are looked up by identity string via the hub presence
    files at ~/.kollab/hub/presence/{agent_id}.json. Each presence
    file contains a socket_path that points to the peer's unix socket
    in /tmp/kollabor-hub. HubStateClient.connect(identity) walks the
    presence directory looking for a file with a matching identity.

Protocol:
    The peer's hub socket already speaks the phase-1 RPC protocol
    (rpc_request -> rpc_reply frames, NDJSON on the wire). No new
    protocol is needed -- we just open a connection and use RpcClient
    the same way the attach path does, plus a background reader task
    that routes replies into the client.

Auth:
    Phase 6 is an MVP. All registered handlers on the peer's RpcServer
    are callable -- there is no per-method auth check yet. That's
    acceptable because the whole mesh runs on the same machine under
    the same user, and the socket is in /tmp/kollabor-hub. A future
    phase can add method-level auth (readable-by-peer, writable-by-self)
    inside RpcServer.handle_request so the policy lives in one place.

Lifecycle:
    connect() is an async context manager. It opens the socket, starts
    a background reader task, hands back a client. On exit (normal or
    exception) it cancels the reader, closes the RpcClient, and closes
    the socket. Pending calls are cancelled with RpcError("client closed").
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from .snapshots import (
    ConversationSnapshot,
    HubSnapshot,
    McpSnapshot,
    PermissionSnapshot,
    ProcessingSnapshot,
    ProfileListSnapshot,
    ProfileSnapshot,
    SessionStats,
    SystemInfoSnapshot,
)

logger = logging.getLogger(__name__)


class HubStateClientError(Exception):
    """Raised when HubStateClient cannot connect or discover a peer."""


class HubStateClient:
    """Cross-agent StateService client backed by the hub socket.

    Implements the same StateService read methods as LocalStateService
    and RemoteStateService, but routes them over the peer's unix
    socket instead of in-process. Returns the same snapshot DTOs, so
    caller code is identical to calling your own state_service.

    Phase 6 exposes only read methods. Write methods (set_active_profile,
    set_approval_mode) are intentionally omitted -- letting one agent
    mutate another agent's state without auth is too big a hole. A
    follow-up phase can add opt-in write permissions.
    """

    # Default timeout matches RemoteStateService so caller expectations
    # stay consistent across local / attach / hub contexts.
    DEFAULT_TIMEOUT: float = 10.0

    def __init__(
        self,
        rpc_client: Any,
        *,
        timeout: float | None = None,
        peer_identity: str = "",
    ) -> None:
        self._rpc = rpc_client
        self._timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self._peer_identity = peer_identity

    @property
    def peer_identity(self) -> str:
        """The identity string of the peer we're connected to."""
        return self._peer_identity

    # === Read methods (mirror RemoteStateService) ===

    async def get_conversation(self) -> ConversationSnapshot:
        result = await self._rpc.call(
            "state.get_conversation", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_conversation expected dict, got {type(result).__name__}"
            )
        return ConversationSnapshot.from_dict(result)

    async def save_conversation(self, format: str = "transcript") -> str:
        result = await self._rpc.call(
            "state.save_conversation",
            {"format": format},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.save_conversation expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        content = result.get("content")
        if not isinstance(content, str):
            raise TypeError("state.save_conversation result missing 'content' string")
        return content

    async def get_session_stats(self) -> SessionStats:
        result = await self._rpc.call(
            "state.get_session_stats", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_session_stats expected dict, got {type(result).__name__}"
            )
        return SessionStats.from_dict(result)

    async def get_active_profile(self) -> ProfileSnapshot:
        result = await self._rpc.call(
            "state.get_active_profile", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_active_profile expected dict, got {type(result).__name__}"
            )
        return ProfileSnapshot.from_dict(result)

    async def list_profiles(self) -> ProfileListSnapshot:
        result = await self._rpc.call("state.list_profiles", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.list_profiles expected dict, got {type(result).__name__}"
            )
        return ProfileListSnapshot.from_dict(result)

    async def get_permission_state(self) -> PermissionSnapshot:
        result = await self._rpc.call(
            "state.get_permission_state", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_permission_state expected dict, got {type(result).__name__}"
            )
        return PermissionSnapshot.from_dict(result)

    async def get_mcp_state(self) -> McpSnapshot:
        result = await self._rpc.call("state.get_mcp_state", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_mcp_state expected dict, got {type(result).__name__}"
            )
        return McpSnapshot.from_dict(result)

    async def get_hub_state(self) -> HubSnapshot:
        result = await self._rpc.call("state.get_hub_state", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_hub_state expected dict, got {type(result).__name__}"
            )
        return HubSnapshot.from_dict(result)

    async def get_processing_state(self) -> ProcessingSnapshot:
        result = await self._rpc.call(
            "state.get_processing_state", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_processing_state expected dict, got {type(result).__name__}"
            )
        return ProcessingSnapshot.from_dict(result)

    async def get_system_info(self) -> SystemInfoSnapshot:
        result = await self._rpc.call(
            "state.get_system_info", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_system_info expected dict, got {type(result).__name__}"
            )
        return SystemInfoSnapshot.from_dict(result)

    async def ping(self) -> dict[str, Any]:
        """Call the daemon's ping handler. Useful for liveness checks.

        Returns the raw result dict from the peer's ping handler
        (status, daemon_pid, uptime, identity). Not wrapped in a
        snapshot because it's meant for quick diagnostic calls.
        """
        result = await self._rpc.call("ping", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(f"ping expected dict, got {type(result).__name__}")
        return result

    # === Discovery + connection ===

    @staticmethod
    def discover_peer_socket(peer_identity: str) -> Path | None:
        """Find the unix socket for an agent by hub identity.

        Walks the presence directory (~/.kollab/hub/presence/)
        and returns the first presence file whose "identity" field
        matches ``peer_identity``. Returns the socket path as a
        pathlib.Path or None if no matching agent is found.

        Matching is case-sensitive. The first match wins if two agents
        happen to share an identity (shouldn't happen, but guards
        against a corrupt presence state).
        """
        try:
            from plugins.hub.presence import get_presence_dir

            presence_dir = get_presence_dir()
        except Exception as e:
            logger.debug(f"HubStateClient discover: home path error: {e}")
            return None

        if not presence_dir.exists():
            return None

        for presence_file in presence_dir.glob("*.json"):
            try:
                with open(presence_file) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.debug(
                    f"HubStateClient discover: skip unreadable {presence_file.name}: {e}"
                )
                continue

            if data.get("identity") == peer_identity:
                socket_path = data.get("socket_path", "")
                if socket_path:
                    return Path(socket_path)

        return None

    @classmethod
    @contextlib.asynccontextmanager
    async def connect(
        cls,
        peer_identity: str,
        *,
        timeout: float | None = None,
    ) -> "AsyncIterator[HubStateClient]":
        """Connect to a peer agent by identity and yield a HubStateClient.

        Async context manager. Opens the unix socket, wraps it in an
        RpcClient with a background reader task that routes rpc_reply
        frames to the client's on_reply. On exit, cancels the reader,
        closes the client (cancelling pending calls with RpcError),
        and closes the socket.

        Uses kollabor_rpc.open_unix_connection_with_large_buffer so
        large responses (full conversation history) work without
        hitting the default 64KB StreamReader limit.

        Raises HubStateClientError if the peer can't be discovered or
        the socket can't be opened.

        Args:
            peer_identity: Hub identity string of the peer agent
                (e.g. "lapis", "koordinator").
            timeout: Per-call RPC timeout in seconds. Defaults to
                HubStateClient.DEFAULT_TIMEOUT.

        Yields:
            HubStateClient instance wired to the peer.
        """
        socket_path = cls.discover_peer_socket(peer_identity)
        if socket_path is None:
            raise HubStateClientError(
                f"no peer with identity {peer_identity!r} "
                f"found in ~/.kollab/hub/presence/"
            )
        if not socket_path.exists():
            raise HubStateClientError(
                f"peer {peer_identity!r} has stale presence file: "
                f"socket {socket_path} does not exist"
            )

        # Local imports keep kollabor_rpc as an optional boundary --
        # callers get an ImportError if they invoke this without
        # kollabor_rpc installed, which is the correct signal.
        from kollabor_rpc import RpcClient, open_unix_connection_with_large_buffer

        try:
            reader, writer = await open_unix_connection_with_large_buffer(
                str(socket_path)
            )
        except Exception as e:
            raise HubStateClientError(
                f"failed to open socket for {peer_identity!r} at {socket_path}: {e}"
            ) from e

        rpc = RpcClient(writer)
        client = cls(
            rpc,
            timeout=timeout,
            peer_identity=peer_identity,
        )

        async def _reply_router() -> None:
            """Read NDJSON frames from the peer's socket and route
            rpc_reply messages to the RpcClient. Ignores other message
            types (attach events, heartbeats, etc.) since this
            connection is only used for RPC.
            """
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    try:
                        msg = json.loads(line.decode("utf-8").strip())
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if msg.get("action") == "rpc_reply":
                        rpc.on_reply(msg)
                    # Silently ignore other message types (heartbeats,
                    # state_snapshots, etc) -- this connection is
                    # RPC-only.
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"HubStateClient reply router exited: {e}")

        router_task = asyncio.create_task(
            _reply_router(), name=f"hub_state_client:{peer_identity}"
        )

        try:
            yield client
        finally:
            # Cancel router first so no new replies arrive during teardown.
            router_task.cancel()
            try:
                await router_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"HubStateClient router cancel error: {e}")

            # Close the RpcClient (cancels pending calls).
            try:
                rpc.close()
            except Exception as e:
                logger.debug(f"HubStateClient rpc close error: {e}")

            # Close the socket writer.
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug(f"HubStateClient writer close error: {e}")
