"""Human-operated discovery, pairing and authorized conversation commands."""

from __future__ import annotations

import asyncio
import inspect
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kollabor_config.managed_config import clear_managed_config, read_managed_config

from .device_names import (
    DEFAULT_TRUST,
    NAME_RE,
    contact_route_hex,
    format_handle,
    key_label,
    short_fingerprint,
    validate_device_name,
    validate_trust,
)
from .dns.discovery import DiscoveryError, discover, fetch_agent_card, normalize_target
from .dns.discovery_store import DiscoveryStore
from .relay_state import RelayError

KNOCK_COUNT_TTL_SECONDS = 15.0
KNOCK_COUNT_TIMEOUT_SECONDS = 5.0
# The Connect screen polls every 2s: a poll this recent means a human is looking.
SCREEN_OPEN_SECONDS = 6.0


@dataclass(frozen=True, slots=True)
class JoinRequestRow:
    """One device asking to join, as the Connect screen shows it.

    ``enrollment_id`` is the receipt the decision needs; it is never rendered.
    """

    enrollment_id: str = field(repr=False)
    device: str
    fingerprint: str
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectSnapshot:
    """Everything the Connect screen shows except the join code."""

    network: str
    domain: str
    trust: str
    device: str
    relay_online: bool
    requests: tuple[JoinRequestRow, ...] = ()
    knocks: int = 0
    local_agents: tuple[str, ...] = ()
    remote_agents: tuple[str, ...] = ()
    offline_devices: tuple[str, ...] = ()
    # True when the process that built this does not run the relay (another
    # window in the workspace does): only the network, trust and device are
    # known, and the screen offers no code or decisions.
    read_only: bool = False
    # The primary that manages this device's settings, once its config arrived.
    config_from: str = ""

    def to_wire(self) -> dict[str, Any]:
        """Plain JSON types, for the daemon's reply to an attached window.

        The request's ``enrollment_id`` crosses the local RPC because deciding
        needs it; it is still never rendered.
        """
        return {
            "network": self.network,
            "domain": self.domain,
            "trust": self.trust,
            "device": self.device,
            "relay_online": self.relay_online,
            "knocks": self.knocks,
            "local_agents": list(self.local_agents),
            "remote_agents": list(self.remote_agents),
            "offline_devices": list(self.offline_devices),
            "read_only": self.read_only,
            "config_from": self.config_from,
            "requests": [
                {
                    "enrollment_id": row.enrollment_id,
                    "device": row.device,
                    "fingerprint": row.fingerprint,
                    "categories": list(row.categories),
                }
                for row in self.requests
            ],
        }

    @classmethod
    def from_wire(cls, value: Any) -> ConnectSnapshot:
        """Validate a daemon reply; anything off-shape raises ValueError.

        Strict on purpose: every field is bounded printable text, so a wrong
        or hostile reply cannot put control characters or a key on screen.
        """
        fields = {
            "network",
            "domain",
            "trust",
            "device",
            "relay_online",
            "knocks",
            "local_agents",
            "remote_agents",
            "offline_devices",
            "config_from",
            "requests",
        }
        # A daemon that predates ``read_only`` sends none: the screen is normal.
        if (
            not isinstance(value, dict)
            or not fields <= set(value) <= fields | {"read_only"}
            or not isinstance(value.get("read_only", False), bool)
        ):
            raise ValueError("invalid connect snapshot")
        knocks = value["knocks"]
        if (
            not isinstance(value["relay_online"], bool)
            or not isinstance(knocks, int)
            or isinstance(knocks, bool)
            or not 0 <= knocks <= 100_000
        ):
            raise ValueError("invalid connect snapshot")
        requests = value["requests"]
        if not isinstance(requests, list) or len(requests) > _WIRE_MAX_ROWS:
            raise ValueError("invalid connect snapshot")
        rows = []
        for item in requests:
            if not isinstance(item, dict) or set(item) != {
                "enrollment_id",
                "device",
                "fingerprint",
                "categories",
            }:
                raise ValueError("invalid connect snapshot")
            enrollment_id = item["enrollment_id"]
            if not isinstance(enrollment_id, str) or not _WIRE_ID_RE.fullmatch(
                enrollment_id
            ):
                raise ValueError("invalid connect snapshot")
            rows.append(
                JoinRequestRow(
                    enrollment_id=enrollment_id,
                    device=_wire_text(item["device"]),
                    fingerprint=_wire_text(item["fingerprint"], 32),
                    categories=_wire_texts(item["categories"]),
                )
            )
        trust = _wire_text(value["trust"], 16)
        validate_trust(trust)
        return cls(
            network=_wire_text(value["network"]),
            domain=_wire_text(value["domain"], 253),
            trust=trust,
            device=_wire_text(value["device"]),
            relay_online=value["relay_online"],
            requests=tuple(rows),
            knocks=knocks,
            local_agents=_wire_texts(value["local_agents"]),
            remote_agents=_wire_texts(value["remote_agents"]),
            offline_devices=_wire_texts(value["offline_devices"]),
            read_only=value.get("read_only", False),
            config_from=_wire_text(value["config_from"]) if value["config_from"] else "",
        )


