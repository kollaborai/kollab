"""Open an agent on another device of this network as if it ran here.

``kollab --attach lapis@devbox`` forwards an agent's socket over SSH
(kollabor/attach_remote.py). This forwards it over the network's sealed device
channel instead, so the web UI opens an agent on another computer the way it
opens one on this computer: its history, its live turns, and input.

The device that runs the agent decides who may open it (``RelayAgentBridge.
attach_target``): only a member device a person on it allowed with
``/connect attach allow <device>``, never under ``trust manual``, and under
``trust agents`` only the agents that device may message. An open channel is
the same control as sitting at that device's keyboard, so the device asks its
rules again while the channel stays open and closes it once they say no.

Wire: secure application requests (``SecureConversationTransport.request``),
sent by both sides:

  attach_open   {channel, agent_id, name}  requester -> the device running it
  attach_data   {channel, seq, data}       either way; data = base64(zlib(bytes))
  attach_close  {channel}                  either way

Each side writes what the other sends into its own local socket: the requester
into a one-shot private unix socket its caller connects to, the device into
the agent's own socket. Requests to one peer are serialized by the transport,
and each side's pump is the only sender on its channel, so each direction
stays in order; ``seq`` makes a retried send harmless.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import secrets
import shutil
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .device_names import NAME_RE
from .relay_conversations import AGENT_ID
from .relay_state import ID, RelayError

logger = logging.getLogger(__name__)

ATTACH_OPEN = "attach_open"
ATTACH_DATA = "attach_data"
ATTACH_CLOSE = "attach_close"
ATTACH_METHODS = frozenset({ATTACH_OPEN, ATTACH_DATA, ATTACH_CLOSE})

MAX_CHANNELS = 16
MAX_CHANNELS_PER_PEER = 4
# Raw bytes per data request. Compressed and base64'd it stays well under the
# 64 KiB secure message bound even when the bytes do not compress.
READ_BYTES = 24 * 1024
MAX_DATA_BYTES = 256 * 1024  # decompressed bound for one request
FLUSH_SECONDS = 0.2  # coalesce a stream of small writes into one request
IDLE_SECONDS = 5.0  # an idle channel wakes this often: the device asks its rules again
RECHECK_SECONDS = 1.0  # with traffic, the device asks its rules at most this often
KEEPALIVE_SECONDS = 30.0  # an idle channel proves its peer still holds it
CONNECT_SECONDS = 15.0  # for the caller to connect the one-shot socket
REQUEST_SECONDS = 30.0
CLOSE_SECONDS = 5.0
WRITE_SECONDS = 30.0
# The relay allows a client 10 frames a second (burst 20) and the client keeps
# itself to 8 for all its traffic. A secure request costs a frame per 4 KiB
# chunk each way: this device's own sends, and its replies to the peer's.
# Tunnels take 6 of the 8.
PACE_RATE = 6.0
PACE_BURST = 12.0
CHUNK_BYTES = 4 * 1024


class AttachRefused(Exception):
    """The plain reason a device gives for not opening one of its agents."""


class _PeerClosed(Exception):
    pass


@dataclass(eq=False)
class _Channel:
    id: str
    peer: str
    name: str
    requester: bool
    agent_id: str = ""
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    server: asyncio.AbstractServer | None = None
    path: Path | None = None
    send_seq: int = 0
    recv_seq: int = 0
    checked: float = 0.0
    pump: asyncio.Task | None = None
    closed: bool = False


class _Pacer:
    """Keeps this device's tunnel frames under the relay's per-client budget."""

    def __init__(self, rate: float = PACE_RATE, burst: float = PACE_BURST):
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.stamp = time.monotonic()
        self.lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now

    async def take(self, cost: float) -> None:
        cost = min(cost, self.burst)
        async with self.lock:
            while True:
                self._refill()
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                await asyncio.sleep((cost - self.tokens) / self.rate)

    def charge(self, cost: float) -> None:
        """Frames already sent without asking (replies to the peer's data)."""
        self._refill()
        self.tokens = max(-self.burst, self.tokens - cost)


def _frames(size: int) -> int:
    return max(1, -(-(size + 256) // CHUNK_BYTES))


def _pack(data: bytes) -> str:
    return base64.b64encode(zlib.compress(data, 6)).decode("ascii") if data else ""


def _unpack(text: object) -> bytes:
    if not isinstance(text, str):
        raise RelayError("invalid attach data")
    if not text:
        return b""
    try:
        packed = base64.b64decode(text.encode("ascii"), validate=True)
        inflater = zlib.decompressobj()
        data = inflater.decompress(packed, MAX_DATA_BYTES)
        if inflater.unconsumed_tail or not inflater.eof:
            raise ValueError("attach data exceeds its bound")
    except (ValueError, zlib.error, UnicodeError):
        raise RelayError("invalid attach data") from None
    return data


Request = Callable[..., Awaitable[dict]]
Target = Callable[[str, str, str], Awaitable[str]]


class NetworkAttach:
    """Both ends of the attach channels of one device's network owner.

    ``request(peer, method, payload, timeout=)`` sends a secure application
    request. ``target(peer, agent_id, name)`` returns the socket of a local
    agent ``peer`` may open, or raises AttachRefused. ``notice(text)`` puts one
    line in front of the person on this device.
    """

    def __init__(
        self,
        *,
        request: Request,
        target: Target,
        peer_name: Callable[[str], str],
        notice: Callable[[str], None],
    ):
        self._request_fn = request
        self._target = target
        self._peer_name = peer_name
        self._notice = notice
        self._channels: dict[tuple[str, str], _Channel] = {}
        self._pacer = _Pacer()
        self._socket_dir: Path | None = None
        self._closed = False

    # -- requester ------------------------------------------------------

    async def open(self, peer: str, agent_id: str, name: str) -> str:
        """Open ``name`` on ``peer``; return a one-shot socket path to connect to."""
        if self._closed:
            raise RelayError("network attach is closed")
        if len(self._channels) >= MAX_CHANNELS:
            raise RelayError("too many open agents on other computers; close one first")
        channel = _Channel(id=secrets.token_hex(16), peer=peer, name=name, requester=True)
        response = await self._send(
            peer, ATTACH_OPEN, {"channel": channel.id, "agent_id": agent_id, "name": name}
        )
        refused = response.get("refused")
        if isinstance(refused, str) and refused:
            raise AttachRefused(refused[:500])
        if response.get("open") is not True:
            raise RelayError(f"{self._peer_name(peer)} did not open {name}")
        self._channels[(peer, channel.id)] = channel
        try:
            channel.path = self._socket_path(channel.id)
            channel.server = await asyncio.start_unix_server(
                lambda reader, writer: self._connected(channel, reader, writer),
                path=str(channel.path),
            )
            os.chmod(channel.path, 0o600)
        except BaseException:
            await self._close(channel, tell_peer=True)
            raise
        asyncio.get_running_loop().call_later(
            CONNECT_SECONDS, self._expire_unconnected, channel
        )
        return str(channel.path)

    def _socket_path(self, channel_id: str) -> Path:
        if self._socket_dir is None:
            # Short on purpose: a unix socket path is capped near 104 bytes.
            self._socket_dir = Path(tempfile.mkdtemp(prefix="kollab-at-"))
            os.chmod(self._socket_dir, 0o700)
        return self._socket_dir / f"{channel_id[:16]}.sock"

    async def _connected(self, channel, reader, writer) -> None:
        if channel.closed or channel.writer is not None:
            writer.close()
            return
        channel.reader, channel.writer = reader, writer
        self._retire_socket(channel)
        channel.pump = asyncio.create_task(self._pump(channel))

    def _expire_unconnected(self, channel: _Channel) -> None:
        if not channel.closed and channel.writer is None:
            asyncio.create_task(self._close(channel, tell_peer=True))

    def _retire_socket(self, channel: _Channel) -> None:
        if channel.server is not None:
            channel.server.close()
            channel.server = None
        if channel.path is not None:
            with contextlib.suppress(OSError):
                channel.path.unlink()
            channel.path = None

    # -- the device running the agent -------------------------------------

    async def receive(self, peer: str, method: str, payload: object) -> dict:
        if not isinstance(payload, dict) or not ID.fullmatch(str(payload.get("channel", ""))):
            raise RelayError("invalid attach request")
        channel_id = payload["channel"]
        if method == ATTACH_OPEN:
            return await self._accept(peer, channel_id, payload)
        channel = self._channels.get((peer, channel_id))
        if method == ATTACH_CLOSE:
            if channel is not None:
                await self._close(channel, tell_peer=False)
            return {"closed": True}
        if method != ATTACH_DATA:
            raise RelayError("unsupported attach operation")
        if channel is None or channel.closed:
            return {"closed": True}
        seq = payload.get("seq")
        if type(seq) is not int or seq < 0:
            raise RelayError("invalid attach request")
        # This device answered every chunk of the request with a frame of its own.
        self._pacer.charge(_frames(len(str(payload.get("data") or ""))))
        if seq < channel.recv_seq:
            return {"ack": seq}  # a retried send that already landed
        if seq != channel.recv_seq:
            await self._close(channel, tell_peer=False, why=self._lost(channel))
            return {"closed": True}
        if not await self._allowed(channel):
            await self._close(channel, tell_peer=False, why=self._withdrawn(channel))
            return {"closed": True}
        data = _unpack(payload.get("data"))
        if data:
            if channel.writer is None:
                await self._close(channel, tell_peer=False)
                return {"closed": True}
            try:
                channel.writer.write(data)
                await asyncio.wait_for(channel.writer.drain(), WRITE_SECONDS)
            except (OSError, asyncio.TimeoutError):
                await self._close(channel, tell_peer=False, why=self._stopped(channel))
                return {"closed": True}
        channel.recv_seq += 1
        return {"ack": seq}

    async def _accept(self, peer: str, channel_id: str, payload: dict) -> dict:
        agent_id, name = payload.get("agent_id"), payload.get("name")
        if not isinstance(agent_id, str) or not AGENT_ID.fullmatch(agent_id):
            raise RelayError("invalid attach request")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise RelayError("invalid attach request")
        if (peer, channel_id) in self._channels:
            raise RelayError("attach channel already open")
        if self._closed:
            return {"refused": "this computer is shutting down its network"}
        # Only the agents that device holds open here count against its share.
        held = sum(1 for (key, _), ch in self._channels.items() if key == peer and not ch.requester)
        if len(self._channels) >= MAX_CHANNELS or held >= MAX_CHANNELS_PER_PEER:
            return {"refused": "too many of this computer's agents are open from there"}
        try:
            socket_path = await self._target(peer, agent_id, name)
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path, limit=4 * READ_BYTES), 10.0
            )
        except AttachRefused as refusal:
            return {"refused": str(refusal)}
        except (OSError, asyncio.TimeoutError):
            return {"refused": f"{name} is not running here any more"}
        channel = _Channel(
            id=channel_id,
            peer=peer,
            name=name,
            requester=False,
            agent_id=agent_id,
            reader=reader,
            writer=writer,
            checked=time.monotonic(),
        )
        self._channels[(peer, channel_id)] = channel
        channel.pump = asyncio.create_task(self._pump(channel))
        self._notice(f"{self._peer_name(peer)} opened {name} from the network")
        return {"open": True}

    async def _allowed(self, channel: _Channel) -> bool:
        """The device side asks its rules again (trust, grants, membership change)."""
        if channel.requester:
            return True
        now = time.monotonic()
        if now - channel.checked < RECHECK_SECONDS:
            return True
        channel.checked = now
        try:
            await self._target(channel.peer, channel.agent_id, channel.name)
        except Exception as exc:  # AttachRefused, or rules that cannot be read: closed either way
            logger.info("network attach %s for %s withdrawn: %s", channel.name, channel.peer[:12], exc)
            return False
        return True

    # -- both ends ------------------------------------------------------

    async def _pump(self, channel: _Channel) -> None:
        """Carry what this side's local socket says to the peer.

        The pump is the channel's only sender, so its seq never races. An idle
        channel wakes every IDLE_SECONDS for the device to ask its rules again,
        and after KEEPALIVE_SECONDS of quiet sends an empty frame, so a vanished
        peer is noticed. One read is in flight at a time and a timeout never
        cancels it: a read cancelled as it completes loses its bytes.
        """
        loop = asyncio.get_running_loop()
        reader = channel.reader
        assert reader is not None
        quiet_since = loop.time()
        why = None
        pending: asyncio.Future | None = None

        async def read(size: int, seconds: float) -> bytes | None:
            """The next bytes, b"" at the end, or None if none came in ``seconds``."""
            nonlocal pending
            if pending is None:
                pending = asyncio.ensure_future(reader.read(size))
            done, _ = await asyncio.wait({pending}, timeout=seconds)
            if not done:
                return None
            data, pending = pending.result(), None
            return data

        try:
            while not channel.closed:
                data = await read(READ_BYTES, IDLE_SECONDS)
                if data is None:
                    if not await self._allowed(channel):
                        why = self._withdrawn(channel)
                        break
                    if loop.time() - quiet_since >= KEEPALIVE_SECONDS:
                        await self._send_data(channel, b"")
                        quiet_since = loop.time()
                    continue
                if not data:
                    why = self._stopped(channel)
                    break
                ended = False
                deadline = loop.time() + FLUSH_SECONDS
                while len(data) < READ_BYTES and (remaining := deadline - loop.time()) > 0:
                    more = await read(READ_BYTES - len(data), remaining)
                    if more is None:
                        break
                    if not more:
                        ended = True
                        break
                    data += more
                if not await self._allowed(channel):
                    why = self._withdrawn(channel)
                    break
                await self._send_data(channel, data)
                quiet_since = loop.time()
                if ended:
                    why = self._stopped(channel)
                    break
        except _PeerClosed:
            why = None  # the peer closed it: "<peer> closed <name>"
        except (RelayError, OSError) as exc:
            logger.info("network attach %s to %s ended: %s", channel.name, channel.peer[:12], exc)
            why = self._lost(channel)
        except asyncio.CancelledError:
            raise
        finally:
            if pending is not None:
                pending.cancel()
            if not channel.closed:
                await self._close(channel, tell_peer=True, why=why)

    async def _send_data(self, channel: _Channel, data: bytes) -> None:
        if channel.closed:
            raise _PeerClosed("the channel closed")
        payload = {"channel": channel.id, "seq": channel.send_seq, "data": _pack(data)}
        for attempt in range(2):
            try:
                response = await self._send(channel.peer, ATTACH_DATA, payload)
                break
            except RelayError:
                if attempt or channel.closed:
                    raise
                await asyncio.sleep(1.0)
        if response.get("closed") or response.get("ack") != payload["seq"]:
            raise _PeerClosed("peer closed the channel")
        channel.send_seq += 1

    async def _send(
        self, peer: str, method: str, payload: dict, timeout: float = REQUEST_SECONDS
    ) -> dict:
        await self._pacer.take(_frames(len(payload.get("data", ""))))
        response = await self._request_fn(peer, method, payload, timeout=timeout)
        if not isinstance(response, dict):
            raise RelayError("invalid attach response")
        return response

    # -- closing ----------------------------------------------------------

    def _withdrawn(self, channel: _Channel) -> str:
        return f"closed {channel.name} for {self._peer_name(channel.peer)}: it may no longer open it"

    def _stopped(self, channel: _Channel) -> str:
        return f"{channel.name} stopped; closed it for {self._peer_name(channel.peer)}"

    def _lost(self, channel: _Channel) -> str:
        return f"lost {self._peer_name(channel.peer)}; closed {channel.name} for it"

    async def recheck(self) -> None:
        """Close now what this device's rules no longer allow (a /connect command changed them)."""

        async def one(channel: _Channel) -> None:
            channel.checked = 0.0
            if not await self._allowed(channel):
                await self._close(channel, tell_peer=True, why=self._withdrawn(channel))

        await asyncio.gather(*(one(c) for c in list(self._channels.values()) if not c.requester))

    async def close_peer(self, peer: str) -> None:
        """Close the agents ``peer`` holds open here (its attach permission was withdrawn).

        This device's own channels to that peer's agents stay open.
        """
        await asyncio.gather(
            *(
                self._close(
                    channel,
                    tell_peer=True,
                    why=f"closed {channel.name} for {self._peer_name(peer)}",
                )
                for (key, _), channel in list(self._channels.items())
                if key == peer and not channel.requester
            )
        )

    async def _close(self, channel: _Channel, *, tell_peer: bool, why: str | None = None) -> None:
        if channel.closed:
            return
        channel.closed = True
        self._channels.pop((channel.peer, channel.id), None)
        self._retire_socket(channel)
        # Closing the local socket ends the pump at its next read. It is never
        # cancelled here: mid-send, it holds the peer's secure session.
        if channel.writer is not None:
            channel.writer.close()
        if not channel.requester:
            self._notice(why or f"{self._peer_name(channel.peer)} closed {channel.name}")
        if tell_peer:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self._send(
                        channel.peer, ATTACH_CLOSE, {"channel": channel.id}, timeout=CLOSE_SECONDS
                    ),
                    CLOSE_SECONDS + 1,
                )

    async def close(self) -> None:
        self._closed = True
        channels = list(self._channels.values())
        await asyncio.gather(
            *(
                self._close(channel, tell_peer=True, why=f"closed {channel.name} for {self._peer_name(channel.peer)}")
                for channel in channels
            )
        )
        for channel in channels:
            if channel.pump is not None and not channel.pump.done():
                channel.pump.cancel()
        if self._socket_dir is not None:
            shutil.rmtree(self._socket_dir, ignore_errors=True)
            self._socket_dir = None
