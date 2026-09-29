"""Human-operated discovery, pairing and authorized conversation commands."""

from __future__ import annotations

import asyncio
import errno
import inspect
import re
import secrets
from pathlib import Path
from typing import Any

from .device_names import DEFAULT_TRUST, format_handle, validate_device_name, validate_trust
from .dns.discovery import DiscoveryError, discover, fetch_agent_card, normalize_target
from .dns.discovery_store import DiscoveryStore

MAX_VISIBLE_ENROLLMENT_REQUESTS = 24
MAX_VISIBLE_ENROLLMENT_SCOPE_ITEMS = 4


# Only valid under trust manual (docs/specs/agent-network-simple-flow.md section 6).
MANUAL_TRUST_COMMANDS = frozenset({"authorize", "send", "withdraw", "answer", "task", "cancel"})


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

    def _network_name(self, domain: str) -> str:
        """The network's human name when available, else its domain."""
        getter = getattr(self.agent_bridge, "network_name", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return value
            except Exception:
                pass
        return domain

    def _trust_level(self) -> str:
        getter = getattr(self.agent_bridge, "trust_level", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return value
            except Exception:
                pass
        return DEFAULT_TRUST

    def _device_name(self) -> str:
        getter = getattr(self.agent_bridge, "device_name", None)
        if callable(getter):
            try:
                return getter() or ""
            except Exception:
                pass
        return ""

    def _trust_level(self) -> str:
        getter = getattr(self.agent_bridge, "trust_level", None)
        try:
            level = getter() if callable(getter) else DEFAULT_TRUST
        except Exception:
            level = DEFAULT_TRUST
        return level if isinstance(level, str) and level else DEFAULT_TRUST

    def _pending_request_lines(self) -> list[str]:
        """`requests` lines for the status screen: who wants to join, by name."""
        getter = getattr(self.agent_bridge, "pending_enrollment_requests", None)
        if not callable(getter):
            return []
        try:
            rows = list(getter())
        except Exception:
            return []
        if not rows:
            return ["requests none"]
        lines = ["requests"]
        for row in rows:
            name = getattr(row, "device_name", "") or getattr(row, "enrollment_id", "")[:8]
            fingerprint = getattr(row, "device_key_fingerprint", "")[:12]
            lines.append(
                f"  {name} wants to join   fingerprint {fingerprint}   /connect accept {name}"
            )
        return lines

    async def _remote_rows(self) -> list:
        """Rows from the relay bridge's remote_agents(), degrading to none."""
        getter = getattr(self.agent_bridge, "remote_agents", None)
        if not callable(getter):
            return []
        try:
            rows = getter()
            if inspect.isawaitable(rows):
                rows = await rows
        except Exception:
            return []
        if not isinstance(rows, (list, tuple)):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _resolve_peer_key(self, token: str) -> str:
        """A 64-hex peer key is returned unchanged; a device name resolves to it."""
        if re.fullmatch(r"[0-9a-f]{64}", token):
            return token
        for row in await self._remote_rows():
            if row.get("device") == token and row.get("address"):
                try:
                    from .relay_conversations import RelayAddress

                    return RelayAddress.parse(row["address"]).key
                except Exception:
                    continue
        raise ValueError(f"no known device matches '{token}'; use its 64-hex peer key")

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

    async def format_status(self, *, show_keys: bool = False) -> str:
        """`/connect status`, redesigned per the constitution (sections 5-6).

        Names, not keys, appear here. ``show_keys`` appends the old
        technical block (public key, workspace id, peer counts) for
        operators who explicitly ask with ``/connect status keys``.
        """
        import hashlib

        state = self.client.status()
        origin = state["origin"] or ""
        domain = origin[len("https://") :] if origin.startswith("https://") else origin

        lines = []
        if domain:
            trust = self._trust_level()
            lines.append(f"network {self._network_name(domain)} via {domain}  trust: {trust}")
        else:
            lines.append("network: none")

        device_name = self._device_name()
        if device_name:
            lines.append(f"this device {device_name}")
        try:
            provisioned = self._format_networks()
        except Exception:
            provisioned = ""
        if provisioned.startswith("provisioned networks:"):
            lines.append(provisioned)

        if domain:
            fingerprint = hashlib.sha256(
                b"kollab-contact-route-v1\0" + bytes.fromhex(state["key"])
            ).hexdigest()[:8]
            lines.append(f"contact route {domain}/c/{fingerprint}")
        else:
            lines.append("contact route none")

        lines.append("online")
        remote_rows = await self._remote_rows()
        plugin = getattr(self.agent_bridge, "plugin", None)
        presence = getattr(plugin, "_presence", None)
        local_agents = []
        if presence is not None:
            try:
                local_agents = list(presence.get_cached_agents())
            except Exception:
                local_agents = []
        own_identity = getattr(plugin, "_identity", None)
        if own_identity is not None and not any(
            getattr(a, "agent_id", None) == getattr(own_identity, "agent_id", None)
            for a in local_agents
        ):
            local_agents = [own_identity] + local_agents
        for agent in local_agents:
            name = getattr(agent, "identity", None)
            if name:
                lines.append(f"  {name} (this device)")
        for row in remote_rows:
            if not row.get("online"):
                continue
            handle = row.get("handle") or format_handle(
                row.get("name", "?"), row.get("device", "?")
            )
            lines.append(f"  {handle} - {row.get('state', 'unknown')}")
        lines.extend(self._pending_request_lines())

        offline_devices = sorted(
            {
                row.get("device")
                for row in remote_rows
                if not row.get("online") and row.get("device")
            }
        )
        if offline_devices:
            lines.append(f"offline devices: {', '.join(offline_devices)}")

        lines.append("join code: run /connect code")

        if state.get("error"):
            lines.append("connection issue: " + state["error"])
        if domain and not state["enabled"]:
            lines.append("reconnect on launch: disabled")

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

        if show_keys:
            agent_id = getattr(own_identity, "agent_id", "unavailable")
            lines.append("")
            lines.append(f"your public key: {state['key']}")
            lines.append(f"workspace id: {state['workspace_id']}")
            lines.append(f"workspace path: {self.client.workspace}")
            lines.append(f"agent id: {agent_id}")
            lines.append(
                f"online peers: {state['peers']}; approved keys: {state['approved_peers']}"
            )
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
            await self.format_status()
            + "\nNext: /connect code shows a code; enter it with /connect on the other device."
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

        head, _, rest = value.partition(" ")
        rest = rest.strip()
        if head in MANUAL_TRUST_COMMANDS:
            level = self._trust_level()
            if level != "manual":
                return (
                    f"connect: {head} is for trust manual; this network is {level}, "
                    "so just message the agent with hub_msg. "
                    "Run /connect trust manual to require a human on every message."
                )
        if head in {"accept", "reject"}:
            fields = rest.split()
            if len(fields) != 1:
                return f"usage: /connect {head} <device-name-or-receipt>"
            if self.agent_bridge is None:
                return "connect: local enrollment issuer is unavailable"
            token = fields[0]
            receipt_id = token if re.fullmatch(r"[0-9a-f]{32}", token) else None
            if receipt_id is None:
                is_hex_prefix = len(token) >= 8 and re.fullmatch(r"[0-9a-f]+", token)
                candidates = self.agent_bridge.pending_enrollment_requests(
                    source_agent=source_agent
                )
                matches = [
                    row
                    for row in candidates
                    if (is_hex_prefix and row.enrollment_id.startswith(token))
                    or getattr(row, "device_name", "") == token
                ]
                if len(matches) > 1:
                    return f"connect: '{token}' matches more than one pending request; use the full receipt"
                if not matches:
                    return f"connect: no pending request matches '{token}'"
                receipt_id = matches[0].enrollment_id
            decision = "accept" if head == "accept" else "reject"
            result = await self.agent_bridge.decide_enrollment_request(
                receipt_id, decision=decision, source_agent=source_agent
            )
            return f"enrollment {result['status']}; receipt: {result['receipt_id']}"
        if head == "name":
            if not rest:
                return "usage: /connect name <name>"
            try:
                name = validate_device_name(rest.strip())
            except ValueError as exc:
                return f"connect: {exc}"
            setter = getattr(self.agent_bridge, "set_device_name", None)
            if setter is None:
                return "connect: device naming is not available on this build"
            applied = setter(name)
            return f"this device is now {applied or name}"
        if head == "trust":
            try:
                level = validate_trust(rest.strip())
            except ValueError as exc:
                return f"connect: {exc}"
            setter = getattr(self.agent_bridge, "set_trust_level", None)
            if setter is None:
                return "connect: trust levels are not available on this build"
            applied = setter(level) or level
            domain = self.client.state.origin or "this network"
            if domain.startswith("https://"):
                domain = domain[len("https://") :]
            lines = [f"trust for {domain} is now {applied}"]
            if applied == "manual":
                lines.append("messages now need /connect authorize or /connect send")
            return "\n".join(lines)
        if head in {"allow", "deny"}:
            if self.agent_bridge is None:
                return (
                    "connect: agent conversations require a running Kollab Hub session"
                )
            fields = rest.split(maxsplit=1)
            if not fields:
                return f"usage: /connect {head} <device-or-peer-key> [agent]"
            try:
                key = await self._resolve_peer_key(fields[0])
            except ValueError as exc:
                return f"connect: {exc}"
            new_rest = key if len(fields) == 1 else f"{key} {fields[1]}"
            return await self.agent_bridge.application_command(
                head, new_rest, source_agent=source_agent
            )
        if head in MANUAL_TRUST_COMMANDS:
            if self.agent_bridge is None:
                return (
                    "connect: agent conversations require a running Kollab Hub session"
                )
            return await self.agent_bridge.application_command(
                head, rest, source_agent=source_agent
            )
        if head == "status":
            return await self.format_status(show_keys=rest.strip() == "keys")
        if head == "revoke":
            if not rest:
                return "usage: /connect revoke <device>"
            try:
                key = await self._resolve_peer_key(rest)
            except ValueError as exc:
                return f"connect: {exc}"
            self.client.revoke(key)
            if self.agent_bridge is not None:
                self.agent_bridge._state()
                self.agent_bridge.store.revoke(self.client.state.room, key)
            return (
                f"device revoked: {rest}\n"
                "Its grants are gone. Provider credentials it copied are not revoked at the provider."
            )
        if head == "leave":
            await self.client.close(disable=True)
            return "left the network; automatic reconnect disabled for this workspace"
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
        if head == "help":
            from .plugin import format_connect_help

            return format_connect_help(show_all=rest.strip().lower() == "all")
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