_WIRE_MAX_ROWS = 64
_WIRE_TEXT_MAX = 200
_WIRE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}")


def _wire_text(value: Any, limit: int = _WIRE_TEXT_MAX) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or not all(char.isprintable() for char in value)
    ):
        raise ValueError("invalid connect snapshot")
    return value


def _wire_texts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _WIRE_MAX_ROWS:
        raise ValueError("invalid connect snapshot")
    return tuple(_wire_text(item) for item in value)


# Only valid under trust manual (docs/specs/agent-network-simple-flow.md section 6).
MANUAL_TRUST_COMMANDS = frozenset({"authorize", "send", "withdraw", "answer", "task", "cancel"})


# The first line of `/connect status` on a device with no network. The plugin
# reads it back in attach mode and in follower windows, so both share this.
NO_NETWORK = "network none"


def network_label(network: str, domain: str) -> str:
    """`marco-home  via kollabor.ai`, or just the domain when it is the name."""
    return domain if network == domain else f"{network}  via {domain}"


def directory_origin(value: str) -> str | None:
    """The canonical origin a directory name, URL or network ID stands for."""
    try:
        target = RelayCommands._resolve_network_target(value)
        return normalize_target(target, document=False).origin
    except (ValueError, TypeError, OSError):
        return None


def offline_device_names(agent_bridge, remote_rows: list, client) -> list[str]:
    """Approved devices that have no online row right now.

    ``remote_agents()`` rows are always ``online: True`` (the directory only
    lists agents currently reachable), so "offline" comes from comparing the
    approved peer keys against the relay's live roster and the online rows,
    not from a per-row flag. Nobody is offline while the relay itself is
    unreachable (presence is unknowable then). An accepted stranger is listed
    like any device once it has a name (the accepting side binds one; the
    knocking side learns it only from a directory answer). Named from the
    recorded ``peer_devices`` binding, falling back to ``key_label``.
    """
    if client.status().get("state") != "online":
        return []
    offline = {
        row.get("device")
        for row in remote_rows
        if not row.get("online") and row.get("device")
    }
    try:
        from .relay_conversations import RelayAddress

        state = agent_bridge._state().state
        online_keys = {peer["key"] for peer in client.peers()} | {
            RelayAddress.parse(row["address"]).key
            for row in remote_rows
            if row.get("online") and row.get("address")
        }
        strangers = getattr(state, "links", [])
        for key in client.state.approvals:
            if key in online_keys or (key in strangers and key not in state.peer_devices):
                continue
            offline.add(state.peer_devices.get(key) or key_label(key))
    except Exception:
        pass
    return sorted(offline)



