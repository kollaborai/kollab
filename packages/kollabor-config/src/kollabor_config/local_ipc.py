"""Local IPC endpoints: unix sockets on POSIX, loopback TCP with an HMAC handshake on Windows (kollab #268)."""

import asyncio
import contextlib
import hashlib
import hmac
import inspect
import json
import logging
import os
import secrets
import socket
import struct
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NONCE_BYTES = 32
_HANDSHAKE_SECONDS = 5
_CLIENT_LABEL = b"kollab-ipc/client"
_SERVER_LABEL = b"kollab-ipc/server"

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)  # local_ipc only

    ERROR_INSUFFICIENT_BUFFER = 122
    AF_INET_ = 2
    TCP_TABLE_OWNER_PID_CONNECTIONS = 4

    class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    _iphlpapi.GetExtendedTcpTable.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    ]
    _iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD


class Endpoint:
    """A running server: its asyncio server, and on Windows the endpoint file that points clients at it."""

    def __init__(
        self, server: asyncio.AbstractServer, path: Path, port: int = 0, pid: int = 0
    ) -> None:
        self.server = server
        self._path = path
        self._port = port
        self._pid = pid

    def close(self) -> None:
        self.server.close()
        if os.name == "nt":
            _remove_endpoint_if_ours(self._path, self._port, self._pid)


