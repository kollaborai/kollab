"""Outbound private-room relay: endpoint-encrypted ping/presence only.

Discovery and origin pins belong to the caller. This module never invokes a
model, Hub message handler, shell, or workspace tool.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import ssl
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import dns.asyncresolver
import dns.exception
from nacl.exceptions import CryptoError
from nacl.public import Box
from nacl.signing import VerifyKey

from .dns.discovery import _PublicResolver
from .relay_state import (
    ID,
    KEY,
    MAX_APPROVALS,
    RelayError,
    RelayStateStore,
    canonical_origin,
    parse_invite,
    strict_json,
    validate_key,
    validate_public_key,
)

__all__ = ["RelayClient", "RelayError", "parse_invite"]

PROTOCOL = "kollab-relay/1"
MAX_FRAME = 65536
MAX_CIPHERTEXT = 49152
MAX_PEERS = 256
MAX_PENDING = 16
MAX_REPLAY = 4096
PING_TIMEOUT = 10


class RelayClient:
    def __init__(self, workspace: Path, state_dir: Path | None = None, label: str | None = None):
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
        self._pending: dict[str, tuple[str, asyncio.Future]] = {}
        self._replay: dict[tuple[str, str], int] = {}
        self._state = "disconnected"
        self._error = ""
        self._closed = True
        self._first_attempt = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._tokens = 20.0
        self._token_time = time.monotonic()
        self._counts: Counter = Counter()
        self._ws_url = ""
        self._ca = ""
        self._private_cidrs: tuple[str, ...] = ()

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
        }

    def peers(self) -> list[dict]:
        return [
            {"key": key, "session": session, "approved": key in self.state.approvals}
            for key, session in sorted(self._peers.items())
        ]

    def invite(self) -> str:
        return self._store.invite()

    def join_invite(self, token: str) -> str:
        if self._task is not None and not self._task.done():
            raise RelayError("disconnect before joining another invitation")
        return self._store.join(token)

    def rotate_room(self):
        if self._task is not None and not self._task.done():
            raise RelayError("disconnect before rotating the invitation room")
        self.state.room = secrets.token_hex(32)
        self.state.approvals = []
        self.state.inviter = ""
        self._store.save()

    def approve(self, key: str):
        validate_public_key(key)
        if key == self.public_key:
            raise RelayError("cannot approve your own key")
        if key not in self.state.approvals:
            if len(self.state.approvals) >= MAX_APPROVALS:
                raise RelayError("local peer approval capacity reached")
            self.state.approvals.append(key)
            try:
                self._store.save()
            except OSError:
                self.state.approvals.remove(key)
                raise

    def revoke(self, key: str):
        validate_key(key)
        if key in self.state.approvals:
            self.state.approvals.remove(key)
            self._store.save()
        for request_id, (peer, future) in tuple(self._pending.items()):
            if peer == key:
                self._pending.pop(request_id, None)
                if not future.done():
                    future.set_exception(RelayError("peer approval revoked"))

    async def connect(self, origin: str, *, ws_url: str, ca: str = "", private_cidrs: tuple[str, ...] = ()) -> dict:
        """Connect only after the caller verified signed discovery and its pin."""
        canonical_origin(origin)
        parsed = urlsplit(ws_url)
        expected = "wss://" + origin.removeprefix("https://") + "/relay/v1/ws"
        if ws_url != expected or parsed.username is not None or parsed.password is not None:
            raise RelayError("verified relay endpoint must be canonical same-origin /relay/v1/ws")
        if self.state.origin and self.state.origin != origin:
            raise RelayError("disconnect and join an invitation to change the relay origin")
        await self.close()
        self._ws_url, self._ca, self._private_cidrs = ws_url, ca, tuple(private_cidrs)
        # Validate operator configuration before starting any background work.
        _PublicResolver(dns.asyncresolver.Resolver(), self._private_cidrs)
        ssl.create_default_context(cafile=ca or None)
        self.state.origin, self.state.enabled = origin, True
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
        self._state = "disconnected"

    def _clear_connection(self):
        self._ws = None
        self._session_id = ""
        self._peers.clear()
        self._replay.clear()
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(RelayError("relay disconnected"))
        self._pending.clear()

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
                self._error = str(exc) if isinstance(exc, RelayError) else "relay transport unavailable"
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
                ws = await session.ws_connect(
                    self._ws_url,
                    heartbeat=20,
                    max_msg_size=MAX_FRAME,
                    compress=0,
                    timeout=aiohttp.ClientWSTimeout(ws_close=3),
                )
                self._ws = ws
                challenge = await self._receive_frame(ws)
                if (
                    set(challenge) != {"type", "protocol", "origin", "nonce"}
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
                if registered != {
                    "type": "registered",
                    "protocol": PROTOCOL,
                    "key": self.public_key,
                    "session": self._session_id,
                }:
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
            set(frame) != {"type", "peers"}
            or frame["type"] != "peers"
            or not isinstance(frame["peers"], list)
            or len(frame["peers"]) > MAX_PEERS
        ):
            raise RelayError("invalid peer snapshot")
        peers = {}
        for peer in frame["peers"]:
            if not isinstance(peer, dict) or set(peer) != {"key", "session"}:
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
        for request_id, (key, future) in tuple(self._pending.items()):
            if peers.get(key) != self._peers.get(key):
                self._pending.pop(request_id, None)
                if not future.done():
                    future.set_exception(RelayError("peer went offline or changed session"))
        self._peers = peers

    async def _send_frame(self, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > MAX_FRAME:
            raise RelayError("relay frame too large")
        async with self._send_lock:
            now = time.monotonic()
            self._tokens = min(20.0, self._tokens + (now - self._token_time) * 8)
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
            "room": hashlib.sha256(bytes.fromhex(self.state.room)).hexdigest(),
            "id": message_id,
            "sent_at": now,
            "expires_at": now + 30,
            "kind": kind,
            "payload": payload,
        }
        box = Box(self._store.key.to_curve25519_private_key(), VerifyKey(bytes.fromhex(key)).to_curve25519_public_key())
        ciphertext = base64.b64encode(box.encrypt(json.dumps(envelope, separators=(",", ":")).encode())).decode()
        if len(ciphertext) > MAX_CIPHERTEXT:
            raise RelayError("ciphertext size limit exceeded")
        await self._send_frame({"type": "send", "to": key, "id": message_id, "ciphertext": ciphertext})

    async def _handle_frame(self, frame):
        if frame.get("type") == "peers":
            self._set_peers(frame)
        elif frame.get("type") == "error":
            # An unauthenticated transport status can fail a request, but
            # only a decrypted, correlated pong can complete it successfully.
            code = frame.get("code")
            request_id = frame.get("id")
            if (
                set(frame) not in ({"type", "code"}, {"type", "code", "id"})
                or not isinstance(code, str)
                or not code.isascii()
                or not code.replace("_", "").isalnum()
                or len(code) > 64
                or ("id" in frame and (not isinstance(request_id, str) or not ID.fullmatch(request_id)))
            ):
                raise RelayError("invalid relay error frame")
            pending = self._pending.get(request_id)
            if pending and not pending[1].done():
                pending[1].set_exception(RelayError("relay transport error: " + code))
        elif frame.get("type") == "message":
            try:
                await self._receive_encrypted(frame)
            except (RelayError, CryptoError, ValueError, TypeError):
                self._counts["rejected_messages"] += 1
        else:
            raise RelayError("unsupported relay frame")

    async def _receive_encrypted(self, frame):
        if set(frame) != {"type", "from", "session", "id", "ciphertext"}:
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
        box = Box(self._store.key.to_curve25519_private_key(), VerifyKey(bytes.fromhex(key)).to_curve25519_public_key())
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
            set(body) != fields
            or type(body["v"]) is not int
            or body["v"] != 1
            or body["from"] != key
            or body["to"] != self.public_key
            or body["from_session"] != frame["session"]
            or body["to_session"] != self._session_id
            or body["room"] != hashlib.sha256(bytes.fromhex(self.state.room)).hexdigest()
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
        self._replay = {key: expiry for key, expiry in self._replay.items() if expiry >= now}
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
            if not isinstance(payload, dict) or set(payload) != {"reply_to", "label", "workspace_id"}:
                raise RelayError("invalid pong")
            if (
                not isinstance(payload["label"], str)
                or not 1 <= len(payload["label"]) <= 80
                or any(ord(c) < 32 or ord(c) == 127 for c in payload["label"])
            ):
                raise RelayError("invalid pong label")
            if not isinstance(payload["workspace_id"], str) or not ID.fullmatch(payload["workspace_id"]):
                raise RelayError("invalid pong workspace identity")
            if not isinstance(payload["reply_to"], str) or not ID.fullmatch(payload["reply_to"]):
                raise RelayError("invalid pong correlation")
            pending = self._pending.get(payload["reply_to"])
            if pending and pending[0] == key and not pending[1].done():
                pending[1].set_result(payload)
                self._counts["received_pongs"] += 1
        else:
            raise RelayError("unsupported encrypted message kind")