def _arrival_name(name: str, key: str = "") -> str:
    """A device name fit for the main pane: a valid one, never its hex stand-in."""
    if not NAME_RE.fullmatch(name or "") or (key and name == key_label(key)):
        return "an unknown device"
    return name


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
        self._knock_count_cache = (float("-inf"), 0)
        self._knock_rows: tuple[tuple[str, str], ...] = ()
        self._screen_polled = float("-inf")
        # Ids of the requests and knocks already announced. They live in the
        # network state, so a restart announces only what is new.
        self._announced: set[str] = set(self.client.state.announced)
        self._knocks_fetched = False
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

    def _try_pending_rows(self) -> list | None:
        """Pending join requests for this device's issuer agent; None when unreadable."""
        getter = getattr(self.agent_bridge, "pending_enrollment_requests", None)
        if not callable(getter):
            return None
        source = getattr(getattr(self.agent_bridge, "identity", None), "agent_id", None)
        try:
            return list(getter(source_agent=source))
        except Exception:
            return None

    def _pending_rows(self) -> list:
        """Pending join requests for this device's issuer agent, never raising."""
        return self._try_pending_rows() or []

    def _pending_request_lines(self) -> list[str]:
        """`requests` lines for the status screen: who wants to join, by name."""
        if not callable(getattr(self.agent_bridge, "pending_enrollment_requests", None)):
            return []
        rows = self._pending_rows()
        if not rows:
            return ["requests none"]
        lines = ["requests"]
        for row in rows:
            full = getattr(row, "device_key_fingerprint", "")
            name = getattr(row, "device_name", "")
            lines.append(
                f"  {name or 'unknown device'} wants to join   "
                f"fingerprint {short_fingerprint(full)}   /connect accept {name or full[:4]}"
            )
        return lines

    def _local_agent_names(self) -> list[str]:
        """Agents running on this device: presence first, this window's agent always."""
        plugin = getattr(self.agent_bridge, "plugin", None)
        presence = getattr(plugin, "_presence", None)
        agents = []
        if presence is not None:
            try:
                agents = list(presence.get_cached_agents())
            except Exception:
                agents = []
        own = getattr(plugin, "_identity", None)
        if own is not None and not any(
            getattr(a, "agent_id", None) == getattr(own, "agent_id", None)
            for a in agents
        ):
            agents = [own] + agents
        return [n for n in (getattr(a, "identity", None) for a in agents) if n]

    async def _knock_count(self, domain: str) -> int:
        """How many knocks wait, fetched at most every KNOCK_COUNT_TTL_SECONDS."""
        checked, count = self._knock_count_cache
        now = time.monotonic()
        if now - checked < KNOCK_COUNT_TTL_SECONDS:
            return count
        self._knock_count_cache = (now, count)
        try:
            rows = await asyncio.wait_for(
                self.pending_contact_requests(domain), KNOCK_COUNT_TIMEOUT_SECONDS
            )
        except Exception:
            return count
        for row in rows:
            row.introduction.clear()
        self._knock_rows = tuple(
            (row.receipt_id, _arrival_name(row.device_name, row.sender_key))
            for row in rows
        )
        self._knocks_fetched = True
        self._knock_count_cache = (now, len(rows))
        return len(rows)

    def _domain_online(self) -> tuple[str, bool]:
        state = self.client.status()
        origin = state["origin"] or ""
        domain = origin[len("https://") :] if origin.startswith("https://") else origin
        return domain, state.get("state") == "online"

    def screen_polled(self) -> None:
        """A Connect or knock screen just loaded, so it shows what is pending."""
        self._screen_polled = time.monotonic()

    async def new_arrivals(self) -> list[str]:
        """Main-pane lines for join requests and knocks not announced yet.

        Names only. A request or knock is announced once, however often this
        runs, and across restarts. While a Connect or knock screen is showing
        them live they are marked seen and nothing is printed.
        """
        domain, online = self._domain_online()
        network = self._network_name(domain) or "this network"
        rows = self._try_pending_rows()
        found = [
            (
                f"join:{getattr(row, 'enrollment_id', '')}",
                f"{_arrival_name(getattr(row, 'device_name', ''))} wants to join "
                f"{network}. /connect to review",
            )
            for row in rows or []
            if getattr(row, "decision_available", True)
        ]
        if online and domain:
            await self._knock_count(domain)
            found += [
                (f"knock:{receipt}", f"{name} knocked. /connect knocks to review")
                for receipt, name in self._knock_rows
            ]
        fresh = [(key, line) for key, line in found if key not in self._announced]
        # Keep only what is still pending, plus every id of a kind that could
        # not be read this time (an unready issuer or an unreachable directory
        # reads as empty, which is not the same as decided).
        pending = {f"join:{getattr(row, 'enrollment_id', '')}" for row in rows or []}
        pending |= {f"knock:{receipt}" for receipt, _ in self._knock_rows}
        unread = (("join:",) if rows is None else ()) + (
            () if online and domain and self._knocks_fetched else ("knock:",)
        )
        self._announced = {
            item
            for item in self._announced
            if item in pending or item.startswith(unread)
        } | {key for key, _ in fresh}
        try:
            self.client.remember_announced(sorted(self._announced))
        except (OSError, RelayError):
            pass  # still announced once for this process; saved at the next change
        if time.monotonic() - self._screen_polled < SCREEN_OPEN_SECONDS:
            return []
        return [line for _, line in fresh]

    async def connect_snapshot(self) -> ConnectSnapshot:
        """The Connect screen's data: names and short fingerprints, never keys."""
        self.screen_polled()
        domain, online = self._domain_online()
        remote_rows = await self._remote_rows()
        requests = tuple(
            JoinRequestRow(
                enrollment_id=getattr(row, "enrollment_id", ""),
                device=getattr(row, "device_name", "") or "",
                fingerprint=short_fingerprint(getattr(row, "device_key_fingerprint", "")),
                categories=tuple(getattr(row, "credential_categories", ()) or ()),
            )
            for row in self._pending_rows()
        )
        return ConnectSnapshot(
            network=self._network_name(domain),
            domain=domain,
            trust=self._trust_level(),
            device=self._device_name(),
            relay_online=online,
            requests=requests,
            knocks=await self._knock_count(domain) if online and domain else 0,
            local_agents=tuple(self._local_agent_names()),
            remote_agents=tuple(
                row.get("handle")
                or format_handle(row.get("name", "?"), row.get("device", "?"))
                for row in remote_rows
                if row.get("online")
            ),
            offline_devices=tuple(
                offline_device_names(self.agent_bridge, remote_rows, self.client)
            ),
            config_from=self._config_from(),
        )

    def _config_from(self) -> str:
        """The primary's name when its sealed config has landed on this device."""
        record = read_managed_config()
        if record is None or record.primary_key != self.client.state.inviter:
            return ""
        return record.primary_name

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
        """A device name resolves to its peer key (a pasted 64-hex key passes).

        The recorded binding (``peer_devices``) is checked first so an
        offline device's name still resolves, not only an online one.
        """
        if re.fullmatch(r"[0-9a-f]{64}", token):
            return token
        if self.agent_bridge is not None:
            try:
                peer_devices = self.agent_bridge._state().state.peer_devices
            except Exception:
                peer_devices = {}
            for key, name in peer_devices.items():
                if name == token:
                    return key
        for row in await self._remote_rows():
            if row.get("device") == token and row.get("address"):
                try:
                    from .relay_conversations import RelayAddress

                    return RelayAddress.parse(row["address"]).key
                except Exception:
                    continue
        raise ValueError(f"no known device matches '{token}'; /connect status lists them")

    @staticmethod
    def _resolve_network_target(value: str) -> str:
        """Resolve an installed network ID to its approved discovery domain."""
        from kollabor_config.provisioned_state import ProvisionedStateFile

        domain = ProvisionedStateFile().get_network_preferences().get(value)
        return f"https://{domain}" if domain is not None else value

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

    async def format_status(self) -> str:
        """`/connect status`, redesigned per the constitution (sections 5-6).

        Names, not keys, appear here.
        """
        state = self.client.status()
        origin = state["origin"] or ""
        domain = origin[len("https://") :] if origin.startswith("https://") else origin

        lines = []
        if domain:
            label = network_label(self._network_name(domain), domain)
            lines.append(f"network {label}  trust: {self._trust_level()}")
        else:
            lines.append(NO_NETWORK)

        device_name = self._device_name()
        if device_name:
            lines.append(f"this device {device_name}")

        if domain:
            lines.append(f"contact route {domain}/c/{contact_route_hex(state['key'])}")
        else:
            lines.append("contact route none")

        lines.append("online")
        remote_rows = await self._remote_rows()
        for name in self._local_agent_names():
            lines.append(f"  {name} (this device)")
        network_trust = self._trust_level()
        effective_trust = getattr(self.agent_bridge, "effective_trust", None)
        for row in remote_rows:
            if not row.get("online"):
                continue
            handle = row.get("handle") or format_handle(
                row.get("name", "?"), row.get("device", "?")
            )
            line = f"  {handle} - {row.get('state', 'unknown')}"
            if callable(effective_trust) and row.get("address"):
                try:
                    from .relay_conversations import RelayAddress

                    peer = effective_trust(RelayAddress.parse(row["address"]).key)
                    if peer != network_trust:
                        line += f"  trust {peer}"
                except Exception:
                    pass
            lines.append(line)
        lines.extend(
            f"  {device} (offline)"
            for device in offline_device_names(
                self.agent_bridge, remote_rows, self.client
            )
        )
        lines.extend(self._pending_request_lines())

        lines.append("join code run /connect code")

        if state.get("error"):
            lines.append("connection issue " + state["error"])
        if domain and not state["enabled"]:
            lines.append("reconnect on launch disabled")

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
        self, domain: str, recipient_key: str, introduction: str, device_name: str = ""
    ) -> str:
        return await self._contacts().submit(
            domain, recipient_key, introduction, device_name
        )

    async def resolve_contact_route(self, domain: str, route_hex: str) -> str:
        return await self._contacts().resolve_route(domain, route_hex)

    async def sync_links(self, domain: str, keys: list[str]) -> None:
        await self._contacts().sync_links(domain, keys)

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
                return f"connect: {exc}"

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
            if not 1 <= len(fields) <= 2:
                return f"usage: /connect {head} <device> [fingerprint]"
            if self.agent_bridge is None:
                return "connect: local enrollment issuer is unavailable"
            token = fields[0]
            candidates = list(
                self.agent_bridge.pending_enrollment_requests(source_agent=source_agent)
            )
            matches = [
                row for row in candidates if getattr(row, "device_name", "") == token
            ] or [
                row
                for row in candidates
                if re.fullmatch(r"[0-9a-f]{4,}", token)
                and (
                    getattr(row, "device_key_fingerprint", "").startswith(token)
                    # scripts may still pass the receipt; no screen shows it
                    or (
                        len(token) >= 8
                        and getattr(row, "enrollment_id", "").startswith(token)
                    )
                )
            ]
            if len(fields) == 2:
                matches = [
                    row
                    for row in matches
                    if getattr(row, "device_key_fingerprint", "").startswith(
                        fields[1].lower()
                    )
                ]
            if len(matches) > 1:
                return (
                    f"connect: more than one pending request is named '{token}'; "
                    f"add the start of its fingerprint: /connect {head} {token} 4d04"
                )
            if not matches:
                return f"connect: no pending request matches '{token}'"
            row = matches[0]
            decision = "accept" if head == "accept" else "reject"
            who = getattr(row, "device_name", "") or "that device"
            try:
                await self.agent_bridge.decide_enrollment_request(
                    row.enrollment_id, decision=decision, source_agent=source_agent
                )
            except RelayError as exc:
                return f"connect: could not {decision} {who}: {exc}"
            if decision == "reject":
                return f"rejected {who}."
            domain = self.client.state.origin.removeprefix("https://")
            return (
                f"accepted {who}. it is now a trusted device on "
                f"{self._network_name(domain) or 'this network'}."
            )
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
            fields = rest.split()
            counts, usage = (
                ((2,), "usage: /connect allow <device> <agent>")
                if head == "allow"
                else ((1, 2), "usage: /connect deny <device> [agent]")
            )
            if len(fields) not in counts:
                return usage
            try:
                key = await self._resolve_peer_key(fields[0])
            except ValueError as exc:
                return f"connect: {exc}"
            effective_trust = getattr(self.agent_bridge, "effective_trust", None)
            if callable(effective_trust) and effective_trust(key) == "open":
                return (
                    f"connect: trust is open, so {head} has no effect; "
                    "use /connect trust agents, or /connect revoke <device>"
                )
            return await self.agent_bridge.application_command(
                head, " ".join([key, *fields[1:]]), source_agent=source_agent
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
            return await self.format_status()
        if head == "revoke":
            if not rest:
                return "usage: /connect revoke <device>"
            try:
                key = await self._resolve_peer_key(rest)
            except ValueError as exc:
                return f"connect: {exc}"
            if key == self.client.state.inviter:
                # Revoking the primary ends its say over this device's settings.
                clear_managed_config(primary_key=key)
            self.client.revoke(key, announce=True)  # every member drops it too
            if self.agent_bridge is not None:
                self.agent_bridge._state()
                self.agent_bridge.store.revoke(self.client.state.room, key)
                # A stranger's path runs through the directory: withdraw it now.
                sync_links = getattr(self.agent_bridge, "sync_links", None)
                if callable(sync_links):
                    await sync_links(force=True)
            return (
                f"device revoked: {rest}\n"
                "Its grants are gone and it gets no more of your sealed config. "
                "Provider credentials it copied are not revoked at the provider."
            )
        if head == "leave":
            origin = self.client.state.origin
            if not origin:
                return "connect: this device is not on a network"
            if rest and rest.lower().removeprefix("https://").rstrip("/") != (
                origin.removeprefix("https://")
            ):
                return f"connect: this device is not on {rest}"
            sync_links = getattr(self.agent_bridge, "sync_links", None)
            if callable(sync_links):
                await sync_links(force=True, keys=[])  # while still connected
            await self.client.leave()
            # The synced settings stay, as this device's own from now on. Rotate
            # blanks the inviter, so clear the record whoever set it.
            clear_managed_config()
            return "left the network; this device can join another with a code"
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
