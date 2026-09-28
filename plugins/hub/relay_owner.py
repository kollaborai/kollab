"""Single workspace relay owner and bounded same-user Hub RPC forwarding."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any

from .relay_state import RelayError

OWNER_RECORD_LIMIT = 2048
RPC_REQUEST_LIMIT = 65536
RPC_RESPONSE_LIMIT = 262144
RELAY_METHODS = frozenset(
    {
        "relay.command",
        "relay.send",
        "relay.directory",
        "relay.deliver",
        "relay.event",
        "relay.cancel",
        "relay.status",
        "relay.contact_submit",
        "relay.contact_pending",
        "relay.contact_decide",
    }
)


class RelayOwnerError(RelayError):
    """Fixed, operator-safe ownership or local forwarding failure."""


def _private_regular(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) & 0o077 == 0
        and info.st_nlink == 1
    )


def _json_object(raw: bytes, limit: int) -> dict:
    if len(raw) > limit:
        raise RelayOwnerError("relay local response exceeds the size limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("constant")

    try:
        value = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=invalid_constant
        )
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise RelayOwnerError("relay local response is invalid") from None


def _socket_path(value: str) -> Path:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\x00" in value
        or len(os.fsencode(value)) > 103
    ):
        raise RelayOwnerError("relay owner requires an absolute Unix socket path")
    return Path(value)


class WorkspaceRelayOwner:
    """A flock owns the relay; the private record only locates its Hub socket.

    Every contender retains the same lock inode. A stale PID/record never grants
    ownership and can safely be replaced by the next successful lock holder.
    """

    def __init__(self, workspace: Path, state_dir: Path | None = None):
        digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
        self.state_dir = state_dir or Path.home() / ".kollab" / "network" / digest
        self._fd: int | None = None
        self._claim = ""
        self._record: dict | None = None
        self._pid = os.getpid()
        try:
            self.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            info = self.state_dir.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise RelayOwnerError(
                    "relay owner directory must be private and owned by this user"
                )
            # Pin the checked directory for all lock/record operations. O_NOFOLLOW
            # rejects a symlink substituted between lstat and open.
            self._dir_fd = os.open(
                self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            opened = os.fstat(self._dir_fd)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                os.close(self._dir_fd)
                del self._dir_fd
                raise RelayOwnerError("relay owner directory changed while opening")
        except OSError:
            raise RelayOwnerError("relay owner directory is unavailable") from None

    def _open_lock(self) -> int:
        fd = os.open(
            "relay-owner.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=self._dir_fd,
        )
        if not _private_regular(os.fstat(fd)):
            os.close(fd)
            raise RelayOwnerError("relay owner lock must be a private regular file")
        return fd

    def _read_record(self) -> dict | None:
        try:
            fd = os.open(
                "relay-owner.json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self._dir_fd,
            )
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            if not _private_regular(os.fstat(stream.fileno())):
                raise RelayOwnerError(
                    "relay owner record must be a private regular file"
                )
            data = _json_object(stream.read(OWNER_RECORD_LIMIT + 1), OWNER_RECORD_LIMIT)
        if set(data) != {"socket_path", "agent_id", "pid", "claim"}:
            raise RelayOwnerError("relay owner record is invalid")
        _socket_path(data["socket_path"])
        if (
            not isinstance(data["agent_id"], str)
            or not 1 <= len(data["agent_id"]) <= 256
            or type(data["pid"]) is not int
            or data["pid"] <= 0
            or not isinstance(data["claim"], str)
            or len(data["claim"]) != 32
        ):
            raise RelayOwnerError("relay owner record is invalid")
        return data

    def _write_record(self, record: dict) -> None:
        # Reject unsafe prior records, rather than overwriting a symlink or an
        # object owned by another user. A valid stale record may be replaced.
        try:
            prior = os.stat(
                "relay-owner.json", dir_fd=self._dir_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            prior = None
        if prior is not None and not _private_regular(prior):
            raise RelayOwnerError("relay owner record must be a private regular file")
        raw = (
            json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode()
            + b"\n"
        )
        if len(raw) > OWNER_RECORD_LIMIT:
            raise RelayOwnerError("relay owner record exceeds the size limit")
        name = ".relay-owner-" + secrets.token_hex(12)
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._dir_fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(
                name,
                "relay-owner.json",
                src_dir_fd=self._dir_fd,
                dst_dir_fd=self._dir_fd,
            )
            os.fsync(self._dir_fd)
        finally:
            try:
                os.unlink(name, dir_fd=self._dir_fd)
            except FileNotFoundError:
                pass

    def acquire(self, socket_path: str, agent_id: str) -> bool:
        _socket_path(socket_path)
        if not isinstance(agent_id, str) or not 1 <= len(agent_id) <= 256:
            raise RelayOwnerError("relay owner agent identity is invalid")
        if self._pid != os.getpid():
            raise RelayOwnerError("relay owner cannot be reused after process fork")
        if self._fd is not None:
            if (
                self._record
                and self._record["socket_path"] == socket_path
                and self._record["agent_id"] == agent_id
            ):
                return True
            raise RelayOwnerError("relay owner already holds a different identity")
        fd = None
        try:
            fd = self._open_lock()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return False
            claim = secrets.token_hex(16)
            record = {
                "socket_path": socket_path,
                "agent_id": agent_id,
                "pid": os.getpid(),
                "claim": claim,
            }
            self._write_record(record)
            self._fd, self._claim, self._record = fd, claim, record
            return True
        except (OSError, RelayOwnerError):
            if fd is not None:
                os.close(fd)
            raise RelayOwnerError(
                "relay owner could not acquire private workspace state"
            ) from None

    def owner(self) -> dict | None:
        if self._fd is not None and self._pid == os.getpid():
            return {
                key: self._record[key] for key in ("socket_path", "agent_id", "pid")
            }
        fd = None
        try:
            fd = self._open_lock()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                record = self._read_record()
                return (
                    {key: record[key] for key in ("socket_path", "agent_id", "pid")}
                    if record
                    else None
                )
            # The kernel let us take the lock, so any recorded owner is stale.
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        except OSError:
            raise RelayOwnerError("relay owner state is unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if self._pid == os.getpid():
                try:
                    record = self._read_record()
                    if record and record.get("claim") == self._claim:
                        os.unlink("relay-owner.json", dir_fd=self._dir_fd)
                        os.fsync(self._dir_fd)
                except (OSError, RelayOwnerError):
                    pass
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            # An inherited child closes its copy without unlocking the shared
            # open-file description or removing its parent's owner record.
            os.close(fd)
            self._record, self._claim = None, ""

    def __del__(self):
        try:
            self.release()
            if hasattr(self, "_dir_fd"):
                os.close(self._dir_fd)
        except (OSError, AttributeError):
            pass


async def local_relay_rpc(
    socket_path: str,
    method: str,
    params: dict,
    timeout: float = 15,
    *,
    auth: dict[str, Any] | None = None,
) -> dict:
    """Call a whitelisted relay method on a same-user Unix Hub socket.

    For Hub local Ed25519 authentication, pass the normal messenger auth shape:
    {"identity_manager": manager, "designation": name, "require_auth": True}.
    Authentication is strict; missing credentials never downgrade the server.
    Exceptions are fixed strings and never include request data or remote errors.
    """
    from kollabor_rpc.models import RpcRequest, new_request_id

    from .messenger import AgentSocketServer

    path = _socket_path(socket_path)
    if (
        not isinstance(method, str)
        or method not in RELAY_METHODS
        or not isinstance(params, dict)
    ):
        raise RelayOwnerError("relay local RPC method or parameters are invalid")
    if auth is not None and not isinstance(auth, dict):
        raise RelayOwnerError("relay local RPC authentication parameters are invalid")
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= 60
    ):
        raise RelayOwnerError("relay local RPC timeout is invalid")
    request = RpcRequest(new_request_id(), method, params, timeout)
    try:
        raw = (
            json.dumps(
                request.to_wire(), separators=(",", ":"), allow_nan=False
            ).encode()
            + b"\n"
        )
    except (ValueError, TypeError, RecursionError):
        raise RelayOwnerError("relay local RPC parameters are invalid") from None
    if len(raw) > RPC_REQUEST_LIMIT:
        raise RelayOwnerError("relay local RPC request exceeds the size limit")

    writer = None
    try:
        info = path.lstat()
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise RelayOwnerError(
                "relay local RPC requires a private socket owned by this user"
            )
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_unix_connection(
                str(path), limit=RPC_RESPONSE_LIMIT
            )
            connected_info = path.lstat()
            if (connected_info.st_dev, connected_info.st_ino) != (
                info.st_dev,
                info.st_ino,
            ):
                raise RelayOwnerError("relay local RPC socket changed while connecting")
            credentials = AgentSocketServer._get_peer_credentials(writer)
            if credentials is None and sys.platform in {"linux", "darwin"}:
                raise RelayOwnerError(
                    "relay local RPC peer credentials could not be verified"
                )
            if credentials is not None and credentials[1] != os.getuid():
                raise RelayOwnerError(
                    "relay local RPC peer has a different user identity"
                )

            async def read() -> dict:
                line = await reader.readline()
                if not line or not line.endswith(b"\n"):
                    raise RelayOwnerError("relay local RPC response is incomplete")
                return _json_object(line, RPC_RESPONSE_LIMIT)

            if auth and auth.get("require_auth"):
                challenge = await read()
                nonce = challenge.get("nonce")
                designation = auth.get("designation")
                if (
                    challenge.get("type") != "auth_challenge"
                    or not isinstance(nonce, str)
                    or not 1 <= len(nonce) <= 512
                ):
                    raise RelayOwnerError("relay local RPC authentication failed")
                if not isinstance(designation, str) or not 1 <= len(designation) <= 256:
                    raise RelayOwnerError("relay local RPC authentication failed")
                try:
                    signature = auth["identity_manager"].sign_message(
                        designation, nonce.encode()
                    )
                    response = (
                        json.dumps(
                            {
                                "type": "auth_response",
                                "designation": designation,
                                "signature": signature,
                            }
                        ).encode()
                        + b"\n"
                    )
                except Exception:
                    raise RelayOwnerError(
                        "relay local RPC authentication failed"
                    ) from None
                if len(response) > RPC_REQUEST_LIMIT:
                    raise RelayOwnerError("relay local RPC authentication failed")
                writer.write(response)
                await writer.drain()
                accepted = await read()
                if (
                    accepted.get("type") != "auth_ok"
                    or accepted.get("designation") != designation
                ):
                    raise RelayOwnerError("relay local RPC authentication failed")

            writer.write(raw)
            await writer.drain()
            reply = await read()
            if reply.get("type") in {"auth_challenge", "auth_rejected"}:
                raise RelayOwnerError(
                    "relay local RPC authentication required or rejected"
                )
            if (
                reply.get("action") != "rpc_reply"
                or reply.get("request_id") != request.request_id
            ):
                raise RelayOwnerError(
                    "relay local RPC response does not match the request"
                )
            if reply.get("error") is not None or reply.get("error_kind") is not None:
                raise RelayOwnerError("relay local RPC handler rejected the request")
            if not isinstance(reply.get("result"), dict):
                raise RelayOwnerError("relay local RPC result is invalid")
            return reply["result"]
    except RelayOwnerError:
        raise
    except TimeoutError:
        raise RelayOwnerError("relay local RPC timed out") from None
    except (OSError, ValueError, EOFError):
        raise RelayOwnerError("relay local RPC connection failed") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (OSError, TimeoutError):
                pass
