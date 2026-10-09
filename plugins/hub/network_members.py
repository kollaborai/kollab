"""How approval spreads to every device on a network.

After a join by code each device approves only whoever admitted it, so B and C
on A's network never approve each other and the mesh has no route between them.
Every device therefore tells the members it approved, signed, which devices it
approves and which it revoked. A member approves a device that a member it
already approved names, on the same network (docs/specs/agent-network-simple-flow.md
sections 4 and 10). The rules live in `RelayClient.accept_membership`; this file
is the signed wire format and the loop that sends it.

A list is dated: `issued_at` rises with every list a device sends, and the
receiver keeps the highest it took from each voucher (`RelayState.members_seen`),
so a late or replayed older list is refused instead of revoking the members the
voucher named since.

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
from .relay_state import MAX_REVOKED, MAX_STAMP, RelayError, failure_text, validate_public_key

logger = logging.getLogger(__name__)

METHOD = "network_members"
POLL_SECONDS = 10.0
REQUEST_SECONDS = 15.0
MAX_RETRY_SECONDS = 300.0
# ponytail: one message per list; 64 members is about 11 KB of the 24 KB frame.
# Past that, send the list in pages.
MAX_LISTED = 64
# Signed fields are fixed per version (docs/specs/agent-public-beacon.md, Versioning):
# version 2 adds `issued_at`; version 1 is what 0.13 and 0.14 read. Retire version 1
# (the `1` in `_send`, `_v1_only`, and the undated branches of `open_members` and
# `_take`) two releases or 90 days after the first release that ships version 2,
# whichever is later.
_DOMAIN = {1: b"kollab-network-members-v1\n", 2: b"kollab-network-members-v2\n"}
_FIELDS = {1: frozenset({"v", "voucher", "to", "net", "members", "revoked", "sig"})}
_FIELDS[2] = _FIELDS[1] | {"issued_at"}
_ENTRY = frozenset({"key", "name"})


def _signed_bytes(body: dict) -> bytes:
    return _DOMAIN[body["v"]] + json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def seal_members(
    *,
    key: SigningKey,
    to: str,
    net: str,
    members: list[tuple[str, str]],
    revoked: list[str],
    issued_at: int | None = None,
) -> dict:
    """The signed list one device sends one member: dated (v2) with `issued_at`, else v1."""
    body = {
        "v": 1 if issued_at is None else 2,
        "voucher": key.verify_key.encode().hex(),
        "to": to,
        "net": net,
        "members": [{"key": k, "name": name} for k, name in members[:MAX_LISTED]],
        "revoked": list(revoked[:MAX_REVOKED]),
    }
    if issued_at is not None:
        body["issued_at"] = issued_at
    return {**body, "sig": key.sign(_signed_bytes(body)).signature.hex()}


def open_members(
    payload: object, *, peer: str, own_key: str, net: str
) -> tuple[list[tuple[str, str]], list[str], int | None]:
    """Check a list came from `peer`, for this device, on this network, unaltered.

    Returns the members, the revoked and `issued_at` (None for an undated v1 list).
    Raises RelayError for anything else: a wrong shape, a list signed by another
    key, one meant for another device or network, a bad signature.
    """
    version = payload.get("v") if isinstance(payload, dict) else None
    if type(version) is not int or version not in _FIELDS or not payload.keys() >= _FIELDS[version]:
        raise RelayError("invalid membership list")
    if payload["voucher"] != peer or payload["to"] != own_key or payload["net"] != net:
        raise RelayError("invalid membership list")
    members, revoked, sig = payload["members"], payload["revoked"], payload["sig"]
    issued_at = payload["issued_at"] if version == 2 else None
    if (
        not isinstance(members, list)
        or len(members) > MAX_LISTED
        or not isinstance(revoked, list)
        or len(revoked) > MAX_REVOKED
        or not isinstance(sig, str)
        or (version == 2 and (type(issued_at) is not int or not 0 < issued_at <= MAX_STAMP))
    ):
        raise RelayError("invalid membership list")
    entries: list[tuple[str, str]] = []
    for item in members:
        if (
            not isinstance(item, dict)
            or not item.keys() >= _ENTRY
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
    return entries, list(revoked), issued_at


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
        # member key -> the relay session that refused v2 and took v1
        self._v1_only: dict[str, str] = {}
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
        issued_at = 0
        for key in client.members():
            session = online.get(key)
            if (
                session is None
                or self._delivered.get(key) == (session, digest)
                or time.monotonic() < self._retry_at.get(key, 0.0)
            ):
                continue
            listed = [entry for entry in members if entry[0] != key]
            issued_at = issued_at or self._stamp()  # one a tick, above every earlier one
            try:
                await self._send(key, session, listed, revoked, issued_at)
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

    async def _send(
        self,
        key: str,
        session: str,
        listed: list[tuple[str, str]],
        revoked: list[str],
        issued_at: int,
    ) -> None:
        """Offer the dated list; a member that refuses it may predate it, so try v1.

        A member that took only v1 is offered v1 alone while its relay session
        lasts; an upgrade restarts it, which is a new session. Raises when
        neither version is taken (or the transport fails).
        """
        client = self._client
        known = self._v1_only.get(key) == session
        for version in ((1,) if known else (2, 1)):
            payload = seal_members(
                key=client._store.key,
                to=key,
                net=client.network_id(),
                members=listed,
                revoked=revoked,
                issued_at=issued_at if version == 2 else None,
            )
            reply = await self._transport.request(
                key, METHOD, payload, timeout=REQUEST_SECONDS
            )
            if reply == {"ok": True}:
                if version == 1 and not known:
                    self._v1_only[key] = session
                return
        raise RelayError("refused")

    def _stamp(self) -> int:
        """The next `issued_at`: the clock, but above the last one sent whatever it says."""
        client = self._client
        client.state.members_issued_at = max(
            int(time.time()), client.state.members_issued_at + 1
        )
        client._adopt_bridge_fields()  # the client saves its whole copy
        client._store.save()  # before the list leaves, so a restart never reuses it
        return client.state.members_issued_at

    def _take(self, peer: str, issued_at: int | None) -> None:
        """Refuse a replayed list, or a v1 one from a voucher that has sent v2.

        A fresh one is noted in memory; the save that applies the list keeps it.
        """
        client = self._client
        if peer not in client.members():
            raise RelayError("peer is not part of this network")
        seen = client.state.members_seen
        if issued_at is None:
            if peer in seen:
                raise RelayError("undated list from a voucher that sends dated ones")
            return
        if issued_at <= seen.get(peer, 0):
            raise RelayError("list is not newer than the last one taken")
        approved = set(client.state.approvals)  # a member that left keeps no mark
        client.state.members_seen = {
            key: mark for key, mark in seen.items() if key in approved
        } | {peer: issued_at}

    async def receive(self, peer: str, payload: dict) -> dict:
        """One list from a member; the reply says only whether it was taken."""
        client = self._client
        try:
            members, revoked, issued_at = open_members(
                payload, peer=peer, own_key=client.public_key, net=client.network_id()
            )
            self._take(peer, issued_at)
            client.accept_membership(peer, members, revoked)
        except RelayError as error:
            logger.info("network members: refused a list (%s)", failure_text(error))
            return {"error": "invalid"}
        return {"ok": True}