async def serve(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any],
    endpoint: str | os.PathLike[str],
    **kw: Any,
) -> Endpoint:
    """Serve ``handler`` on the endpoint: a unix socket on POSIX, a loopback port on Windows."""
    path = Path(endpoint)
    if os.name != "nt":
        server = await asyncio.start_unix_server(handler, path=str(path), **kw)
        return Endpoint(server, path)

    from . import private_fs

    private_fs.ensure_private_dir(path.parent)
    key = secrets.token_bytes(32)

    async def wrapped(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await asyncio.wait_for(
                _server_handshake(reader, writer, key), _HANDSHAKE_SECONDS
            )
        except (
            OSError,
            EOFError,
        ):  # a refused or silent peer; asyncio.TimeoutError is an OSError
            logger.debug("local_ipc: refused a peer that failed the handshake")
            writer.close()
            return
        result = handler(reader, writer)
        if inspect.isawaitable(result):
            await result

    server = await asyncio.start_server(
        wrapped, "127.0.0.1", 0, reuse_address=False, **kw
    )
    try:
        port = server.sockets[0].getsockname()[1]
        pid = os.getpid()
        _write_endpoint(path, {"port": port, "key": key.hex(), "pid": pid})
    except BaseException:
        server.close()
        raise
    return Endpoint(server, path, port, pid)


async def connect(
    endpoint: str | os.PathLike[str], **kw: Any
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to an endpoint made by ``serve``."""
    path = Path(endpoint)
    if os.name != "nt":
        return await asyncio.open_unix_connection(str(path), **kw)

    port, key = _checked_endpoint(path)
    reader, writer = await asyncio.open_connection("127.0.0.1", port, **kw)
    try:
        await asyncio.wait_for(
            _client_handshake(reader, writer, key), _HANDSHAKE_SECONDS
        )
    except (OSError, EOFError) as exc:
        writer.close()
        raise ConnectionRefusedError(f"local IPC handshake failed for {path}") from exc
    return reader, writer


def connect_blocking(endpoint: str | os.PathLike[str], timeout: float) -> socket.socket:
    """Connect to an endpoint made by ``serve``, from a blocking client."""
    path = Path(endpoint)
    if os.name != "nt":
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(str(path))
        except BaseException:
            sock.close()
            raise
        return sock

    port, key = _checked_endpoint(path)
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        _client_handshake_blocking(sock, key)
    except OSError as exc:
        sock.close()
        raise ConnectionRefusedError(f"local IPC handshake failed for {path}") from exc
    return sock


def peer_pid(writer_or_sock: Any) -> int:
    """The pid on the other end (LOCAL_PEERPID / SO_PEERCRED), 0 when it cannot be read.

    On Windows, the pid that owns the client's end of our loopback connection, 0 when no row matches.
    """
    sock = _socket_of(writer_or_sock)
    if os.name == "nt":
        return _tcp_owner_pid(sock)
    if sock is None or getattr(sock, "family", None) != socket.AF_UNIX:
        return 0
    try:
        if sys.platform == "darwin":
            return int(sock.getsockopt(0, 0x002))  # SOL_LOCAL, LOCAL_PEERPID
        return int(
            struct.unpack(
                "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )[0]
        )
    except OSError, ValueError, struct.error:
        return 0


def peer_is_me(writer_or_sock: Any) -> bool:
    """True when the peer runs as our uid. On Windows, True: the handshake already proved the peer."""
    if os.name == "nt":
        return True
    sock = _socket_of(writer_or_sock)
    if sock is None:
        return False
    try:
        if sys.platform == "darwin":
            # SOL_LOCAL, LOCAL_PEERCRED: struct xucred starts with cr_version, then cr_uid
            uid = struct.unpack("2I", sock.getsockopt(0, 0x001, 256)[:8])[1]
        else:
            uid = struct.unpack(
                "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )[1]
    except OSError, ValueError, struct.error:
        return False
    return uid == os.getuid()


def _socket_of(writer_or_sock: Any) -> Any:
    """The socket under a StreamWriter, or the socket itself."""
    get_extra_info = getattr(writer_or_sock, "get_extra_info", None)
    return get_extra_info("socket") if get_extra_info else writer_or_sock


def _mac(key: bytes, label: bytes, first: bytes, second: bytes) -> bytes:
    return hmac.new(key, label + first + second, hashlib.sha256).digest()


async def _server_handshake(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, key: bytes
) -> None:
    n_s = secrets.token_bytes(_NONCE_BYTES)
    writer.write(n_s)
    await writer.drain()
    n_c = await reader.readexactly(_NONCE_BYTES)
    proof = await reader.readexactly(_NONCE_BYTES)
    if not hmac.compare_digest(proof, _mac(key, _CLIENT_LABEL, n_s, n_c)):
        raise ConnectionRefusedError("client failed the local IPC handshake")
    writer.write(_mac(key, _SERVER_LABEL, n_c, n_s))
    await writer.drain()


async def _client_handshake(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, key: bytes
) -> None:
    n_s = await reader.readexactly(_NONCE_BYTES)
    n_c = secrets.token_bytes(_NONCE_BYTES)
    writer.write(n_c + _mac(key, _CLIENT_LABEL, n_s, n_c))
    await writer.drain()
    proof = await reader.readexactly(_NONCE_BYTES)
    if not hmac.compare_digest(proof, _mac(key, _SERVER_LABEL, n_c, n_s)):
        raise ConnectionRefusedError("server failed the local IPC handshake")


def _client_handshake_blocking(sock: socket.socket, key: bytes) -> None:
    n_s = _recv_exact(sock, _NONCE_BYTES)
    n_c = secrets.token_bytes(_NONCE_BYTES)
    sock.sendall(n_c + _mac(key, _CLIENT_LABEL, n_s, n_c))
    proof = _recv_exact(sock, _NONCE_BYTES)
    if not hmac.compare_digest(proof, _mac(key, _SERVER_LABEL, n_c, n_s)):
        raise ConnectionRefusedError("server failed the local IPC handshake")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionResetError("peer closed during the local IPC handshake")
        data += chunk
    return bytes(data)


def _checked_endpoint(path: Path) -> tuple[int, bytes]:
    """Windows: the port and key from the endpoint file, once its folder and file pass the checks."""
    from . import private_fs, proc_ctl

    private_fs.check_private_dir(path.parent)
    if not private_fs.owned_private(path, mask=0o077):
        raise ConnectionRefusedError(f"endpoint file {path} is not private")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        port = int(record["port"])
        key = bytes.fromhex(record["key"])
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectionRefusedError(f"endpoint file {path} is unreadable") from exc
    if len(key) != _NONCE_BYTES:
        raise ConnectionRefusedError(f"endpoint file {path} has a bad key")
    if not proc_ctl.pid_alive(pid):
        raise ConnectionRefusedError(f"endpoint file {path} names a dead server")
    return port, key


def _write_endpoint(path: Path, record: dict[str, Any]) -> None:
    """Windows: write the endpoint file to a temporary name, then replace the endpoint with it."""
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOINHERIT | os.O_BINARY, 0o600
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _remove_endpoint_if_ours(path: Path, port: int, pid: int) -> None:
    """Remove the endpoint file only while it still names this server, so a newer server's file survives."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return
    if (
        isinstance(record, dict)
        and record.get("port") == port
        and record.get("pid") == pid
    ):
        with contextlib.suppress(OSError):
            path.unlink()


def _tcp_owner_pid(sock: Any) -> int:
    """Windows: the owner of the client's TCP row, matched by the client's local port and our port."""
    if sock is None:
        return 0
    try:
        client_port = sock.getpeername()[1]
        our_port = sock.getsockname()[1]
    except OSError:
        return 0
    for local_port, remote_port, pid in _tcp_connections():
        if local_port == client_port and remote_port == our_port:
            return pid
    return 0


def _tcp_connections() -> list[tuple[int, int, int]]:
    """Windows: (local port, remote port, owning pid) for every IPv4 TCP connection."""
    size = wintypes.DWORD(0)
    _iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), False, AF_INET_, TCP_TABLE_OWNER_PID_CONNECTIONS, 0
    )
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        rc = _iphlpapi.GetExtendedTcpTable(
            buf, ctypes.byref(size), False, AF_INET_, TCP_TABLE_OWNER_PID_CONNECTIONS, 0
        )
        if rc != ERROR_INSUFFICIENT_BUFFER:
            break
    if rc != 0:
        raise OSError(rc, "GetExtendedTcpTable failed")
    count = wintypes.DWORD.from_buffer(buf, 0).value
    rows = (_MIB_TCPROW_OWNER_PID * count).from_buffer(buf, 4)
    return [
        (
            socket.ntohs(row.dwLocalPort & 0xFFFF),
            socket.ntohs(row.dwRemotePort & 0xFFFF),
            row.dwOwningPid,
        )
        for row in rows
    ]
