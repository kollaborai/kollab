"""Human-operated discovery, pairing and authorized conversation commands."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import secrets
import shlex
import stat
from pathlib import Path
from typing import Any

from .dns.discovery import DiscoveryError, discover, fetch_agent_card, normalize_target
from .dns.discovery_store import DiscoveryStore

MAX_VISIBLE_ENROLLMENT_REQUESTS = 24
MAX_VISIBLE_ENROLLMENT_SCOPE_ITEMS = 4


class RelayCommands:
    def __init__(
        self,
        workspace: Path,
        *,
        config: Any = None,
        state_dir: Path | None = None,
        agent_bridge=None,
    ):
        from .relay_client import RelayClient

        self.client = RelayClient(workspace=workspace, state_dir=state_dir)
        self.config = config
        self.agent_bridge = agent_bridge
        self._lock = asyncio.Lock()
        self._closed = False
        self._contact_manager = None
        # Public discovery pins remain separate from transport invitations.
        if state_dir is None:
            from .dns.storage import get_dns_dir

            cache_dir = get_dns_dir() / "discovered"
        else:
            cache_dir = self.client.state_dir / "discovered"
        self._cache = DiscoveryStore(cache_dir)

    def _setting(self, name: str, default: Any) -> Any:
        return self.config.get(name, default) if self.config else default

    @staticmethod
    def _resolve_network_target(value: str) -> str:
        """Resolve an installed network ID to its approved discovery domain."""
        from kollabor_config.provisioned_state import ProvisionedStateFile

        domain = ProvisionedStateFile().get_network_preferences().get(value)
        return f"https://{domain}" if domain is not None else value

    @staticmethod
    def _format_networks() -> str:
        from kollabor_config.provisioned_state import ProvisionedStateFile

        networks = ProvisionedStateFile().get_network_preferences()
        if not networks:
            return "connect: no provisioned networks"
        return "provisioned networks:\n" + "\n".join(
            f"  {network_id}: {domain}"
            for network_id, domain in sorted(networks.items())
        )

    async def _discover(self, value: str):
        value = self._resolve_network_target(value)
        requested = normalize_target(value, document=False)
        ca = self._setting("plugins.hub.endpoint_tls_ca", "") or ""
        scopes = self._setting("plugins.hub.discovery_private_origins", {})
        if not isinstance(scopes, dict):
            raise ValueError(
                "discovery_private_origins must be an origin-to-CIDR mapping"
            )
        cidrs = scopes.get(requested.origin, [])
        if not isinstance(cidrs, list) or any(not isinstance(c, str) for c in cidrs):
            raise ValueError("private discovery scope must be a list of CIDRs")
        is_card = requested.url == requested.origin + "/.well-known/agent-card.json"
        result = await discover(
            requested.origin if is_card else value, ca=ca, private_cidrs=tuple(cidrs)
        )
        result = await asyncio.to_thread(self._cache.accept, result)
        return result, ca, tuple(cidrs), is_card

    @staticmethod
    def _relay_url(result) -> str | None:
        payload = result.manifest
        control = payload["endpoints"].get("control")
        protocols = payload["coordinator"]["protocols"]
        roles = payload["discovery"]["roles"]
        if (
            "kollab-relay/1" not in protocols
            or "relay" not in roles
            or "rendezvous" not in roles
        ):
            return None
        if control != result.origin + "/relay/v1":
            raise ValueError("Unsupported advertised relay control URL")
        return "wss://" + result.origin[len("https://") :] + "/relay/v1/ws"

    def format_status(self) -> str:
        state = self.client.status()
        identity = getattr(self.agent_bridge, "identity", None)
        agent_identity = getattr(identity, "identity", "unavailable")
        agent_id = getattr(identity, "agent_id", "unavailable")
        lines = [
            f"beacon: {state['state']}",
            f"address: {state['origin'] or 'not configured'}",
            f"your public key: {state['key']}",
            f"workspace id: {state['workspace_id']}",
            f"workspace path: {self.client.workspace}",
            f"agent identity: {agent_identity}",
            f"agent id: {agent_id}",
            f"online peers: {state['peers']}; approved keys: {state['approved_peers']}",
            f"reconnect on launch: {'enabled' if state['enabled'] else 'disabled'}",
        ]
        if state.get("error"):
            lines.append("connection issue: " + state["error"])
        issuer = getattr(self.agent_bridge, "_enrollment_issuer", None)
        if issuer is not None:
            recovery = issuer.destination_recovery_status()
            if recovery and recovery["pending"]:
                detail = f"device enrollment recovery: {recovery['pending']} pending"
                source_counts = []
                if recovery.get("issuer_pending"):
                    source_counts.append(f"{recovery['issuer_pending']} issuer")
                if recovery.get("destination_pending"):
                    source_counts.append(
                        f"{recovery['destination_pending']} destination"
                    )
                if source_counts:
                    detail += " (" + ", ".join(source_counts) + ")"
                if recovery["active"]:
                    detail += f", {recovery['active']} running"
                if recovery["last_error_code"]:
                    detail += f"; last issue: {recovery['last_error_code']}"
                if recovery["retry_in_seconds"]:
                    detail += f"; retry in {recovery['retry_in_seconds']}s"
                lines.append(detail)
        lines.append(
            "Private room; conversation grants and receiving workspace permissions are separate."
        )
        return "\n".join(lines)

    async def _attach(self, result, ca: str, cidrs: tuple[str, ...]) -> str:
        ws_url = self._relay_url(result)
        if not ws_url:
            return (
                result.summary()
                + "\nBeacon: no relay service advertised; no connection opened"
            )
        await self.client.connect(
            result.origin, ws_url=ws_url, ca=ca, private_cidrs=cidrs
        )
        return (
            self.format_status()
            + "\nNext: /connect offer shows a code; enter it with /connect on the other device."
        )

    def _contacts(self):
        if self._contact_manager is None:
            from .contact_requests import ContactRequestManager

            self._contact_manager = ContactRequestManager(self)
        return self._contact_manager

    async def submit_contact_request(
        self, domain: str, recipient_key: str, introduction: str
    ) -> str:
        return await self._contacts().submit(domain, recipient_key, introduction)

    async def pending_contact_requests(self, domain: str):
        return await self._contacts().pending(domain)

    async def decide_contact_request(
        self, domain: str, request_id: str, decision: str
    ):
        return await self._contacts().decide(domain, request_id, decision)

    async def resume(self) -> None:
        delay = 1.0
        while (
            not self._closed and self.client.state.enabled and self.client.state.origin
        ):
            try:
                async with self._lock:
                    # An operator may have connected while discovery was
                    # backing off. The transport owns subsequent retries.
                    if self.client.status()["state"] in {
                        "online",
                        "connecting",
                        "reconnecting",
                    }:
                        return
                    if not self.client.state.enabled:
                        return
                    result, ca, cidrs, _ = await self._discover(
                        self.client.state.origin
                    )
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
                exc.code,
                "relay discovery failed verification; check the publisher and local discovery configuration",
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
        if head == "requests":
            if rest:
                return "usage: /connect requests"
            if self.agent_bridge is None:
                return "connect: local enrollment issuer is unavailable"
            requests = self.agent_bridge.pending_enrollment_requests(
                source_agent=source_agent
            )
            return self._format_enrollment_requests(requests)
        if head in {"accept", "reject"}:
            fields = rest.split()
            if len(fields) != 1 or not re.fullmatch(r"[0-9a-f]{32}", fields[0]):
                return f"usage: /connect {head} <32-hex receipt-id>"
            if self.agent_bridge is None:
                return "connect: local enrollment issuer is unavailable"
            decision = "accept" if head == "accept" else "reject"
            result = await self.agent_bridge.decide_enrollment_request(
                fields[0], decision=decision, source_agent=source_agent
            )
            return f"enrollment {result['status']}; receipt: {result['receipt_id']}"
        if head == "contact-point":
            fields = rest.split()
            if len(fields) > 1:
                return "usage: /connect contact-point <relay-domain>"
            domain = fields[0] if fields else self.client.state.origin or "kollabor.ai"
            try:
                point = await self._contacts().contact_point(domain)
            except Exception:
                return "connect: contact point is unavailable"
            return (
                f"contact route: {point['origin']} {point['identity']} "
                "(share this route out of band)"
            )
        if head in {
            "allow",
            "deny",
            "grants",
            "agents",
            "authorize",
            "withdraw",
            "send",
            "task",
            "cancel",
            "answer",
        }:
            if self.agent_bridge is None:
                return (
                    "connect: agent conversations require a running Kollab Hub session"
                )
            return await self.agent_bridge.application_command(
                head, rest, source_agent=source_agent
            )
        if head == "status":
            return self.format_status()
        if head == "networks":
            if rest:
                return "usage: /connect networks"
            return self._format_networks()
        if head == "peers":
            peers = self.client.peers()
            if not peers:
                return "beacon: no other peers currently online in this invitation room"
            return "beacon peers (routing visibility only):\n" + "\n".join(
                peer["key"]
                + ("  approved" if peer["approved"] else "  pending local approval")
                for peer in peers
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
            return (
                "beacon disconnected; automatic reconnect disabled for this workspace"
            )
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
            descriptor = os.open(
                Path(rest).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
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
                return (
                    "beacon: invitation origin does not advertise this relay protocol"
                )
            await self.client.close()
            self.client.join_invite(token)
            return await self._attach(result, ca, cidrs)
        if head == "help":
            from .plugin import format_connect_help

            return format_connect_help()
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

    @staticmethod
    def _format_enrollment_requests(requests) -> str:
        if not requests:
            return "connect: no pending enrollment requests"
        visible = requests[:MAX_VISIBLE_ENROLLMENT_REQUESTS]
        lines = [f"pending enrollment requests ({len(requests)}):"]
        for request in visible:
            networks = request.network_ids[:MAX_VISIBLE_ENROLLMENT_SCOPE_ITEMS]
            categories = request.credential_categories[
                :MAX_VISIBLE_ENROLLMENT_SCOPE_ITEMS
            ]
            network_text = ", ".join(networks) or "none"
            if len(request.network_ids) > len(networks):
                network_text += f", +{len(request.network_ids) - len(networks)} more"
            category_text = ", ".join(categories) or "none"
            if len(request.credential_categories) > len(categories):
                category_text += (
                    f", +{len(request.credential_categories) - len(categories)} more"
                )
            profile = request.profile_summary or request.configuration_profile or "none"
            availability = (
                "available"
                if request.decision_available
                else "unavailable in this process"
            )
            lines.extend(
                (
                    f"  receipt: {request.enrollment_id}",
                    "  device fingerprint: sha256:"
                    + request.device_key_fingerprint[:16]
                    + "…",
                    f"  destination workspace: {request.workspace_id or 'unbound'}",
                    f"  issuer: {request.issuer}",
                    f"  networks: {network_text}",
                    f"  profile: {profile}; categories: {category_text}",
                    f"  allowance remaining: {request.remaining_new_devices}; "
                    f"expires: {request.expires_at}; decision: {availability}",
                )
            )
            if any(category.startswith("provider:") for category in categories):
                lines.append(
                    "  provider credentials will be copied to this device; "
                    "network revocation does not revoke them at the provider"
                )
        if len(requests) > len(visible):
            lines.append(
                f"  {len(requests) - len(visible)} additional request(s) omitted"
            )
        lines.append(
            "Accept or reject by receipt ID; code, proof and membership details are hidden."
        )
        return "\n".join(lines)
