"""How approval spreads to every device on a network.

After a join by code each device approves only whoever admitted it, so B and C
on A's network never approve each other and the mesh has no route between them.
Every device therefore tells the members it approved, signed, which devices it
approves and which it revoked. A member approves a device that a member it
already approved names, on the same network (docs/specs/agent-network-simple-flow.md
sections 4 and 10). The rules live in `RelayClient.accept_membership`; this file
is the signed wire format and the loop that sends it.

An accepted stranger (Story 5) is not a member: it is never named, never heard
and never revoked this way. A vouch is an approval, not a grant: no agent
becomes reachable under `agents` or `manual` trust because of it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .device_names import NAME_RE
from .relay_state import MAX_REVOKED, RelayError, validate_public_key

logger = logging.getLogger(__name__)

METHOD = "network_members"
POLL_SECONDS = 10.0
REQUEST_SECONDS = 15.0
MAX_RETRY_SECONDS = 300.0
# ponytail: one message per list; 64 members is about 11 KB of the 24 KB frame.
# Past that, send the list in pages.
MAX_LISTED = 64
_DOMAIN = b"kollab-network-members-v1\n"
_FIELDS = frozenset({"v", "voucher", "to", "net", "members", "revoked", "sig"})
_ENTRY = frozenset({"key", "name"})


def _signed_bytes(body: dict) -> bytes:
    return _DOMAIN + json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def seal_members(
    *,
    key: SigningKey,
    to: str,
    net: str,
    members: list[tuple[str, str]],
    revoked: list[str],
) -> dict:
    """The signed list one device sends one member."""
    body = {
        "v": 1,
        "voucher": key.verify_key.encode().hex(),
        "to": to,
        "net": net,
        "members": [{"key": k, "name": name} for k, name in members[:MAX_LISTED]],
        "revoked": list(revoked[:MAX_REVOKED]),
    }
    return {**body, "sig": key.sign(_signed_bytes(body)).signature.hex()}


def open_members(
    payload: object, *, peer: str, own_key: str, net: str
) -> tuple[list[tuple[str, str]], list[str]]:
    """Check a list came from `peer`, for this device, on this network, unaltered.

    Raises RelayError for anything else: a wrong shape, a list signed by another
    key, one meant for another device or network, a bad signature.
    """
    if not isinstance(payload, dict) or set(payload) != _FIELDS or payload["v"] != 1:
        raise RelayError("invalid membership list")
    if payload["voucher"] != peer or payload["to"] != own_key or payload["net"] != net:
        raise RelayError("invalid membership list")
    members, revoked, sig = payload["members"], payload["revoked"], payload["sig"]
    if (
        not isinstance(members, list)
        or len(members) > MAX_LISTED
        or not isinstance(revoked, list)
        or len(revoked) > MAX_REVOKED
        or not isinstance(sig, str)
    ):
        raise RelayError("invalid membership list")
    entries: list[tuple[str, str]] = []
    for item in members:
        if (
            not isinstance(item, dict)
            or set(item) != _ENTRY
            or not isinstance(item["name"], str)
            or (item["name"] and not NAME_RE.fullmatch(item["name"]))
        ):
            raise RelayError("invalid membership list")
        validate_public_key(item["key"])
        entries.append((item["key"], item["name"]))
    for key in revoked:
        validate_public_key(key)
    body = {name: value for name, value in payload.items() if name != "sig"}
    try:
        VerifyKey(bytes.fromhex(peer)).verify(_signed_bytes(body), bytes.fromhex(sig))
    except (BadSignatureError, ValueError):
        raise RelayError("invalid membership list") from None
    return entries, list(revoked)


class MembershipSync:
    """Sends this device's signed member list to every member online, and hears theirs."""

    def __init__(
        self,
        *,
        client,
        transport,
        online: Callable[[], dict[str, str]],
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._client = client
        self._transport = transport
        self._online = online
        self._poll = poll_seconds
        self._sleep = sleep
        # member key -> (relay session, digest of the list) it last acknowledged
        self._delivered: dict[str, tuple[str, str]] = {}
        self._failures: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="kollab-membership-sync")

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while not self._closed:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("network members: tick failed (%s)", type(error).__name__)
            await self._sleep(self._poll)

    async def tick(self) -> None:
        client = self._client
        members, revoked = client.membership()
        digest = hashlib.sha256(
            json.dumps([members, revoked], sort_keys=True).encode()
        ).hexdigest()
        online = self._online()
        for key in client.members():
            session = online.get(key)
            if (
                session is None
                or self._delivered.get(key) == (session, digest)
                or time.monotonic() < self._retry_at.get(key, 0.0)
            ):
                continue
            listed = [entry for entry in members if entry[0] != key]
            payload = seal_members(
                key=client._store.key,
                to=key,
                net=client.network_id(),
                members=listed,
                revoked=revoked,
            )
            try:
                reply = await self._transport.request(
                    key, METHOD, payload, timeout=REQUEST_SECONDS
                )
                if reply != {"ok": True}:
                    raise RelayError("refused")
            except Exception:
                # The far side may not have approved this device yet; it will
                # once the list that names us reaches it. Try again later.
                failures = self._failures.get(key, 0) + 1
                self._failures[key] = failures
                self._retry_at[key] = time.monotonic() + min(
                    MAX_RETRY_SECONDS, self._poll * 2 ** (failures - 1)
                )
                continue
            self._delivered[key] = (session, digest)
            self._failures.pop(key, None)
            self._retry_at.pop(key, None)

    async def receive(self, peer: str, payload: dict) -> dict:
        """One list from a member; the reply says only whether it was taken."""
        client = self._client
        try:
            members, revoked = open_members(
                payload, peer=peer, own_key=client.public_key, net=client.network_id()
            )
            client.accept_membership(peer, members, revoked)
        except RelayError:
            logger.info("network members: refused a list")
            return {"error": "invalid"}
        return {"ok": True}
