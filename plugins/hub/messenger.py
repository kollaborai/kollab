"""Socket-based messaging between agents."""

import asyncio
import json
import logging
import os
import secrets
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import HubMessage
from .presence import _atomic_write, get_messages_dir, get_socket_dir

# --- Durable inbox bounds ---
# Max ordinary messages kept on disk per inbox. Durable task controls are
# preserved until normal receive can classify them; oldest ordinary entries
# are evicted first at write time.
INBOX_MAX_SIZE: int = 50
# Messages older than this (seconds) are silently discarded on read.
INBOX_TTL_SECS: int = 7 * 86400  # 7 days
# Max ordinary messages replayed to a reconnecting agent per mailbox poll.
# Durable task controls bypass this cap and retain normal receive semantics.
INBOX_MAX_REPLAY: int = 20

# Idle timeout (seconds) for the off-box read loop. An authenticated remote
# peer that handshakes then sends nothing (or stops between actions) is dropped
# after this long, capping the per-connection resource hold. Local unix
# connections are unbounded (cooperative same-UID peers). Does NOT affect the
# `attach` live-stream path, which exits the read loop before streaming.
REMOTE_IDLE_TIMEOUT: float = 30.0


def _coerce_line_count(value: Any, default: int) -> int:
    """Normalize optional line counts received from native tools or sockets."""
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        count = default
    return max(1, count)

logger = logging.getLogger(__name__)


def _is_durable_control(data: Any) -> bool:
    """Return whether a mailbox payload must bypass ordinary replay limits.

    Task assignments require normal receive classification, and task-cron
    reminders require an explicit stale/active decision plus correlated ACK.
    They therefore have priority over ordinary chatter at every inbox bound.
    Valid JSON scalars and arrays are malformed mailbox records, not controls.
    """
    if not isinstance(data, dict):
        return False
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return bool(
        metadata.get("task_cron")
        or metadata.get("task_assignment")
        or data.get("from_identity") == "task-cron"
    )


def _prune_inbox(msg_dir: Path, max_size: int = INBOX_MAX_SIZE) -> None:
    """Evict oldest ordinary messages while preserving durable controls.

    ``max_size`` bounds ordinary chatter, not task controls that still require
    normal receive classification or an explicit stale ACK. Malformed files
    are treated as ordinary and remain eligible for eviction.
    """
    try:
        files = sorted(msg_dir.glob("*.json"))
        ordinary_files: List[Path] = []
        control_count = 0
        for path in files:
            try:
                with open(path) as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
            if _is_durable_control(data):
                control_count += 1
            else:
                ordinary_files.append(path)

        excess = len(ordinary_files) - max_size
        if excess <= 0:
            return
        for path in ordinary_files[:excess]:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        logger.info(
            "Inbox prune: evicted %d oldest ordinary message(s) from %r "
            "(ordinary_limit=%d, controls_preserved=%d)",
            excess,
            msg_dir.name,
            max_size,
            control_count,
        )
    except Exception as e:
        logger.debug(f"Inbox prune failed for {msg_dir}: {e}")


