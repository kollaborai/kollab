"""Outbound private-room relay with authenticated application requests.

Discovery and origin pins belong to the caller. This module never invokes a
model, Hub message handler, shell, or workspace tool.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import inspect
import json
import logging
import math
import secrets
import ssl
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import aiohttp
import dns.asyncresolver
import dns.exception
from nacl.exceptions import CryptoError
from nacl.public import Box
from nacl.signing import VerifyKey

from kollabor_config.managed_config import (
    clear_managed_config,
    managed_config_path,
    read_managed_config,
)

from .device_names import (
    NAME_RE,
    contact_route_hex,
    default_device_name,
    default_network_name,
    key_label,
)
from .dns.discovery import _PublicResolver
from .relay_state import (
    ID,
    KEY,
    MAX_ANNOUNCED,
    MAX_APPROVALS,
    MAX_REVOKED,
    MAX_VOUCHERS,
    RelayError,
    RelayStateStore,
    canonical_origin,
    failure_text,
    parse_invite,
    strict_json,
    validate_key,
    validate_public_key,
)

__all__ = ["PeerSessionEvent", "RelayClient", "RelayError", "parse_invite"]

_LOGGER = logging.getLogger(__name__)

PROTOCOL = "kollab-relay/1"
# The relay protocol versions this client speaks, offered newest first in the
# WebSocket subprotocol header. Within a version both sides ignore fields and
# frames they do not know (docs/specs/agent-public-beacon.md#versioning).
PROTOCOLS = (PROTOCOL,)
# On a 426 the directory names the versions it serves here.
PROTOCOLS_HEADER = "X-Kollab-Relay-Protocols"
# Frames the knock service answers (plugins/hub/knocks.py).
KNOCK_FRAMES = frozenset({"knock", "knock_answer", "knock_result"})
# A route lookup or a links declaration waits this long for the directory.
CONTROL_TIMEOUT = 10.0
MAX_FRAME = 65536
MAX_CIPHERTEXT = 49152
MAX_PEERS = 256
MAX_PENDING = 16
MAX_REPLAY = 4096
PING_TIMEOUT = 10
APPLICATION_METHODS = frozenset(
    {
        "directory",
        "message",
        "cancel",
        "status",
        "secure_identity",
        "secure_packet",
        "peer.forward",
    }
)
MAX_APPLICATION_PAYLOAD = 24 * 1024
MAX_PEER_PENDING = 4
MAX_DISPATCH = 16
MAX_PEER_DISPATCH = 4
MAX_REQUEST_TIMEOUT = 300
APPLICATION_ERRORS = frozenset(
    {"busy", "not_supported", "failed", "deadline", "cancelled"}
)
RequestHandler = Callable[[str, str, dict], Awaitable[dict]]
LINK_BINDING_DOMAIN = b"kollab-relay-link/1\x00"


def _version(protocol: str) -> int:
    """`kollab-relay/2` -> 2; 0 for anything else."""
    number = protocol.strip().rpartition("/")[2]
    return int(number) if number.isdigit() else 0


def _version_refused(headers, domain: str) -> str:
    """A relay's 426: it serves none of our versions. Which side updates?"""
    served = (headers.get(PROTOCOLS_HEADER) or "").split(",")
    if max(map(_version, served), default=0) > _version(PROTOCOL):
        return f"this version of kollab is too old for {domain}; run kollab --upgrade"
    return f"{domain} needs an update for this version of kollab"


def link_binding(first: str, second: str) -> str:
    """The envelope `room` value between two keys that are linked across rooms.

    Both keys compute the same value, so it binds a message to this pair of
    devices the way the room hash binds one to a room.
    """
    low, high = sorted((first, second))
    return hashlib.sha256(
        LINK_BINDING_DOMAIN + bytes.fromhex(low) + bytes.fromhex(high)
    ).hexdigest()


# Frames a client may send: a burst, then a steady rate (a token bucket on the real clock).
SEND_BURST = 20.0
SEND_RATE_PER_SECOND = 8


@dataclass

class _ApplicationPending:
    peer: str
    local_session: str
    peer_session: str
    method: str
    future: asyncio.Future


PeerSessionEventKind = Literal[
    "peer_appeared",
    "peer_session_changed",
    "peer_disappeared",
    "peer_revoked",
    "local_disconnected",
]


@dataclass(frozen=True, slots=True)
class PeerSessionEvent:
    """Synchronous lifecycle notification for one approved relay session.

    For peer events, ``peer_key`` identifies the peer and the session fields
    describe its roster session. A local disconnect uses ``peer_key=None`` and
    the session fields describe this client's relay registration.
    """

    kind: PeerSessionEventKind
    peer_key: str | None
    previous_session: str | None
    current_session: str | None


PeerSessionListener = Callable[[PeerSessionEvent], None]


