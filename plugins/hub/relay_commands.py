"""Human-operated discovery, pairing and authorized conversation commands."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import secrets
import shlex
import stat
from pathlib import Path
from typing import Any

from .dns.discovery import DiscoveryError, discover, fetch_agent_card, normalize_target
from .dns.discovery_store import DiscoveryStore


class RelayCommands:
    def __init__(self, workspace: Path, *, config: Any = None, state_dir: Path | None = None, agent_bridge=None):
        from .relay_client import RelayClient

        self.client = RelayClient(workspace=workspace, state_dir=state_dir)
        self.config = config
        self.agent_bridge = agent_bridge
        self._lock = asyncio.Lock()
        self._closed = False
        # Public discovery pins remain separate from transport invitations.
        if state_dir is None:
            from .dns.storage import get_dns_dir

            cache_dir = get_dns_dir() / "discovered"
        else:
            cache_dir = self.client.state_dir / "discovered"
        self._cache = DiscoveryStore(cache_dir)

    def _setting(self, name: str, default: Any) -> Any:
        return self.config.get(name, default) if self.config else default

    async def _discover(self, value: str):
        requested = normalize_target(value, document=False)
        ca = self._setting("plugins.hub.endpoint_tls_ca", "") or ""
        scopes = self._setting("plugins.hub.discovery_private_origins", {})
        if not isinstance(scopes, dict):
            raise ValueError("discovery_private_origins must be an origin-to-CIDR mapping")
        cidrs = scopes.get(requested.origin, [])
        if not isinstance(cidrs, list) or any(not isinstance(c, str) for c in cidrs):
            raise ValueError("private discovery scope must be a list of CIDRs")
        is_card = requested.url == requested.origin + "/.well-known/agent-card.json"
        result = await discover(requested.origin if is_card else value, ca=ca, private_cidrs=tuple(cidrs))
        result = await asyncio.to_thread(self._cache.accept, result)
        return result, ca, tuple(cidrs), is_card

    @staticmethod
    def _relay_url(result) -> str | None:
        payload = result.manifest
        control = payload["endpoints"].get("control")
        protocols = payload["coordinator"]["protocols"]
        roles = payload["discovery"]["roles"]
        if "kollab-relay/1" not in protocols or "relay" not in roles or "rendezvous" not in roles:
            return None
        if control != result.origin + "/relay/v1":
            raise ValueError("Unsupported advertised relay control URL")
        return "wss://" + result.origin[len("https://") :] + "/relay/v1/ws"

    def format_status(self) -> str:
        state = self.client.status()
        lines = [
            f"beacon: {state['state']}",
            f"address: {state['origin'] or 'not configured'}",
            f"your public key: {state['key']}",
            f"online peers: {state['peers']}; approved keys: {state['approved_peers']}",
            f"reconnect on launch: {'enabled' if state['enabled'] else 'disabled'}",
        ]
        if state.get("error"):
            lines.append("connection issue: " + state["error"])
        lines.append("Private room key-presence only; workspace tools remain unauthorized.")
        return "\n".join(lines)

    async def _attach(self, result, ca: str, cidrs: tuple[str, ...]) -> str:
        ws_url = self._relay_url(result)
        if not ws_url:
            return result.summary() + "\nBeacon: no relay service advertised; no connection opened"
        await self.client.connect(result.origin, ws_url=ws_url, ca=ca, private_cidrs=cidrs)
        return (
            self.format_status()
            + "\nNext: /connect invite, privately copy that file, then /connect join <file> on the second computer."
        )

    async def resume(self) -> None:
        delay = 1.0
        while not self._closed and self.client.state.enabled and self.client.state.origin:
            try:
                async with self._lock:
                    # An operator may have connected while discovery was
                    # backing off. The transport owns subsequent retries.
                    if self.client.status()["state"] in {"online", "connecting", "reconnecting"}:
                        return
                    if not self.client.state.enabled:
                        return
                    result, ca, cidrs, _ = await self._discover(self.client.state.origin)
                    if self._relay_url(result):
                        await self._attach(result, ca, cidrs)
                        return
            except (DiscoveryError, OSError, ValueError, TimeoutError):
                # Keep the app usable while offline. Never discard the
                # publisher pin or join a different room to repair a route.
                pass
            await asyncio.sleep(delay + secrets.randbelow(1000) / 1000)
            delay = min(delay * 2, 60.0)

    async def close(self) -> None:
        self._closed = True
        await self.client.close()

    async def run(self, value: str, *, source_agent=None) -> str:
        if not isinstance(value, str) or len(value) > 4096:
            return "connect: command is too large"
        value = value.strip()
        async with self._lock:
            try:
                return await self._run(value, source_agent=source_agent)
            except (DiscoveryError, OSError, ValueError, TimeoutError) as exc:
                # Client errors must never include invite/private state material.
                if value.partition(" ")[0] == "join":
                    return self._join_error(exc)
                return f"connect: {exc}"

    @staticmethod
    def _join_error(exc: Exception) -> str:
        """Use fixed diagnostics; never echo a token, path, or raw exception."""
        if isinstance(exc, DiscoveryError):
            hint = {
                "expired": "the relay discovery document has expired; the publisher must renew it",
                "key_changed": "the publisher key changed; verify it with the operator before re-pairing",
                "rollback": "the publisher revision is older than the trusted revision",
                "revision_conflict": "the publisher reused a revision with different content",
                "key_conflict": "DNS and the discovery document disagree about the publisher key",
                "cache_unavailable": "the verified publisher identity could not be saved locally",
                "address_denied": "the relay address is outside the configured discovery scope",
                "dns_unavailable": "the relay domain could not be resolved",
                "timeout": "the relay discovery request timed out; retry when reachable",
                "unavailable": "relay discovery is unavailable; check network and TLS configuration",
            }.get(
                exc.code, "relay discovery failed verification; check the publisher and local discovery configuration"
            )
            return "connect: " + hint
        if isinstance(exc, TimeoutError):
            return "connect: relay connection timed out; use /connect status to inspect reconnect state"
        if isinstance(exc, FileNotFoundError):
            return "connect: a required local file was not found; use the invitation's path on this computer"
        if isinstance(exc, PermissionError):
            return "connect: local file access was denied; check invitation and relay-state ownership and permissions"
        if isinstance(exc, OSError):
            if exc.errno == errno.ELOOP:
                return "connect: invitation must be a regular file, not a symbolic link"
            return "connect: could not read the invitation or save local relay state"
        hint = {
            "cannot join your own invitation": (
                "this invitation belongs to this workspace; join it from the other computer's Kollab session"
            ),
            "invitation must be an owned private regular file": (
                "invitation must be a regular file owned by this user with private permissions; "
                "run chmod 600 on the receiving file"
            ),
            "invitation file is too large": "invitation exceeds the 4096-byte limit; transfer the original file again",
            "invalid invitation path quoting": (
                "provide one invitation file path; use matching quotes around a path containing spaces"
            ),
            "Unsupported advertised relay control URL": "the publisher advertises an unsupported relay endpoint",
        }.get(
            str(exc),
            "invalid invitation or relay configuration; "
            "transfer the original invitation file and check /connect status",
        )
        return "connect: " + hint

    async def _run(self, value: str, *, source_agent=None) -> str:
        from .relay_client import parse_invite

        head, _, rest = value.partition(" ")
        rest = rest.strip()
        if head in {"allow", "deny", "grants", "agents", "authorize", "withdraw", "send", "task", "cancel"}:
            if self.agent_bridge is None:
                return "connect: agent conversations require a running Kollab Hub session"
            return await self.agent_bridge.application_command(head, rest, source_agent=source_agent)
        if head == "status":
            return self.format_status()
        if head == "peers":
            peers = self.client.peers()
            if not peers:
                return "beacon: no other peers currently online in this invitation room"
            return "beacon peers (routing visibility only):\n" + "\n".join(
                peer["key"] + ("  approved" if peer["approved"] else "  pending local approval") for peer in peers
            )
        if head == "invite":
            # Command events/history must never receive the bearer capability.
            # Only a private file path reaches display and command hooks.
            path = self.client.state_dir / ("invite-" + secrets.token_hex(4) + ".txt")
            token = self.client.invite()
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(token + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return (
                f"Private invitation saved: {path}\n"
                "Copy it privately to the other computer, then /connect join <local file path>."
            )
        if head in {"approve", "revoke"}:
            if not rest:
                return f"usage: /connect {head} <full 64-hex peer public key>"
            getattr(self.client, head)(rest)
            if head == "revoke" and self.agent_bridge is not None:
                self.agent_bridge._state()
                self.agent_bridge.store.revoke(self.client.state.room, rest)
            return (
                f"peer presence {'approved' if head == 'approve' else 'revoked'}: {rest}\n"
                "Workspace tool permissions are separate."
            )
        if head == "ping":
            if not rest:
                return "usage: /connect ping <full 64-hex peer public key>"
            reply = await self.client.ping(rest)
            return "encrypted peer response: " + json.dumps(reply, sort_keys=True)
        if head == "disconnect":
            await self.client.close(disable=True)
            return "beacon disconnected; automatic reconnect disabled for this workspace"
        if head == "rotate":
            origin = self.client.state.origin
            if not origin:
                return "connect to a beacon before rotating its room"
            result, ca, cidrs, _ = await self._discover(origin)
            if not self._relay_url(result):
                return "beacon: relay not currently advertised; room unchanged"
            await self.client.close()
            self.client.rotate_room()
            return await self._attach(result, ca, cidrs)
        if head == "join":
            if rest.startswith(("'", '"')):
                try:
                    paths = shlex.split(rest)
                except ValueError:
                    raise ValueError("invalid invitation path quoting") from None
                if len(paths) != 1:
                    raise ValueError("invalid invitation path quoting")
                rest = paths[0]
            if not rest or rest.startswith("kollab-invite-"):
                return "usage: /connect join <private invitation file path>; do not paste invitation tokens into chat"
            descriptor = os.open(Path(rest).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError("invitation must be an owned private regular file")
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError("invitation file is too large")
            token = raw.decode("ascii").strip()
            parsed = parse_invite(token)
            # Reject before discovery or closing the existing connection.
            if parsed["key"] == self.client.public_key:
                raise ValueError("cannot join your own invitation")
            result, ca, cidrs, _ = await self._discover(parsed["origin"])
            if not self._relay_url(result):
                return "beacon: invitation origin does not advertise this relay protocol"
            await self.client.close()
            self.client.join_invite(token)
            return await self._attach(result, ca, cidrs)
        if head == "help":
            return (
                "/connect [domain] | status | peers | invite | join <private file>\n"
                "/connect approve|revoke|ping <peer public key> | rotate | disconnect\n"
                "/connect agents [local|peer public key] | grants\n"
                "/connect allow <peer public key> <local agent name>\n"
                "/connect deny <peer public key> [local agent name]\n"
                "/connect authorize <full relay agent address> <human task purpose>\n"
                "/connect withdraw <communication grant id>\n"
                "/connect send <full relay agent address> <message>\n"
                "/connect task|cancel <full relay agent address> <message id>"
            )
        value = value or self.client.state.origin or "https://kollabor.ai"
        result, ca, cidrs, is_card = await self._discover(value)
        if is_card:
            card = await fetch_agent_card(result, ca=ca, private_cidrs=cidrs)
            return result.summary() + (
                "\nAgent Card: verified (JWS EdDSA, pinned locator key); no task submitted"
                if card
                else "\nAgent Card: not advertised by this publisher"
            )
        return await self._attach(result, ca, cidrs)