class AgentSocketServer:
    """Per-agent unix domain socket server for receiving messages.

    Each agent runs one of these. Other agents connect to deliver
    messages directly (peer-to-peer, no hub in the middle).
    """

    def __init__(
        self,
        agent_id: str,
        on_message: Callable,
        on_get_output: Optional[Callable] = None,
        on_shutdown: Optional[Callable] = None,
        on_input_inject: Optional[Callable] = None,
        socket_name: Optional[str] = None,
    ):
        self.agent_id = agent_id
        # Use identity-based name when provided, fall back to agent_id
        self.socket_path = get_socket_dir() / f"{socket_name or agent_id}.sock"
        self._server: Optional[asyncio.AbstractServer] = None
        self._on_message = on_message
        self._on_get_output = on_get_output
        self._on_shutdown = on_shutdown
        self._on_input_inject = on_input_inject
        self._identity: Optional[Dict] = None
        self._started_at: float = time.time()
        self._shutdown_requested = False
        self._display_tap = None  # Set by hub plugin for live attach
        self._identity_info = None  # Set by hub plugin (AgentRuntime)
        self._rpc_server = None  # Set by hub plugin; kollabor_rpc.RpcServer instance

        # Ed25519 challenge-response authentication
        self._auth_enabled: bool = False  # OFF until set_dns_auth() enables it
        self._dns_registry: Optional[Any] = None  # AgentRegistry for pubkey lookup
        self._dns_identity: Optional[Any] = None  # IdentityManager for verify

        # Off-box TCP/TLS endpoint (None until enable_endpoint() is called).
        # Remote connections share _handle_connection but ALWAYS require the
        # Ed25519 handshake regardless of the local-socket auth setting.
        self._tcp_server: Optional[asyncio.AbstractServer] = None
        self._tcp_host: Optional[str] = None
        self._tcp_port: Optional[int] = None
        self._tcp_ssl: Optional[Any] = None
        # Last bind error (or "") so `/hub dns endpoint` can explain *why*
        # the off-box listener isn't up. Cleared on a successful bind.
        self._endpoint_bind_error: str = ""
        # Idle read timeout for off-box connections (overridable for tests).
        self._remote_idle_timeout: float = REMOTE_IDLE_TIMEOUT

        # Listening-server shutdown does not close already accepted streams.
        # Own both sides explicitly so stop() can drain connection handlers
        # before their event loop is torn down.
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._connection_writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> str:
        """Start the socket server. Returns the socket path."""
        # Clean stale socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        try:
            self._server = await asyncio.wait_for(
                asyncio.start_unix_server(
                    self._accept_connection, path=str(self.socket_path)
                ),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(f"socket bind timeout: {self.socket_path}")
        except Exception as e:
            raise RuntimeError(f"socket bind failed: {e}")
        os.chmod(str(self.socket_path), 0o600)
        logger.info(f"Agent socket listening: {self.socket_path}")

        # Off-box TCP/TLS listener (optional). Same handler as the unix
        # socket; remote connections are forced through the Ed25519
        # handshake. A bind failure here is non-fatal — the unix socket
        # keeps the local mesh working.
        if self._tcp_host is not None and self._tcp_port is not None:
            try:
                self._tcp_server = await asyncio.wait_for(
                    asyncio.start_server(
                        lambda r, w: self._accept_connection(r, w, require_auth=True),
                        host=self._tcp_host,
                        port=self._tcp_port,
                        ssl=self._tcp_ssl,
                    ),
                    timeout=10.0,
                )
                bound = (
                    self._tcp_server.sockets[0].getsockname()
                    if self._tcp_server.sockets
                    else (self._tcp_host, self._tcp_port)
                )
                self._endpoint_bind_error = ""
                logger.info(
                    "Agent endpoint listening: %s:%s (%s)",
                    bound[0],
                    bound[1],
                    "TLS" if self._tcp_ssl else "plaintext",
                )
            except (asyncio.TimeoutError, OSError) as e:
                self._endpoint_bind_error = f"{type(e).__name__}: {e}"
                logger.error(
                    "endpoint bind failed (%s:%s): %s — unix socket still active",
                    self._tcp_host,
                    self._tcp_port,
                    e,
                )
                self._tcp_server = None

        return str(self.socket_path)

    @staticmethod
    def _get_peer_credentials(
        writer: asyncio.StreamWriter,
    ) -> Optional[tuple]:
        """Get (pid, uid) of the connecting process from the unix socket.

        Returns (pid, uid) on success, (None, uid) if pid unavailable,
        or None if peer credentials are not supported on this platform.

        - Linux: uses SO_PEERCRED (struct ucred)
        - macOS: uses getpeereid() via ctypes
        - Other: returns None (graceful degradation)
        """
        import struct
        import sys

        try:
            transport = writer.transport
            sock = transport.get_extra_info("socket")
            if sock is None:
                logger.debug("peer cred: no underlying socket available")
                return None

            if sys.platform == "linux":
                # SO_PEERCRED returns struct ucred { pid_t, uid_t, gid_t }
                SO_PEERCRED = 17  # linux-specific, not always in socket module
                cred_size = struct.calcsize("3i")
                raw = sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, cred_size)
                pid, uid, _gid = struct.unpack("3i", raw)
                return (pid, uid)

            elif sys.platform == "darwin":
                # macOS: getpeereid() via ctypes (not exposed in Python socket)
                import ctypes
                import ctypes.util

                lib_path = ctypes.util.find_library("c")
                if not lib_path:
                    logger.debug("peer cred: could not locate libc on macOS")
                    return None
                libc = ctypes.CDLL(lib_path)

                fd = sock.fileno()
                euid = ctypes.c_uint32()
                egid = ctypes.c_uint32()

                # getpeereid(int fd, uid_t *euid, gid_t *egid) -> 0 on success
                if libc.getpeereid(fd, ctypes.byref(euid), ctypes.byref(egid)) == 0:
                    return (None, euid.value)  # pid not available on macOS

                logger.debug("peer cred: getpeereid() failed on macOS")
                return None

            else:
                logger.debug(
                    "peer cred: unsupported platform '%s', skipping check",
                    sys.platform,
                )
                return None

        except (OSError, AttributeError, TypeError) as exc:
            logger.debug("peer cred: could not retrieve credentials: %s", exc)
            return None

    async def _do_handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> Optional[str]:
        """Ed25519 challenge-response handshake.

        Protocol:
          1. Server sends {"type": "auth_challenge", "nonce": "<hex>"}
          2. Client sends {"type": "auth_response", "designation": "lapis",
                           "signature": "<hex>"}
          3. Server verifies signature against DNS registry public key
          4. On success: returns the authenticated designation
          5. On failure: sends auth_rejected and returns None

        Returns the authenticated designation on success, None on failure.
        """
        nonce = secrets.token_hex(32)

        # Send challenge
        challenge = (
            json.dumps(
                {
                    "type": "auth_challenge",
                    "nonce": nonce,
                }
            )
            + "\n"
        )
        writer.write(challenge.encode())
        await writer.drain()

        # Wait for response (10s timeout)
        try:
            resp_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        except asyncio.TimeoutError:
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": "handshake timeout",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            return None

        if not resp_line:
            return None

        try:
            resp = json.loads(resp_line.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": "invalid auth_response JSON",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            return None

        if resp.get("type") != "auth_response":
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": f"expected auth_response, got {resp.get('type', 'none')}",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            return None

        designation = resp.get("designation", "")
        signature_hex = resp.get("signature", "")

        if not designation or not signature_hex:
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": "missing designation or signature",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            return None

        # Look up public key from DNS registry
        if self._dns_registry is None:
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": "DNS registry not available",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            logger.warning("auth rejected: DNS registry not initialized")
            return None

        record = self._dns_registry.resolve(designation)
        if record is None or not getattr(record, "public_key", ""):
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": f"unknown designation: {designation}",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            logger.warning(f"auth rejected: unknown designation '{designation}'")
            return None

        # Verify Ed25519 signature of nonce
        if self._dns_identity is None:
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": "identity manager not available",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            logger.warning("auth rejected: identity manager not initialized")
            return None

        nonce_bytes = nonce.encode("utf-8")
        valid = self._dns_identity.verify_signature(
            record.public_key, nonce_bytes, signature_hex
        )

        if not valid:
            rej = (
                json.dumps(
                    {
                        "type": "auth_rejected",
                        "reason": "signature verification failed",
                    }
                )
                + "\n"
            )
            writer.write(rej.encode())
            await writer.drain()
            logger.warning(
                f"auth rejected: bad signature for '{designation}' "
                f"(pub={record.public_key[:16]}...)"
            )
            return None

        # Authenticated — send success
        ack = (
            json.dumps(
                {
                    "type": "auth_ok",
                    "designation": designation,
                }
            )
            + "\n"
        )
        writer.write(ack.encode())
        await writer.drain()
        logger.info(f"socket auth succeeded for '{designation}'")
        return designation

    def _accept_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        require_auth: bool = False,
    ) -> None:
        """Own an accepted stream until its connection handler is drained."""
        self._connection_writers.add(writer)
        task = asyncio.create_task(
            self._own_connection(reader, writer, require_auth=require_auth)
        )
        self._connection_tasks.add(task)

    async def _own_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        require_auth: bool = False,
    ) -> None:
        """Run and clean up one accepted connection owned by this server."""
        task = asyncio.current_task()
        try:
            await self._handle_connection(reader, writer, require_auth=require_auth)
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except (Exception, asyncio.CancelledError):
                pass
            self._connection_writers.discard(writer)
            if task is not None:
                self._connection_tasks.discard(task)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        require_auth: bool = False,
    ):
        """Handle incoming connection.

        ``require_auth=True`` is set by the off-box TCP listener and forces
        the Ed25519 handshake regardless of the local-socket auth setting.
        """
        # --- Peer credential check ---
        # Verify the connecting process runs under the same UID. This is a
        # same-host gate and is meaningless off-box, where the Ed25519
        # handshake is the real gate — so skip it for remote connections.
        peer_cred = None if require_auth else self._get_peer_credentials(writer)
        if require_auth:
            pass  # remote connection — UID check skipped, handshake enforced below
        elif peer_cred is not None:
            peer_pid, peer_uid = peer_cred
            our_uid = os.getuid()
            if peer_uid != our_uid:
                logger.warning(
                    "Rejected socket connection from UID %d "
                    "(expected %d, pid=%s) — closing",
                    peer_uid,
                    our_uid,
                    peer_pid if peer_pid is not None else "unknown",
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return
            # Credentials OK — store for connection-context logging
            logger.debug(
                "Accepted socket connection from UID %d (pid=%s)",
                peer_uid,
                peer_pid if peer_pid is not None else "unknown",
            )
        else:
            # Platform doesn't support peer credentials — log but allow
            logger.info(
                "Peer credential check skipped (unsupported platform or "
                "socket type) — connection allowed"
            )

        # --- Ed25519 challenge-response auth ---
        if self._auth_enabled or require_auth:
            if self._dns_registry is None or self._dns_identity is None:
                # DNS subsystem not initialized yet — reject
                logger.warning("auth required but DNS not ready — closing connection")
                try:
                    rej = (
                        json.dumps(
                            {
                                "type": "auth_rejected",
                                "reason": "authentication service not ready",
                            }
                        )
                        + "\n"
                    )
                    writer.write(rej.encode())
                    await writer.drain()
                except Exception:
                    pass
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return

            authenticated_as = await self._do_handshake(reader, writer)
            if authenticated_as is None:
                # Handshake failed — connection already cleaned up in _do_handshake
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return
            # Connection is now authenticated as `authenticated_as`
            logger.debug(f"connection authenticated as '{authenticated_as}'")

        try:
            while True:
                # Off-box connections get an idle read timeout so an
                # authenticated-but-idle peer can't hold connections open
                # forever. Local unix peers are cooperative same-UID processes
                # and stay unbounded. (`attach` exits this loop before it
                # streams, so live attach is unaffected.)
                if require_auth:
                    try:
                        line = await asyncio.wait_for(
                            reader.readline(), timeout=self._remote_idle_timeout
                        )
                    except asyncio.TimeoutError:
                        logger.debug(
                            "remote connection idle >%ss — closing",
                            self._remote_idle_timeout,
                        )
                        break
                else:
                    line = await reader.readline()
                if not line:
                    break

                try:
                    msg_data = json.loads(line.decode().strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                action = msg_data.get("action", "")

                if action == "message":
                    msg = HubMessage.from_dict(msg_data)
                    await self._on_message(msg)
                    ack = json.dumps({"type": "ack", "id": msg.id}) + "\n"
                    writer.write(ack.encode())
                    await writer.drain()

                elif action == "ping":
                    pong = (
                        json.dumps(
                            {
                                "type": "pong",
                                "agent_id": self.agent_id,
                            }
                        )
                        + "\n"
                    )
                    writer.write(pong.encode())
                    await writer.drain()

                elif action == "get_context":
                    # Return recent conversation context for social awareness
                    lines_requested = _coerce_line_count(msg_data.get("lines"), 200)
                    context = await self._get_context(lines_requested)
                    resp = (
                        json.dumps(
                            {
                                "type": "context",
                                "content": context,
                            }
                        )
                        + "\n"
                    )
                    writer.write(resp.encode())
                    await writer.drain()

                elif action == "roster_update":
                    # Hub pushing updated roster
                    await self._on_message(
                        HubMessage(
                            action="roster_update",
                            content=json.dumps(msg_data.get("agents", [])),
                            from_identity="hub",
                        )
                    )
                    ack = json.dumps({"type": "ack"}) + "\n"
                    writer.write(ack.encode())
                    await writer.drain()

                elif action == "get_output":
                    lines_requested = _coerce_line_count(msg_data.get("lines"), 100)
                    output_lines: List[str] = []
                    if self._on_get_output:
                        try:
                            result = self._on_get_output(lines_requested)
                            if asyncio.iscoroutine(result):
                                result = await result
                            if isinstance(result, list):
                                output_lines = result
                        except Exception as exc:
                            logger.debug(f"get_output callback error: {exc}")
                    resp = json.dumps({"type": "output", "lines": output_lines}) + "\n"
                    writer.write(resp.encode())
                    await writer.drain()

                elif action in ("subscribe", "attach"):
                    # Live attach: stream display events to client
                    mode = msg_data.get("mode", "readonly")
                    client_id = msg_data.get("client_id", f"attach-{id(writer)}")

                    if not self._display_tap:
                        resp = (
                            json.dumps(
                                {"type": "error", "msg": "display tap not available"}
                            )
                            + "\n"
                        )
                        writer.write(resp.encode())
                        await writer.drain()
                    else:
                        # Build hub info for client status bar
                        hub_info = {}
                        if self._identity_info:
                            hub_info["identity"] = getattr(
                                self._identity_info, "identity", ""
                            )
                            hub_info["is_coordinator"] = getattr(
                                self._identity_info, "is_coordinator", False
                            )

                        # Register the subscriber before ack/snapshot so the
                        # daemon can see a visible attach client immediately.
                        # Permission prompts may be requested as soon as input
                        # is accepted, while the snapshot can still be draining.
                        sub_queue = self._display_tap.subscribe(client_id)

                        # Send ack
                        ack = (
                            json.dumps(
                                {
                                    "type": "attach_ack",
                                    "agent_id": self.agent_id,
                                    "mode": mode,
                                    "uptime": int(time.time() - self._started_at),
                                    "hub": hub_info,
                                }
                            )
                            + "\n"
                        )
                        writer.write(ack.encode())
                        await writer.drain()

                        # Send snapshot (catch-up) - one event per line
                        # to avoid exceeding readline buffer limits
                        snapshot = self._display_tap.get_snapshot()
                        for event in snapshot:
                            line = json.dumps(event, default=str) + "\n"
                            writer.write(line.encode())
                        await writer.drain()

                        # Enter persistent streaming loop
                        await self._stream_to_attacher(
                            reader,
                            writer,
                            client_id,
                            mode,
                            sub_queue=sub_queue,
                        )
                        return  # Connection ends when attacher detaches

                elif action == "get_status":
                    status_data = {
                        "type": "status",
                        "identity": "",
                        "state": "unknown",
                        "pid": os.getpid(),
                        "uptime": int(time.time() - self._started_at),
                        "current_task": "",
                    }
                    if self._identity:
                        status_data["identity"] = self._identity.get(
                            "identity", ""
                        )  # external attr
                        status_data["state"] = self._identity.get("state", "unknown")
                        status_data["pid"] = self._identity.get("pid", os.getpid())
                        status_data["current_task"] = self._identity.get(
                            "current_task", ""
                        )
                    resp = json.dumps(status_data) + "\n"
                    writer.write(resp.encode())
                    await writer.drain()

                elif action == "shutdown":
                    reason = msg_data.get("reason", "")
                    logger.info(f"Shutdown requested: {reason or 'no reason given'}")
                    ack = json.dumps({"type": "ack"}) + "\n"
                    writer.write(ack.encode())
                    await writer.drain()
                    self._shutdown_requested = True
                    if self._on_shutdown:
                        try:
                            result = self._on_shutdown(reason)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as exc:
                            logger.debug(f"shutdown callback error: {exc}")

                elif action == "rpc_request":
                    # Handshake-phase RPC: no concurrent writer, safe to write
                    # directly without a lock. Attached-phase RPC is handled
                    # separately in _recv_input below with a write lock.
                    if self._rpc_server is None:
                        resp = {
                            "action": "rpc_reply",
                            "request_id": msg_data.get("request_id", ""),
                            "error": "rpc server not initialized",
                            "error_kind": "handler",
                        }
                    else:
                        try:
                            resp = await self._rpc_server.handle_wire(msg_data)
                        except Exception as exc:
                            logger.warning(
                                "rpc handle_wire failed: %s", exc, exc_info=True
                            )
                            resp = {
                                "action": "rpc_reply",
                                "request_id": msg_data.get("request_id", ""),
                                "error": repr(exc),
                                "error_kind": "handler",
                            }
                    writer.write((json.dumps(resp, default=str) + "\n").encode("utf-8"))
                    await writer.drain()

        except Exception as e:
            logger.debug(f"Connection handler error: {e}")

    async def _get_context(self, lines: int) -> str:
        """Get recent context - override in plugin integration."""
        return ""

    def set_identity(self, identity_dict: Dict) -> None:
        """Set the identity dict used for get_status responses."""
        self._identity = identity_dict

    def set_dns_auth(
        self,
        registry: Any,
        identity_manager: Any,
        require_auth: bool = False,
    ) -> None:
        """Wire DNS registry + identity manager for Ed25519 handshake auth.

        Called by HubPlugin after DNS subsystem initializes.
        If require_auth is False, handshake is skipped (dev mode).
        """
        self._dns_registry = registry
        self._dns_identity = identity_manager
        self._auth_enabled = require_auth
        if require_auth:
            logger.info("socket auth enabled (Ed25519 challenge-response)")
        else:
            logger.info("socket auth DISABLED (dev mode)")

    def enable_endpoint(self, host: str, port: int, ssl_ctx: Any = None) -> None:
        """Enable an off-box TCP/TLS listener sharing the same handler.

        Must be called BEFORE ``start()`` so the listener binds. Remote
        connections are always required to complete the Ed25519 handshake
        (the local-socket ``require_auth`` setting does not relax this);
        the handshake still needs ``set_dns_auth()`` to have wired the
        registry + identity manager.
        """
        self._tcp_host = host
        self._tcp_port = port
        self._tcp_ssl = ssl_ctx
        logger.info(
            "endpoint configured: %s:%s [%s]",
            host,
            port,
            "TLS" if ssl_ctx is not None else "plaintext",
        )

    @property
    def shutdown_requested(self) -> bool:
        """Whether a remote shutdown has been requested."""
        return self._shutdown_requested

    async def _stream_to_attacher(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client_id: str,
        mode: str,
        sub_queue: Any | None = None,
    ) -> None:
        """Persistent streaming loop for an attached client.

        Streams display events from DisplayTap to the attacher.
        Optionally accepts input from the attacher (interactive mode).
        """
        import queue as queue_mod

        if self._display_tap is None:
            return
        if sub_queue is None:
            sub_queue = self._display_tap.subscribe(client_id)

        # Serialize writes on the shared StreamWriter between _send_events
        # (server->client event stream) and the RPC reply branch in
        # _recv_input. Without this lock, NDJSON frames can interleave
        # mid-drain() and corrupt the wire protocol.
        write_lock = asyncio.Lock()

        async def _send_events():
            try:
                while True:
                    try:
                        event = await asyncio.to_thread(sub_queue.get, timeout=5.0)
                    except queue_mod.Empty:
                        # Heartbeat
                        hb = json.dumps({"type": "heartbeat"}) + "\n"
                        async with write_lock:
                            writer.write(hb.encode())
                            await writer.drain()
                        continue

                    line = json.dumps(event, default=str) + "\n"
                    async with write_lock:
                        writer.write(line.encode())
                        await writer.drain()
            except (ConnectionError, BrokenPipeError, OSError):
                pass

        async def _recv_input():
            try:
                while True:
                    data = await reader.readline()
                    if not data:
                        break
                    try:
                        msg = json.loads(data.decode().strip())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                    # RPC request on attached connection - dispatch to
                    # RpcServer. Must be checked before the msg_type branches
                    # because rpc frames use "action", not "type".
                    if msg.get("action") == "rpc_request":
                        if self._rpc_server is not None:
                            try:
                                reply = await self._rpc_server.handle_wire(msg)
                                line = json.dumps(reply, default=str) + "\n"
                                async with write_lock:
                                    writer.write(line.encode("utf-8"))
                                    await writer.drain()
                            except Exception as exc:
                                logger.warning(
                                    "rpc request failed on attached connection: %s",
                                    exc,
                                    exc_info=True,
                                )
                                error_reply = {
                                    "action": "rpc_reply",
                                    "request_id": msg.get("request_id", ""),
                                    "error": repr(exc),
                                    "error_kind": "handler",
                                }
                                try:
                                    async with write_lock:
                                        writer.write(
                                            (
                                                json.dumps(error_reply, default=str)
                                                + "\n"
                                            ).encode("utf-8")
                                        )
                                        await writer.drain()
                                except Exception:
                                    pass
                        else:
                            # No RpcServer wired; return handler error so the
                            # client doesn't hang forever waiting for a reply.
                            error_reply = {
                                "action": "rpc_reply",
                                "request_id": msg.get("request_id", ""),
                                "error": "rpc server not initialized",
                                "error_kind": "handler",
                            }
                            try:
                                async with write_lock:
                                    writer.write(
                                        (
                                            json.dumps(error_reply, default=str) + "\n"
                                        ).encode("utf-8")
                                    )
                                    await writer.drain()
                            except Exception:
                                pass
                        continue

                    msg_type = msg.get("type", "")
                    if msg_type == "detach":
                        break
                    elif msg_type == "input" and mode == "interactive":
                        text = msg.get("text", "")
                        if text and self._on_input_inject:
                            try:
                                result = self._on_input_inject(text)
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception as e:
                                logger.debug(f"Input inject error: {e}")
            except (ConnectionError, BrokenPipeError, OSError):
                pass

        send_task = asyncio.create_task(_send_events())
        recv_task = asyncio.create_task(_recv_input())

        try:
            await asyncio.wait(
                [send_task, recv_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            # Always stop both child loops, including when this stream task is
            # cancelled externally (for example during server shutdown). A
            # normal detach cancels the still-running sender; cancellation of
            # this coroutine must do the same or the sender leaks indefinitely.
            for task in (send_task, recv_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)
            if self._display_tap is not None:
                self._display_tap.unsubscribe(client_id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(f"Attacher {client_id} disconnected")

    async def stop(self) -> None:
        """Stop the socket server."""
        listeners: list[asyncio.AbstractServer] = []
        if self._tcp_server:
            self._tcp_server.close()
            listeners.append(self._tcp_server)
            self._tcp_server = None
        if self._server:
            self._server.close()
            listeners.append(self._server)
            self._server = None

        # Closing a listening server leaves accepted connections alive. Close
        # their transports, give handlers a short chance to finish naturally,
        # then cancel anything still blocked before callers tear down the loop.
        writers = tuple(self._connection_writers)
        for writer in writers:
            writer.close()
        tasks = tuple(self._connection_tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=1.0)
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        self._connection_tasks.difference_update(tasks)
        self._connection_writers.difference_update(writers)

        for listener in listeners:
            try:
                await asyncio.wait_for(listener.wait_closed(), timeout=1.0)
            except Exception:
                pass

        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        logger.info("Agent socket stopped")


class AgentMessenger:
    """Send messages to other agents via their sockets."""

    @staticmethod
    async def do_client_handshake(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        identity_manager: Any,
        designation: str,
        timeout: float = 10.0,
    ) -> bool:
        """Client side of the Ed25519 challenge-response handshake.

        Called after opening a connection to a peer's socket server.
        Waits for auth_challenge, signs the nonce, sends auth_response.

        Returns True if authenticated, False if rejected.
        If no challenge arrives (peer doesn't require auth), returns True.
        """
        try:
            # Read server greeting — should be auth_challenge or a normal ack
            greeting_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug("client handshake: timeout waiting for server greeting")
            return False

        if not greeting_line:
            return False

        try:
            greeting = json.loads(greeting_line.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Not JSON — peer doesn't speak handshake protocol, allow
            return True

        gtype = greeting.get("type", "")

        # If this isn't a challenge, the server doesn't require auth
        if gtype != "auth_challenge":
            # It's a normal response (ack, pong, etc) — auth not required
            # Caller should handle this line as normal protocol
            return True

        nonce = greeting.get("nonce", "")
        if not nonce:
            logger.warning("client handshake: challenge missing nonce")
            return False

        # Sign the nonce with our private key
        nonce_bytes = nonce.encode("utf-8")
        try:
            signature = identity_manager.sign_message(designation, nonce_bytes)
        except (ValueError, Exception) as e:
            logger.warning(f"client handshake: signing failed: {e}")
            return False

        # Send auth_response
        auth_resp = (
            json.dumps(
                {
                    "type": "auth_response",
                    "designation": designation,
                    "signature": signature,
                }
            )
            + "\n"
        )
        writer.write(auth_resp.encode())
        await writer.drain()

        # Wait for auth_ok or auth_rejected
        try:
            result_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug("client handshake: timeout waiting for auth result")
            return False

        if not result_line:
            return False

        try:
            result = json.loads(result_line.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False

        if result.get("type") == "auth_ok":
            logger.debug(f"client handshake: authenticated as '{designation}'")
            return True

        # auth_rejected or anything else
        reason = result.get("reason", "unknown")
        logger.warning(f"client handshake: rejected for '{designation}': {reason}")
        return False

    @staticmethod
    async def _open(
        target: str,
        *,
        timeout: float,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ):
        """Open a connection to a peer (unix socket path OR endpoint URI).

        ``target`` is a unix socket path, or a remote endpoint URI
        (``wss://host:port`` / ``ws://`` / ``a2a://``). For remote targets —
        or whenever ``auth`` is supplied and the peer challenges — the
        Ed25519 client handshake runs first, leaving the stream positioned
        for normal protocol traffic.

        ``auth`` is ``{"identity_manager": IdentityManager,
        "designation": str, "require_auth"?: bool, "tls_ca"?: str}``. When
        ``auth`` is None and ``target`` is a unix path, behavior is identical
        to a plain ``open_unix_connection`` — existing local callers unchanged.

        Returns ``(reader, writer)``. Raises on connect/handshake failure.
        """
        from .dns.endpoint import (
            build_client_ssl_context,
            is_remote_uri,
            parse_endpoint_uri,
            uri_is_tls,
        )

        remote = is_remote_uri(target)
        if remote:
            parsed = parse_endpoint_uri(target)
            if parsed is None:
                raise ValueError(f"invalid endpoint URI: {target}")
            host, port = parsed
            ctx = ssl_ctx
            if ctx is None and uri_is_tls(target):
                ca = auth.get("tls_ca", "") if auth else ""
                ctx = build_client_ssl_context(ca)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ctx),
                timeout=timeout,
            )
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(target),
                timeout=timeout,
            )

        # Remote peers always challenge; local peers only when auth is forced.
        if auth is not None and (remote or auth.get("require_auth")):
            ok = await AgentMessenger.do_client_handshake(
                reader,
                writer,
                auth["identity_manager"],
                auth["designation"],
                timeout=timeout,
            )
            if not ok:
                writer.close()
                raise ConnectionError(f"handshake failed for {target}")

        return reader, writer

    @staticmethod
    async def send_to_agent(
        target_socket: str,
        message: HubMessage,
        timeout: float = 5.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> bool:
        """Send a message to an agent's socket or remote endpoint.

        ``target_socket`` may be a unix socket path or an endpoint URI.
        Pass ``auth`` to authenticate over a remote / auth-required peer.
        Returns True if delivered and acked.
        """
        writer = None
        try:
            reader, writer = await AgentMessenger._open(
                target_socket, timeout=timeout, auth=auth, ssl_ctx=ssl_ctx
            )

            msg_line = json.dumps(message.to_dict()) + "\n"
            writer.write(msg_line.encode())
            await writer.drain()

            # Wait for ack
            ack_line = await asyncio.wait_for(reader.readline(), timeout=timeout)

            if ack_line:
                ack = json.loads(ack_line.decode().strip())
                if isinstance(ack, dict):
                    return ack.get("type") == "ack"
            return False

        except Exception as e:
            logger.debug(f"Send to {target_socket} failed: {e}")
            return False
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    async def ping_agent(
        socket_path: str,
        timeout: float = 3.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> bool:
        """Ping an agent (unix socket or remote endpoint) to check liveness."""
        writer = None
        try:
            reader, writer = await AgentMessenger._open(
                socket_path, timeout=timeout, auth=auth, ssl_ctx=ssl_ctx
            )
            ping = json.dumps({"action": "ping"}) + "\n"
            writer.write(ping.encode())
            await writer.drain()

            resp_line = await asyncio.wait_for(reader.readline(), timeout=timeout)

            if resp_line:
                resp = json.loads(resp_line.decode().strip())
                if isinstance(resp, dict):
                    return resp.get("type") == "pong"
            return False
        except Exception:
            return False
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    async def request_context(
        socket_path: str,
        lines: int = 200,
        timeout: float = 5.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> str:
        """Request recent context from an agent (unix socket or endpoint)."""
        lines = _coerce_line_count(lines, 200)
        writer = None
        try:
            reader, writer = await AgentMessenger._open(
                socket_path, timeout=timeout, auth=auth, ssl_ctx=ssl_ctx
            )
            req = json.dumps({"action": "get_context", "lines": lines}) + "\n"
            writer.write(req.encode())
            await writer.drain()

            resp_line = await asyncio.wait_for(reader.readline(), timeout=timeout)

            if resp_line:
                resp = json.loads(resp_line.decode().strip())
                if isinstance(resp, dict):
                    content = resp.get("content", "")
                    return content if isinstance(content, str) else ""
            return ""
        except Exception:
            return ""
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    async def request_output(
        socket_path: str,
        lines: int = 100,
        timeout: float = 5.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> List[str]:
        """Request recent output lines from an agent (unix socket or endpoint)."""
        result, _error = await AgentMessenger.request_output_diagnostic(
            socket_path,
            lines=lines,
            timeout=timeout,
            auth=auth,
            ssl_ctx=ssl_ctx,
        )
        return result

    @staticmethod
    async def request_output_diagnostic(
        socket_path: str,
        lines: int = 100,
        timeout: float = 5.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> tuple[List[str], Optional[str]]:
        """Request output while distinguishing transport failure from empty output."""
        lines = _coerce_line_count(lines, 100)
        writer = None
        try:
            reader, writer = await AgentMessenger._open(
                socket_path, timeout=timeout, auth=auth, ssl_ctx=ssl_ctx
            )
            req = json.dumps({"action": "get_output", "lines": lines}) + "\n"
            writer.write(req.encode())
            await writer.drain()

            resp_line = await asyncio.wait_for(reader.readline(), timeout=timeout)

            if not resp_line:
                return [], "empty response"

            try:
                resp = json.loads(resp_line.decode().strip())
            except (UnicodeDecodeError, json.JSONDecodeError):
                return [], "invalid JSON response"

            if not isinstance(resp, dict):
                return [], "invalid response envelope"
            if resp.get("type") != "output":
                return [], f"unexpected response type: {resp.get('type')!r}"

            result = resp.get("lines", [])
            if not isinstance(result, list):
                return [], "output lines were not a list"
            return result, None
        except asyncio.TimeoutError:
            return [], f"timeout after {timeout:g}s"
        except Exception as exc:
            logger.debug("Output request failed for %s: %s", socket_path, exc)
            detail = str(exc).strip()
            if detail:
                return [], f"{type(exc).__name__}: {detail}"
            return [], type(exc).__name__
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    async def request_status(
        socket_path: str,
        timeout: float = 3.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> dict:
        """Request status from an agent (unix socket or remote endpoint)."""
        writer = None
        try:
            reader, writer = await AgentMessenger._open(
                socket_path, timeout=timeout, auth=auth, ssl_ctx=ssl_ctx
            )
            req = json.dumps({"action": "get_status"}) + "\n"
            writer.write(req.encode())
            await writer.drain()

            resp_line = await asyncio.wait_for(reader.readline(), timeout=timeout)

            if resp_line:
                resp = json.loads(resp_line.decode().strip())
                if isinstance(resp, dict) and resp.get("type") == "status":
                    return resp
            return {}
        except Exception:
            return {}
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    async def signal_shutdown(
        socket_path: str,
        reason: str = "",
        timeout: float = 3.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> bool:
        """Signal an agent to shut down gracefully (unix or remote). Returns True if acked."""
        writer = None
        try:
            reader, writer = await AgentMessenger._open(
                socket_path, timeout=timeout, auth=auth, ssl_ctx=ssl_ctx
            )
            req = json.dumps({"action": "shutdown", "reason": reason}) + "\n"
            writer.write(req.encode())
            await writer.drain()

            resp_line = await asyncio.wait_for(reader.readline(), timeout=timeout)

            if resp_line:
                resp = json.loads(resp_line.decode().strip())
                if isinstance(resp, dict):
                    return resp.get("type") == "ack"
            return False
        except Exception:
            return False
        finally:
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    async def subscribe(
        socket_path: str,
        timeout: float = 5.0,
        *,
        auth: Optional[Dict[str, Any]] = None,
        ssl_ctx: Any = None,
    ) -> dict:
        """Subscribe to an agent's output stream (unix or remote). Returns ack dict."""
        try:
            reader, writer = await AgentMessenger._open(
                socket_path, timeout=timeout, auth=auth, ssl_ctx=ssl_ctx
            )
            req = json.dumps({"action": "subscribe"}) + "\n"
            writer.write(req.encode())
            await writer.drain()

            resp_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            writer.close()
            await writer.wait_closed()

            if resp_line:
                resp = json.loads(resp_line.decode().strip())
                if isinstance(resp, dict):
                    return resp
            return {}
        except Exception:
            return {}

    @staticmethod
    async def send_to_file(target_agent_id: str, message: HubMessage) -> None:
        """Fallback: write message to agent's filesystem mailbox.

        After writing, prunes ordinary traffic to INBOX_MAX_SIZE entries
        (oldest first). Durable task controls are preserved until classified.
        """
        msg_dir = get_messages_dir() / target_agent_id
        msg_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Timestamp + UUID filename avoids seq race under concurrency
        filename = f"{time.time():.6f}-{uuid.uuid4().hex[:8]}-from-{message.from_identity}.json"

        _atomic_write(msg_dir / filename, message.to_dict())
        logger.info(f"Wrote message to mailbox: {filename}")

        # Enforce inbox size bound — evict oldest if over limit
        _prune_inbox(msg_dir, INBOX_MAX_SIZE)

    @staticmethod
    def read_mailbox(agent_id: str, max_replay: int = 0) -> List[HubMessage]:
        """Read and consume messages from filesystem mailbox.

        Args:
            agent_id: Agent identity or agent_id to read from.
            max_replay: If > 0, cap ordinary messages returned to this many
                (newest first). Durable task controls bypass the cap so normal
                receive can classify them exactly once. A synthetic
                ``inbox_summary`` reports dropped ordinary traffic. TTL also
                applies only to ordinary traffic; controls must be classified.
        """
        msg_dir = get_messages_dir() / agent_id
        if not msg_dir.exists():
            return []

        now = time.time()
        messages: List[HubMessage] = []
        expired_count = 0

        for f in sorted(msg_dir.glob("*.json")):
            data: Dict[str, Any] = {}
            try:
                with open(f) as fh:
                    data = json.load(fh)
            except Exception as e:
                logger.warning(f"Bad mailbox message {f}: {e}")
                try:
                    f.unlink()
                except Exception:
                    pass
                continue

            if not isinstance(data, dict):
                logger.warning(
                    "Bad mailbox message %s: expected JSON object, got %s",
                    f,
                    type(data).__name__,
                )
                try:
                    f.unlink()
                except Exception:
                    pass
                continue

            # TTL bounds ordinary chatter only. Durable task controls must
            # reach normal receive so stale cron reminders are explicitly
            # ACKed and assignments are classified instead of disappearing.
            ts = data.get("timestamp", 0) or 0
            if (
                not _is_durable_control(data)
                and ts
                and (now - float(ts)) > INBOX_TTL_SECS
            ):
                expired_count += 1
                try:
                    f.unlink()
                except Exception:
                    pass
                continue

            try:
                msg = HubMessage.from_dict(data)
                messages.append(msg)
            except Exception as e:
                logger.warning(f"Failed to parse mailbox message {f}: {e}")
                try:
                    f.unlink()
                except Exception:
                    pass
                continue

            # Delete after successful parse
            try:
                f.unlink()
            except Exception:
                pass

        if expired_count:
            logger.info(
                f"Discarded {expired_count} TTL-expired inbox message(s) for {agent_id}"
            )

        total = len(messages)
        controls = [
            message for message in messages if _is_durable_control(message.to_dict())
        ]
        ordinary = [message for message in messages if message not in controls]

        if max_replay > 0 and len(ordinary) > max_replay:
            replayed_ordinary = ordinary[-max_replay:]
            dropped = len(ordinary) - len(replayed_ordinary)
            replayed_ids = {message.id for message in controls + replayed_ordinary}
            replayed = [message for message in messages if message.id in replayed_ids]

            # Collect unique senders from the full set for the summary.
            senders: List[str] = list(
                dict.fromkeys(
                    m.from_identity or m.from_agent
                    for m in messages
                    if m.from_identity or m.from_agent
                )
            )
            sender_str = ", ".join(senders[:5])
            if len(senders) > 5:
                sender_str += f" and {len(senders) - 5} more"

            summary_content = (
                f"[offline inbox] {total} message(s) arrived while offline"
                f" (senders: {sender_str or 'unknown'}). "
                f"Showing most recent {max_replay} ordinary message(s) and "
                f"{len(controls)} preserved task control(s). "
                f"Older ordinary messages have been dropped to prevent flooding."
            )
            summary = HubMessage(
                action="inbox_summary",
                from_identity="hub",
                content=summary_content,
                # timestamp=0 ensures this summary sorts before all real
                # messages when read_mailboxes does its chronological sort.
                timestamp=0.0,
                metadata={
                    "total": total,
                    "showing": len(replayed_ordinary),
                    "controls_preserved": len(controls),
                    "senders": senders,
                    "dropped": dropped,
                },
            )
            logger.info(
                "Inbox replay bounded for %s: %d total, replaying %d ordinary "
                "and preserving %d control(s)",
                agent_id,
                total,
                len(replayed_ordinary),
                len(controls),
            )
            return [summary] + replayed

        return messages

    @staticmethod
    def read_mailboxes(agent_keys: List[str], max_replay: int = 0) -> List[HubMessage]:
        """Read and consume mailboxes for all durable addresses.

        Agents get a fresh agent_id on restart, but their hub identity
        stays stable. Reading both prevents offline messages from being
        stranded in the old session's mailbox.

        Args:
            agent_keys: List of agent_id and identity strings to drain.
            max_replay: Forwarded to read_mailbox — caps per-inbox replay.
        """
        messages: List[HubMessage] = []
        seen: set[str] = set()
        for key in dict.fromkeys(k for k in agent_keys if k):
            for msg in AgentMessenger.read_mailbox(key, max_replay=max_replay):
                if msg.id in seen:
                    continue
                seen.add(msg.id)
                messages.append(msg)
        return sorted(messages, key=lambda msg: msg.timestamp)

    @staticmethod
    def get_inbox_count(identity: str) -> int:
        """Return the number of pending messages in an identity's inbox.

        Does NOT consume (delete) the messages.  Safe to call at any time.
        """
        msg_dir = get_messages_dir() / identity
        if not msg_dir.exists():
            return 0
        try:
            return sum(1 for _ in msg_dir.glob("*.json"))
        except Exception:
            return 0

    @staticmethod
    def get_all_inbox_counts() -> Dict[str, int]:
        """Return {identity: count} for every non-empty offline inbox.

        Used by the coordinator to surface "agent X has N pending" in
        the hub status view.  Non-destructive (does not consume messages).
        """
        messages_dir = get_messages_dir()
        if not messages_dir.exists():
            return {}
        counts: Dict[str, int] = {}
        try:
            for d in messages_dir.iterdir():
                if not d.is_dir():
                    continue
                count = sum(1 for _ in d.glob("*.json"))
                if count > 0:
                    counts[d.name] = count
        except Exception:
            pass
        return counts