class RelayClient:
    def __init__(
        self, workspace: Path, state_dir: Path | None = None, label: str | None = None
    ):
        self.workspace = Path(workspace).resolve()
        self._store = RelayStateStore(self.workspace, state_dir)
        self.state_dir = self._store.path
        self.state = self._store.state
        self.public_key = self._store.key.verify_key.encode().hex()
        self.label = label if label is not None else self.workspace.name
        if (
            not isinstance(self.label, str)
            or not 1 <= len(self.label) <= 80
            or any(ord(c) < 32 or ord(c) == 127 for c in self.label)
        ):
            raise RelayError("workspace label must be 1–80 printable characters")
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session_id = ""
        self._peers: dict[str, str] = {}
        self._peer_session_listeners: dict[int, PeerSessionListener] = {}
        self._next_peer_session_listener_id = 0
        self._pending: dict[str, tuple[str, asyncio.Future]] = {}
        self._application_pending: dict[str, _ApplicationPending] = {}
        self._request_handler: RequestHandler | None = None
        self._dispatch: dict[tuple[str, str, str, str], asyncio.Task] = {}
        self._replay: dict[tuple[str, str], int] = {}
        self._state = "disconnected"
        self._error = ""
        self._closed = True
        self._first_attempt = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._tokens = SEND_BURST
        self._token_time = time.monotonic()
        self._counts: Counter = Counter()
        self._ws_url = ""
        self._ca = ""
        self._private_cidrs: tuple[str, ...] = ()
        self._knock_handler: Callable[[dict], Awaitable[None]] | None = None
        self._knock_tasks: set[asyncio.Task] = set()
        # One id-less request (a lookup or a links declaration) at a time:
        # (the reply type, what it must echo, its future).
        self._control_lock = asyncio.Lock()
        self._control: tuple[str, tuple[str, object], asyncio.Future] | None = None

    def status(self) -> dict:
        return {
            "state": self._state,
            "enabled": self.state.enabled,
            "origin": self.state.origin,
            "key": self.public_key,
            "workspace_id": self.state.workspace_id,
            "session": self._session_id,
            "peers": len(self._peers),
            "approved_peers": len(self.state.approvals),
            "error": self._error,
            "counters": dict(self._counts),
            "pending_requests": len(self._application_pending),
            "active_requests": len(self._dispatch),
        }

    def peers(self) -> list[dict]:
        return [
            {"key": key, "session": session, "approved": key in self.state.approvals}
            for key, session in sorted(self._peers.items())
        ]

    def invite(self) -> str:
        return self._store.invite()

    def set_request_handler(self, handler: RequestHandler | None) -> None:
        """Install the workspace's authorization/dispatch boundary.

        Discovery does not call this handler. Only approved peers' authenticated
        requests reach it; the handler must additionally enforce workspace and
        operation authority and must propagate asyncio cancellation.
        """
        if handler is not None and not callable(handler):
            raise TypeError("request handler must be callable or None")
        self._request_handler = handler
        # An old handler must not continue under a replacement's policy.
        for task in self._dispatch.values():
            task.cancel()

    def add_peer_session_listener(
        self, listener: PeerSessionListener
    ) -> Callable[[], None]:
        """Subscribe to synchronous approved-peer and local-session changes.

        The returned removal function is idempotent. Listeners observe future
        changes only; callers that attach to an already-connected client should
        inspect ``peers()`` and ``status()`` for its current snapshot.
        """
        if not callable(listener):
            raise TypeError("peer-session listener must be callable")
        token = self._next_peer_session_listener_id
        self._next_peer_session_listener_id += 1
        self._peer_session_listeners[token] = listener
        active = True

        def remove() -> None:
            nonlocal active
            if active:
                active = False
                self._peer_session_listeners.pop(token, None)

        return remove

    def _notify_peer_session_listeners(self, event: PeerSessionEvent) -> None:
        for listener in tuple(self._peer_session_listeners.values()):
            try:
                result = listener(event)
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise TypeError("peer-session listeners must be synchronous")
            except Exception:
                # Listener failures must not interrupt revocation or transport
                # cleanup, and callback exception text may contain private data.
                _LOGGER.warning("relay peer-session listener failed")

    def join_invite(self, token: str) -> str:
        if self._task is not None and not self._task.done():
            raise RelayError("disconnect before joining another invitation")
        self._adopt_bridge_fields()
        origin = self._store.join(token)
        self._forget_stale_primary()
        return origin

    def _forget_stale_primary(self) -> None:
        """A join names this device's primary; a managed-config record left by
        another one (a wiped workspace, an earlier network) would refuse every
        bundle from the new primary as other_primary, for good. The record is
        machine-global, so only a state living in this machine's own
        ``~/.kollab/network`` may touch it."""
        if self.state_dir.parent != managed_config_path().parent.parent / "network":
            return
        stale = read_managed_config()
        if stale is not None and stale.primary_key != self.state.inviter:
            clear_managed_config(primary_key=stale.primary_key)

    def rotate_room(self):
        if self._task is not None and not self._task.done():
            raise RelayError("disconnect before rotating the invitation room")
        self._adopt_bridge_fields()
        self.state.room = secrets.token_hex(32)
        self.state.approvals = []
        self.state.inviter = ""
        # Every prior peer key is meaningless in the new room; keeping their
        # device-name bindings would only block those names from reuse.
        self.state.peer_devices = {}
        self.state.peer_trust = {}
        self.state.config_recipients = []
        self.state.links = []
        self.state.vouched_by = {}
        self.state.revoked = []
        self._store.save()

    async def leave(self) -> None:
        """Disconnect for good: forget the directory, the room and every peer.

        A device that left can then join any network by code, which needs an
        empty origin, no inviter and no approvals.
        """
        await self.close(disable=True)
        self.state.origin = ""
        self.rotate_room()
        self.state.network_name = ""  # rotate keeps the name; leaving forgets it
        self._store.save()

    def _adopt_bridge_fields(self) -> None:
        """Take the fields the agent bridge writes through its own state store.

        This client keeps one long-lived copy and saves all of it, so every
        method that saves must call this first, or it writes a stale device
        name, trust and peer bindings back over the bridge's newer ones.
        """
        disk = RelayStateStore(self.workspace, self.state_dir).state
        for name in (
            "device_name",
            "network_name",
            "trust",
            "peer_devices",
            "peer_trust",
            "config_recipients",
            "links",
            "attach_allowed",
        ):
            setattr(self.state, name, getattr(disk, name))

    def approve(self, key: str):
        validate_public_key(key)
        if key == self.public_key:
            raise RelayError("cannot approve your own key")
        newly_approved = key not in self.state.approvals
        if newly_approved:
            self._adopt_bridge_fields()
            if len(self.state.approvals) >= MAX_APPROVALS:
                raise RelayError("local peer approval capacity reached", "capacity")
            self.state.approvals.append(key)
            try:
                self._store.save()
            except OSError:
                self.state.approvals.remove(key)
                raise
            session = self._peers.get(key)
            if session is not None:
                self._notify_peer_session_listeners(
                    PeerSessionEvent("peer_appeared", key, None, session)
                )

    def remember_announced(self, ids: list[str]) -> None:
        """Keep which join requests and knocks the human was already told about."""
        ids = ids[-MAX_ANNOUNCED:]
        if ids != self.state.announced:
            self._adopt_bridge_fields()
            self.state.announced = ids
            self._store.save()

    def remember_config_told(self, skipped: str, refused: bool) -> None:
        """Keep what the sealed-config sync last told the human, so a restart repeats nothing."""
        if (skipped, refused) != (
            self.state.config_told_skipped,
            self.state.config_told_refused,
        ):
            self._adopt_bridge_fields()
            self.state.config_told_skipped, self.state.config_told_refused = skipped, refused
            self._store.save()

    def add_config_recipient(self, key: str) -> None:
        """Remember a device accepted with a join code: it gets the sealed config.

        A joined device is a member, not a stranger. An earlier knock left it a
        cross-room link and agents trust, which would make the two ends bind
        their messages to different rooms.
        """
        validate_public_key(key)
        self._adopt_bridge_fields()
        changed = False
        if key not in self.state.config_recipients:
            if len(self.state.config_recipients) >= MAX_APPROVALS:
                raise RelayError("config recipient capacity reached", "capacity")
            self.state.config_recipients.append(key)
            changed = True
        # A stranger that joins by code stops being a stranger.
        if key in self.state.links:
            self.state.links.remove(key)
            self.state.peer_trust.pop(key, None)
            changed = True
        # A human just accepted this device: first-hand, and no longer revoked.
        if self.state.vouched_by.pop(key, None) is not None:
            changed = True
        if key in self.state.revoked:
            self.state.revoked.remove(key)
            changed = True
        if changed:
            self._store.save()

    def network_id(self) -> str:
        """What every envelope and membership list of this network is bound to."""
        return hashlib.sha256(bytes.fromhex(self.state.room)).hexdigest()

    def members(self) -> list[str]:
        """The devices on this network: approved, and not an accepted stranger.

        The relay path and the mesh path both ask this one question.
        """
        self._adopt_bridge_fields()  # the bridge records accepted strangers
        strangers = set(self.state.links)
        return [key for key in self.state.approvals if key not in strangers]

    def membership(self) -> tuple[list[tuple[str, str]], list[str]]:
        """What this device tells its members: (key, name) and its revocations.

        Only first-hand members are listed: devices a human here accepted, by
        join code or by joining through them. A device this one merely heard
        about is never passed on, so every vouch traces back to a human's accept.
        """
        self._adopt_bridge_fields()
        listed = [
            (key, self.state.peer_devices.get(key, ""))
            for key in self.members()
            if key not in self.state.vouched_by
        ]
        return listed, list(self.state.revoked)

    def accept_membership(
        self, voucher: str, members: list[tuple[str, str]], revoked: list[str]
    ) -> None:
        """Take a member's word on who else is on this network.

        Only a member this device already approved is heard. A device it names
        is approved, and remembered as vouched for by that member. A device it
        stops naming loses that vouch; one left with none is dropped. A
        revocation is applied at once and remembered: no vouch brings that
        device back until a human here accepts it again with a join code.
        Accepted strangers never enter or leave this way.
        """
        self._adopt_bridge_fields()
        if voucher not in self.members():
            raise RelayError("peer is not part of this network")
        for key in revoked:
            if key not in (self.public_key, voucher) and key not in self.state.links:
                self.revoke(key, announce=True)
        named = set()
        for key, name in members:
            named.add(key)
            if key == self.public_key or key in self.state.links or key in self.state.revoked:
                continue
            first_hand = key in self.state.approvals and key not in self.state.vouched_by
            if key not in self.state.approvals:
                try:
                    self.approve(key)
                except RelayError:
                    continue  # at capacity: this device stays out
                taken = {
                    self.state.device_name or default_device_name(self.workspace),
                    *self.state.peer_devices.values(),
                }
                if not NAME_RE.fullmatch(name) or name in taken:
                    name = key_label(key)  # a name is never reused or renamed
                self.state.peer_devices[key] = name
                self._store.save()  # the next approval re-reads bridge fields from disk
            if not first_hand:
                vouchers = self.state.vouched_by.setdefault(key, [])
                if voucher not in vouchers and len(vouchers) < MAX_VOUCHERS:
                    vouchers.append(voucher)
        orphans = []
        for member, vouchers in tuple(self.state.vouched_by.items()):
            if voucher in vouchers and member not in named:
                vouchers.remove(voucher)
                if not vouchers:
                    del self.state.vouched_by[member]
                    orphans.append(member)
        self._store.save()
        for orphan in orphans:
            self.revoke(orphan)

    def revoke(self, key: str, *, announce: bool = False):
        """Drop a device. `announce` also tells the members, at their next list.

        Devices whose only vouch came from `key` go with it.
        """
        validate_key(key)
        was_approved = key in self.state.approvals
        previous_session = self._peers.get(key) if was_approved else None
        orphans: list[str] = []
        try:
            self._adopt_bridge_fields()
            changed = False
            if announce and key not in self.state.links and key not in self.state.revoked:
                if key != self.public_key:
                    self.state.revoked.append(key)
                    del self.state.revoked[:-MAX_REVOKED]
                    changed = True
            if self.state.vouched_by.pop(key, None) is not None:
                changed = True
            for member, vouchers in tuple(self.state.vouched_by.items()):
                if key in vouchers:
                    vouchers.remove(key)
                    changed = True
                    if not vouchers:
                        del self.state.vouched_by[member]
                        orphans.append(member)
            if key in self.state.config_recipients:
                self.state.config_recipients.remove(key)
                changed = True
            if key in self.state.approvals:
                self.state.approvals.remove(key)
                changed = True
            if self.state.peer_devices.pop(key, None) is not None:
                changed = True
            if self.state.peer_trust.pop(key, None) is not None:
                changed = True
            if key in self.state.links:
                self.state.links.remove(key)
                changed = True
            if changed:
                self._store.save()
        finally:
            # A persistence error must not leave already-running callbacks
            # authorized in this process after the user revoked their peer.
            for request_id, (peer, future) in tuple(self._pending.items()):
                if peer == key:
                    self._pending.pop(request_id, None)
                    if not future.done():
                        future.set_exception(RelayError("peer approval revoked"))
            self._settle_application_peer(key, "peer approval revoked")
            if was_approved:
                self._notify_peer_session_listeners(
                    PeerSessionEvent("peer_revoked", key, previous_session, None)
                )
        for orphan in orphans:
            self.revoke(orphan)

    def _binding(self, peer_key: str) -> str:
        """What an envelope's `room` must say for this peer.

        An accepted stranger lives in its own room, so its messages are bound
        to the pair of keys instead of a room both sides share.
        """
        if peer_key in self.state.links:
            return link_binding(self.public_key, peer_key)
        return self.network_id()

    def _settle_application_peer(self, key: str, reason: str) -> None:
        for request_id, pending in tuple(self._application_pending.items()):
            if pending.peer == key:
                self._application_pending.pop(request_id, None)
                if not pending.future.done():
                    pending.future.set_exception(RelayError(reason))
        for (peer, _, _, _), task in tuple(self._dispatch.items()):
            if peer == key:
                task.cancel()

    async def connect(
        self,
        origin: str,
        *,
        ws_url: str,
        ca: str = "",
        private_cidrs: tuple[str, ...] = (),
    ) -> dict:
        """Connect only after the caller verified signed discovery and its pin."""
        canonical_origin(origin)
        parsed = urlsplit(ws_url)
        expected = "wss://" + origin.removeprefix("https://") + "/relay/v1/ws"
        if (
            ws_url != expected
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise RelayError(
                "verified relay endpoint must be canonical same-origin /relay/v1/ws"
            )
        if self.state.origin and self.state.origin != origin:
            raise RelayError("run /connect leave before joining another directory")
        await self.close()
        self._ws_url, self._ca, self._private_cidrs = ws_url, ca, tuple(private_cidrs)
        # Validate operator configuration before starting any background work.
        _PublicResolver(dns.asyncresolver.Resolver(), self._private_cidrs)
        ssl.create_default_context(cafile=ca or None)
        self._adopt_bridge_fields()
        self.state.origin, self.state.enabled = origin, True
        if not self.state.network_name and not self.state.inviter:
            # No one invited this device, so it is the first: it names the network.
            self.state.network_name = default_network_name(
                self.state.device_name or default_device_name(self.workspace)
            )
        self._store.save()
        self._closed, self._error = False, ""
        self._state = "connecting"
        self._first_attempt.clear()
        self._task = asyncio.create_task(self._run(), name="kollab-relay-client")
        await asyncio.wait_for(self._first_attempt.wait(), timeout=15)
        return self.status()

    async def close(self, disable: bool = False):
        self._closed = True
        if disable:
            self._adopt_bridge_fields()
            self.state.enabled = False
            self._store.save()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._clear_connection()
        if self._dispatch:
            await asyncio.gather(
                *tuple(self._dispatch.values()), return_exceptions=True
            )
        self._state = "disconnected"

    def _clear_connection(self):
        previous_local_session = self._session_id or None
        previous_peers = dict(self._peers)
        approved = set(self.state.approvals)
        self._ws = None
        self._session_id = ""
        self._peers.clear()
        self._replay.clear()
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(RelayError("relay disconnected"))
        self._pending.clear()
        for pending in self._application_pending.values():
            if not pending.future.done():
                pending.future.set_exception(RelayError("relay disconnected"))
        self._application_pending.clear()
        if self._control is not None and not self._control[2].done():
            self._control[2].set_exception(RelayError("relay disconnected"))
        for task in tuple(self._dispatch.values()):
            task.cancel()
        for key in sorted(previous_peers):
            if key in approved:
                self._notify_peer_session_listeners(
                    PeerSessionEvent("peer_disappeared", key, previous_peers[key], None)
                )
        if previous_local_session is not None:
            self._notify_peer_session_listeners(
                PeerSessionEvent(
                    "local_disconnected", None, previous_local_session, None
                )
            )

    async def _run(self):
        delay = 1.0
        while not self._closed:
            self._state = "connecting"
            try:
                await self._connection()
                self._error = "relay connection closed"
            except asyncio.CancelledError:
                raise
            except (
                aiohttp.ClientError,
                dns.exception.DNSException,
                OSError,
                TimeoutError,
                ValueError,
                TypeError,
                CryptoError,
            ) as exc:
                # Never retain URLs from exception text: invitations and
                # future protocol changes must not leak through diagnostics.
                self._error = (
                    str(exc)
                    if isinstance(exc, RelayError)
                    else "relay transport unavailable"
                )
            finally:
                was_online = self._state == "online"
                self._clear_connection()
                self._first_attempt.set()
            if self._closed:
                break
            self._state = "reconnecting"
            delay = 1.0 if was_online else min(delay * 2, 30.0)
            await asyncio.sleep(delay + secrets.randbelow(1000) / 1000)

    async def _connection(self):
        context = ssl.create_default_context(cafile=self._ca or None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connector = aiohttp.TCPConnector(
            resolver=_PublicResolver(dns.asyncresolver.Resolver(), self._private_cidrs),
            use_dns_cache=False,
            ssl=context,
            limit=1,
        )
        trace = aiohttp.TraceConfig()

        async def reject_redirect(*_):
            raise RelayError("relay WebSocket redirects are not permitted")

        trace.on_request_redirect.append(reject_redirect)
        async with aiohttp.ClientSession(
            connector=connector,
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10),
            trace_configs=[trace],
        ) as session:
            async with asyncio.timeout(12):
                try:
                    ws = await session.ws_connect(
                        self._ws_url,
                        protocols=PROTOCOLS,
                        heartbeat=20,
                        max_msg_size=MAX_FRAME,
                        compress=0,
                        timeout=aiohttp.ClientWSTimeout(ws_close=3),
                    )
                except aiohttp.WSServerHandshakeError as exc:
                    if exc.status != 426:
                        raise
                    domain = urlsplit(self.state.origin).hostname or "the relay"
                    raise RelayError(_version_refused(exc.headers, domain)) from None
                self._ws = ws
                challenge = await self._receive_frame(ws)
                if (
                    not challenge.keys() >= {"type", "protocol", "origin", "nonce"}
                    or challenge["type"] != "challenge"
                    or challenge["protocol"] != PROTOCOL
                    or challenge["origin"] != self.state.origin
                    or not isinstance(challenge["nonce"], str)
                    or not KEY.fullmatch(challenge["nonce"])
                ):
                    raise RelayError("invalid relay registration challenge")
                self._session_id = secrets.token_hex(16)
                signed = "\n".join(
                    (
                        PROTOCOL,
                        self.state.origin,
                        challenge["nonce"],
                        self.public_key,
                        self.state.room,
                        self._session_id,
                    )
                ).encode()
                await self._send_frame(
                    {
                        "type": "register",
                        "key": self.public_key,
                        "room": self.state.room,
                        "session": self._session_id,
                        "signature": self._store.key.sign(signed).signature.hex(),
                    }
                )
                registered = await self._receive_frame(ws)
                expected = {
                    "type": "registered",
                    "protocol": PROTOCOL,
                    "key": self.public_key,
                    "session": self._session_id,
                }
                if any(registered.get(field) != value for field, value in expected.items()):
                    raise RelayError("relay registration rejected")
                self._set_peers(await self._receive_frame(ws))
                self._state, self._error = "online", ""
                self._first_attempt.set()
            try:
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_frame(strict_json(message.data))
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        raise RelayError("binary relay frames are unsupported")
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        raise RelayError("relay WebSocket closed with an error")
            finally:
                await ws.close()

    @staticmethod
    async def _receive_frame(ws):
        message = await ws.receive()
        if message.type != aiohttp.WSMsgType.TEXT:
            raise RelayError("relay expected a text frame")
        return strict_json(message.data)

    def _set_peers(self, frame):
        if (
            not frame.keys() >= {"type", "peers"}
            or frame["type"] != "peers"
            or not isinstance(frame["peers"], list)
            or len(frame["peers"]) > MAX_PEERS
        ):
            raise RelayError("invalid peer snapshot")
        peers = {}
        for peer in frame["peers"]:
            if not isinstance(peer, dict) or not peer.keys() >= {"key", "session"}:
                raise RelayError("invalid peer entry")
            key = validate_key(peer["key"])
            if (
                key == self.public_key
                or key in peers
                or not isinstance(peer["session"], str)
                or not ID.fullmatch(peer["session"])
            ):
                raise RelayError("invalid peer identity or session")
            peers[key] = peer["session"]
        previous_peers = self._peers
        for request_id, (key, future) in tuple(self._pending.items()):
            if peers.get(key) != previous_peers.get(key):
                self._pending.pop(request_id, None)
                if not future.done():
                    future.set_exception(
                        RelayError("peer went offline or changed session")
                    )
        for key, session in previous_peers.items():
            if peers.get(key) != session:
                self._settle_application_peer(
                    key, "peer went offline or changed session"
                )
        self._peers = peers
        for key in sorted(set(previous_peers) | set(peers)):
            previous_session = previous_peers.get(key)
            current_session = peers.get(key)
            if previous_session == current_session or key not in self.state.approvals:
                continue
            if previous_session is None:
                kind: PeerSessionEventKind = "peer_appeared"
            elif current_session is None:
                kind = "peer_disappeared"
            else:
                kind = "peer_session_changed"
            self._notify_peer_session_listeners(
                PeerSessionEvent(kind, key, previous_session, current_session)
            )

    async def _send_frame(self, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > MAX_FRAME:
            raise RelayError("relay frame too large")
        async with self._send_lock:
            now = time.monotonic()
            self._tokens = min(
                SEND_BURST, self._tokens + (now - self._token_time) * SEND_RATE_PER_SECOND
            )
            self._token_time = now
            if self._tokens < 1:
                raise RelayError("relay send rate limit reached")
            self._tokens -= 1
            if self._ws is None or self._ws.closed:
                raise RelayError("relay is not connected")
            await asyncio.wait_for(self._ws.send_str(raw), timeout=3)

    async def ping(self, peer_key: str) -> dict:
        validate_key(peer_key)
        if peer_key not in self.state.approvals:
            raise RelayError("peer requires explicit local approval")
        if self._state != "online" or peer_key not in self._peers:
            raise RelayError("peer is offline")
        if len(self._pending) >= MAX_PENDING:
            raise RelayError("pending ping capacity reached")
        request_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (peer_key, future)
        start = time.monotonic()
        try:
            await self._send_encrypted(peer_key, "ping", {}, request_id)
            payload = await asyncio.wait_for(future, timeout=PING_TIMEOUT)
            return {
                "peer": peer_key,
                "id": request_id,
                **payload,
                "round_trip_ms": round((time.monotonic() - start) * 1000),
            }
        except TimeoutError as exc:
            raise RelayError("no authenticated pong received before deadline") from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    @staticmethod
    def _application_payload(payload: dict) -> dict:
        """Copy a bounded JSON object before crossing an asynchronous boundary."""
        if not isinstance(payload, dict):
            raise RelayError("application payload must be a JSON object")
        try:
            raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
            value = strict_json(raw, limit=MAX_APPLICATION_PAYLOAD)
            # Leave nesting headroom for the encrypted envelope's payload and
            # arguments/result objects; accepted data must also be receivable.
            strict_json('{"payload":{"arguments":' + raw + "}}", limit=MAX_CIPHERTEXT)
            return value
        except (ValueError, TypeError, RecursionError) as exc:
            raise RelayError("invalid or oversized application payload") from exc

    async def request(
        self, peer_key: str, method: str, payload: dict, *, timeout: float = 10.0
    ) -> dict:
        """Return an authenticated receiver response, never a relay receipt.

        A successful transport response means only what the workspace handler
        states in its result (for example accepted), not completed model work.
        Requests are not retried implicitly: callers own operation idempotency.
        """
        validate_key(peer_key)
        if not isinstance(method, str) or method not in APPLICATION_METHODS:
            raise RelayError("unsupported application method")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= MAX_REQUEST_TIMEOUT
        ):
            raise RelayError(
                "application timeout must be greater than zero and at most 300 seconds"
            )
        arguments = self._application_payload(payload)
        if peer_key not in self.state.approvals:
            raise RelayError("peer requires explicit local approval")
        if self._state != "online" or peer_key not in self._peers:
            raise RelayError("peer is offline")
        if (
            len(self._application_pending) >= MAX_PENDING
            or sum(p.peer == peer_key for p in self._application_pending.values())
            >= MAX_PEER_PENDING
        ):
            raise RelayError("pending application request capacity reached")
        request_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        pending = _ApplicationPending(
            peer_key, self._session_id, self._peers[peer_key], method, future
        )
        self._application_pending[request_id] = pending
        try:
            async with asyncio.timeout(timeout):
                await self._send_encrypted(
                    peer_key,
                    "request",
                    {
                        "method": method,
                        "arguments": arguments,
                        "timeout_ms": max(1, math.ceil(timeout * 1000)),
                    },
                    request_id,
                )
                return await future
        except TimeoutError as exc:
            await self._cancel_remote_request(request_id, pending)
            raise RelayError(
                "no authenticated application response received before deadline"
            ) from exc
        except asyncio.CancelledError:
            await self._cancel_remote_request(request_id, pending)
            raise
        finally:
            self._application_pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _cancel_remote_request(
        self, request_id: str, pending: _ApplicationPending
    ) -> None:
        if (
            pending.peer in self.state.approvals
            and self._session_id == pending.local_session
            and self._peers.get(pending.peer) == pending.peer_session
        ):
            try:
                await self._send_encrypted(
                    pending.peer,
                    "request_cancel",
                    {"reply_to": request_id, "method": pending.method},
                    secrets.token_hex(16),
                )
            except (RelayError, aiohttp.ClientError, OSError, TimeoutError):
                # The receiver also enforces a finite deadline. Cancellation is
                # best effort when transport or peer presence was lost.
                pass

    async def _application_response(
        self,
        key: str,
        session: str,
        local_session: str,
        request_id: str,
        method: str,
        *,
        result: dict | None = None,
        error: str = "",
    ) -> None:
        if (
            self._session_id != local_session
            or self._peers.get(key) != session
            or key not in self.state.approvals
        ):
            return
        try:
            await self._send_encrypted(
                key,
                "response",
                {
                    "reply_to": request_id,
                    "method": method,
                    "result": result,
                    "error": error,
                },
                secrets.token_hex(16),
            )
        except (RelayError, aiohttp.ClientError, OSError, TimeoutError):
            self._counts["response_send_failures"] += 1

    async def _dispatch_request(
        self,
        key: str,
        session: str,
        local_session: str,
        request_id: str,
        method: str,
        arguments: dict,
        timeout: float,
        handler: RequestHandler,
    ) -> None:
        result, error = None, ""
        try:
            async with asyncio.timeout(timeout):
                # Recheck after task scheduling: revocation and peer snapshots
                # may have arrived since envelope validation.
                if (
                    key not in self.state.approvals
                    or self._peers.get(key) != session
                    or self._session_id != local_session
                    or self._request_handler is not handler
                ):
                    return
                result = self._application_payload(
                    await handler(key, method, arguments)
                )
        except TimeoutError:
            error = "deadline"
        except asyncio.CancelledError:
            # A cancelled request must never produce a successful response.
            raise
        except Exception as exc:
            # Handler details can contain credentials, filesystem paths, or
            # prompt content; only fixed transport errors cross this boundary.
            _LOGGER.warning("peer application request failed: %s", failure_text(exc))
            error = "failed"
        await self._application_response(
            key,
            session,
            local_session,
            request_id,
            method,
            result=result,
            error=error,
        )

    def _dispatch_finished(
        self, identity: tuple[str, str, str, str], task: asyncio.Task
    ) -> None:
        if self._dispatch.get(identity) is task:
            self._dispatch.pop(identity, None)
        if not task.cancelled():
            task.exception()

    async def _send_encrypted(self, key, kind, payload, message_id):
        if key not in self.state.approvals or key not in self._peers:
            raise RelayError("peer is unapproved or offline")
        now = int(time.time())
        envelope = {
            "v": 1,
            "from": self.public_key,
            "to": key,
            "from_session": self._session_id,
            "to_session": self._peers[key],
            "room": self._binding(key),
            "id": message_id,
            "sent_at": now,
            "expires_at": now + 30,
            "kind": kind,
            "payload": payload,
        }
        box = Box(
            self._store.key.to_curve25519_private_key(),
            VerifyKey(bytes.fromhex(key)).to_curve25519_public_key(),
        )
        encoded = json.dumps(envelope, separators=(",", ":"))
        strict_json(encoded, limit=MAX_CIPHERTEXT)
        ciphertext = base64.b64encode(box.encrypt(encoded.encode())).decode()
        if len(ciphertext) > MAX_CIPHERTEXT:
            raise RelayError("ciphertext size limit exceeded")
        await self._send_frame(
            {"type": "send", "to": key, "id": message_id, "ciphertext": ciphertext}
        )

    async def _handle_frame(self, frame):
        if frame.get("type") == "peers":
            self._set_peers(frame)
        elif frame.get("type") == "error":
            # An unauthenticated transport status can fail a request, but
            # only a decrypted, correlated pong can complete it successfully.
            code = frame.get("code")
            request_id = frame.get("id")
            if (
                not frame.keys() >= {"type", "code"}
                or not isinstance(code, str)
                or not code.isascii()
                or not code.replace("_", "").isalnum()
                or len(code) > 64
                or (
                    "id" in frame
                    and (
                        not isinstance(request_id, str) or not ID.fullmatch(request_id)
                    )
                )
            ):
                raise RelayError("invalid relay error frame")
            pending = self._pending.get(request_id)
            if pending and not pending[1].done():
                pending[1].set_exception(RelayError("relay transport error: " + code))
            application = self._application_pending.get(request_id)
            if application and not application.future.done():
                application.future.set_exception(
                    RelayError("relay transport error: " + code)
                )
            control = self._control
            if request_id is None and control is not None and not control[2].done():
                # A directory without lookup and links answers them with an
                # id-less invalid_frame: it needs an update.
                if code == "invalid_frame":
                    control[2].set_result({"type": control[0], "result": "outdated"})
                else:
                    control[2].set_exception(RelayError("relay transport error: " + code))
            if request_id is not None and not pending and not application:
                # The directory refused a knock or an answer this device sent.
                self._dispatch_knock(
                    {"type": "knock_result", "id": request_id, "result": "refused"}
                )
        elif frame.get("type") in ("lookup_result", "links_result"):
            control = self._control
            echo = control[1] if control else None
            if (
                control is not None
                and frame["type"] == control[0]
                and echo is not None
                and frame.get(echo[0]) == echo[1]
                and not control[2].done()
            ):
                control[2].set_result(frame)
        elif frame.get("type") in KNOCK_FRAMES:
            self._dispatch_knock(frame)
        elif frame.get("type") == "message":
            try:
                await self._receive_encrypted(frame)
            except (RelayError, CryptoError, ValueError, TypeError):
                self._counts["rejected_messages"] += 1
        else:
            # A frame from a newer directory: ignored, never a reason to drop
            # the connection.
            self._counts["ignored_frames"] += 1

    def set_knock_handler(self, handler: Callable[[dict], Awaitable[None]] | None) -> None:
        """Where knocks, answers and knock results go (plugins/hub/knocks.py)."""
        self._knock_handler = handler

    def _dispatch_knock(self, frame: dict) -> None:
        """Hand a knock frame to the knock service without holding up the socket.

        It runs in a fresh context, never inside a model turn, and in its own
        task because answering waits for a reply this socket has to read.
        """
        handler = self._knock_handler
        if handler is None:
            return

        def done(task: asyncio.Task) -> None:
            self._knock_tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                _LOGGER.warning("knock handling failed: %s", type(task.exception()).__name__)

        task = asyncio.create_task(handler(frame), context=contextvars.Context())
        self._knock_tasks.add(task)
        task.add_done_callback(done)

    async def _control_request(self, frame: dict, reply: str, echo: tuple[str, object]) -> dict:
        async with self._control_lock:
            if self._state != "online":
                raise RelayError("relay is not connected")
            future = asyncio.get_running_loop().create_future()
            self._control = (reply, echo, future)
            try:
                await self._send_frame(frame)
                return await asyncio.wait_for(future, timeout=CONTROL_TIMEOUT)
            except TimeoutError:
                return {"type": reply, "result": "busy"}
            finally:
                self._control = None

    async def lookup(self, route: str) -> tuple[str, str]:
        """The key online under a contact route: (`found`, key), or (`unavailable`
        | `busy` | `outdated`, "").

        Never trusts the directory: a key that does not hash to the route reads
        as unavailable, so a directory cannot point a knock at another device.
        """
        reply = await self._control_request(
            {"type": "lookup", "route": route}, "lookup_result", ("route", route)
        )
        result, key = reply.get("result"), reply.get("key", "")
        if result in ("unavailable", "busy", "outdated"):
            return result, ""
        if (
            result != "found"
            or not isinstance(key, str)
            or not KEY.fullmatch(key)
            or key == self.public_key
            or contact_route_hex(key) != route
        ):
            _LOGGER.warning("the directory answered a route lookup with a key that does not fit")
            return "unavailable", ""
        return "found", key

    async def declare_links(self, peers: list[str]) -> str:
        """Replace the keys this device consents to link with; the directory's result."""
        issued_at = int(time.time())
        reply = await self._control_request(
            {"type": "links", "peers": sorted(set(peers)), "issued_at": issued_at},
            "links_result",
            ("issued_at", issued_at),
        )
        result = reply.get("result")
        return result if isinstance(result, str) else "busy"

    async def send_knock(self, to: str, knock_id: str, ticket: dict, ciphertext: str) -> None:
        await self._send_frame(
            {"type": "knock", "to": to, "id": knock_id, "ticket": ticket, "ciphertext": ciphertext}
        )

    async def send_knock_answer(self, knock_id: str, ticket: dict, ciphertext: str) -> None:
        await self._send_frame(
            {"type": "knock_answer", "id": knock_id, "ticket": ticket, "ciphertext": ciphertext}
        )

    async def _receive_encrypted(self, frame):
        if not frame.keys() >= {"type", "from", "session", "id", "ciphertext"}:
            raise RelayError("invalid encrypted frame")
        key = validate_key(frame["from"])
        if key not in self.state.approvals:
            raise RelayError("unapproved peer")
        if (
            self._peers.get(key) != frame["session"]
            or not isinstance(frame["id"], str)
            or not ID.fullmatch(frame["id"])
        ):
            raise RelayError("invalid sender session or message id")
        ciphertext = frame["ciphertext"]
        if not isinstance(ciphertext, str) or len(ciphertext) > MAX_CIPHERTEXT:
            raise RelayError("invalid ciphertext size")
        encrypted = base64.b64decode(ciphertext, validate=True)
        if base64.b64encode(encrypted).decode() != ciphertext:
            raise RelayError("noncanonical ciphertext encoding")
        box = Box(
            self._store.key.to_curve25519_private_key(),
            VerifyKey(bytes.fromhex(key)).to_curve25519_public_key(),
        )
        body = strict_json(box.decrypt(encrypted), limit=MAX_CIPHERTEXT)
        fields = {
            "v",
            "from",
            "to",
            "from_session",
            "to_session",
            "room",
            "id",
            "sent_at",
            "expires_at",
            "kind",
            "payload",
        }
        if (
            not body.keys() >= fields
            or type(body["v"]) is not int
            or body["v"] != 1
            or body["from"] != key
            or body["to"] != self.public_key
            or body["from_session"] != frame["session"]
            or body["to_session"] != self._session_id
            or body["room"] != self._binding(key)
            or body["id"] != frame["id"]
        ):
            raise RelayError("encrypted envelope binding mismatch")
        now = int(time.time())
        sent, expires = body["sent_at"], body["expires_at"]
        if (
            type(sent) is not int
            or type(expires) is not int
            or not sent < expires <= sent + 60
            or sent > now + 30
            or expires < now
        ):
            raise RelayError("encrypted message outside validity window")
        self._replay = {
            key: expiry for key, expiry in self._replay.items() if expiry >= now
        }
        replay_key = (key, body["id"])
        if replay_key in self._replay or len(self._replay) >= MAX_REPLAY:
            raise RelayError("replayed message or replay capacity reached")
        self._replay[replay_key] = expires
        payload = body["payload"]
        if body["kind"] == "ping" and payload == {}:
            await self._send_encrypted(
                key,
                "pong",
                {
                    "reply_to": body["id"],
                    "label": self.label,
                    "workspace_id": self.state.workspace_id,
                },
                secrets.token_hex(16),
            )
            self._counts["received_pings"] += 1
        elif body["kind"] == "pong":
            if not isinstance(payload, dict) or not payload.keys() >= {
                "reply_to",
                "label",
                "workspace_id",
            }:
                raise RelayError("invalid pong")
            payload = {name: payload[name] for name in ("reply_to", "label", "workspace_id")}
            if (
                not isinstance(payload["label"], str)
                or not 1 <= len(payload["label"]) <= 80
                or any(ord(c) < 32 or ord(c) == 127 for c in payload["label"])
            ):
                raise RelayError("invalid pong label")
            if not isinstance(payload["workspace_id"], str) or not ID.fullmatch(
                payload["workspace_id"]
            ):
                raise RelayError("invalid pong workspace identity")
            if not isinstance(payload["reply_to"], str) or not ID.fullmatch(
                payload["reply_to"]
            ):
                raise RelayError("invalid pong correlation")
            pending = self._pending.get(payload["reply_to"])
            if pending and pending[0] == key and not pending[1].done():
                pending[1].set_result(payload)
                self._counts["received_pongs"] += 1
        elif body["kind"] == "request":
            if (
                not isinstance(payload, dict)
                or not payload.keys() >= {"method", "arguments", "timeout_ms"}
                or not isinstance(payload["method"], str)
                or not 1 <= len(payload["method"]) <= 64
                or type(payload["timeout_ms"]) is not int
                or not 1 <= payload["timeout_ms"] <= MAX_REQUEST_TIMEOUT * 1000
            ):
                raise RelayError("invalid application request")
            method = payload["method"]
            handler = self._request_handler
            error = ""
            if handler is None or method not in APPLICATION_METHODS:
                # A newer device's method is answered at once, not left to
                # wait out its timeout.
                error = "not_supported"
            elif (
                len(self._dispatch) >= MAX_DISPATCH
                or sum(peer == key for peer, _, _, _ in self._dispatch)
                >= MAX_PEER_DISPATCH
            ):
                error = "busy"
            if error:
                await self._application_response(
                    key,
                    frame["session"],
                    self._session_id,
                    body["id"],
                    method,
                    error=error,
                )
                return
            arguments = self._application_payload(payload["arguments"])
            identity = (key, frame["session"], body["id"], method)
            if identity in self._dispatch:
                # A callback may outlive its envelope's replay retention. A
                # fresh envelope reusing that ID cannot replace its owned task.
                raise RelayError("application request is already running")
            task = asyncio.create_task(
                self._dispatch_request(
                    key,
                    frame["session"],
                    self._session_id,
                    body["id"],
                    method,
                    arguments,
                    payload["timeout_ms"] / 1000,
                    handler,
                ),
                name="kollab-relay-application-request",
            )
            self._dispatch[identity] = task
            task.add_done_callback(
                lambda done, identity=identity: self._dispatch_finished(identity, done)
            )
            self._counts["received_requests"] += 1
        elif body["kind"] == "response":
            if (
                not isinstance(payload, dict)
                or not payload.keys() >= {"reply_to", "method", "result", "error"}
                or not isinstance(payload["reply_to"], str)
                or not ID.fullmatch(payload["reply_to"])
                or not isinstance(payload["method"], str)
                or payload["method"] not in APPLICATION_METHODS
                or not isinstance(payload["error"], str)
                or (payload["error"] and payload["error"] not in APPLICATION_ERRORS)
            ):
                raise RelayError("invalid application response")
            if payload["error"]:
                if payload["result"] is not None:
                    raise RelayError("application error cannot include a result")
                result = None
            else:
                result = self._application_payload(payload["result"])
            pending = self._application_pending.get(payload["reply_to"])
            if (
                pending is not None
                and pending.peer == key
                and pending.peer_session == frame["session"]
                and pending.local_session == self._session_id
                and pending.method == payload["method"]
                and not pending.future.done()
            ):
                if payload["error"]:
                    pending.future.set_exception(
                        RelayError("peer application request " + payload["error"])
                    )
                else:
                    pending.future.set_result(result)
                self._counts["received_responses"] += 1
        elif body["kind"] == "request_cancel":
            if (
                not isinstance(payload, dict)
                or not payload.keys() >= {"reply_to", "method"}
                or not isinstance(payload["reply_to"], str)
                or not ID.fullmatch(payload["reply_to"])
                or not isinstance(payload["method"], str)
                or payload["method"] not in APPLICATION_METHODS
            ):
                raise RelayError("invalid application cancellation")
            task = self._dispatch.get(
                (key, frame["session"], payload["reply_to"], payload["method"])
            )
            if task is not None:
                task.cancel()
                self._counts["cancelled_requests"] += 1
        else:
            # A kind from a newer device: ignored, like an unknown field.
            self._counts["ignored_messages"] += 1
