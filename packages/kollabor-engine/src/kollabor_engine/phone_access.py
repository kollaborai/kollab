"""Generate one-time WireGuard phone configs and manage server peer snippets.

The generated client private key exists only in ``create_peer``'s QR response.
Kollab persists only phone public keys needed by the server-side peer file.
The optional apply helper is trusted host configuration, never a WebUI setting.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import segno
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from kollabor_config.config_utils import get_config_directory

logger = logging.getLogger(__name__)

APPLY_COMMAND_ENV = "KOLLAB_PHONE_ACCESS_APPLY_COMMAND"
PEERS_FILE_ENV = "KOLLAB_PHONE_ACCESS_PEERS_FILE"
SETTING_KEYS = (
    "endpoint",
    "server_public_key",
    "subnet",
    "allowed_ips",
    "dns",
    "portal_url",
)
DEFAULT_SETTINGS = {
    "endpoint": "",
    "server_public_key": "",
    "subnet": "10.77.0.0/24",
    "allowed_ips": "",
    "dns": "",
    "portal_url": "",
}
_PRIVATE_V4 = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_PRIVATE_V6 = ipaddress.ip_network("fc00::/7")
_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}\Z")
_HOST_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_PEER_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


class PhoneAccessError(Exception):
    """A phone-access request that can be returned as an HTTP error."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def store_dir() -> Path:
    return get_config_directory() / "phone-access"


def generate_keypair() -> tuple[str, str]:
    """Return (private, public) X25519 WireGuard keys as standard base64."""
    key = X25519PrivateKey.generate()
    raw = serialization.Encoding.Raw
    private = key.private_bytes(
        raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private).decode("ascii"), base64.b64encode(public).decode("ascii")


def _valid_wireguard_key(value: str) -> bool:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(decoded) == 32 and base64.b64encode(decoded).decode("ascii") == value


def _no_control_or_space(value: str) -> bool:
    return all(ord(char) >= 33 and ord(char) != 127 for char in value)


def _valid_hostname(host: str) -> bool:
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        return False
    return all(_HOST_LABEL_PATTERN.fullmatch(label) for label in host.split("."))


def _validate_endpoint(value: str) -> str:
    if not value:
        return ""
    if len(value) > 260 or not _no_control_or_space(value):
        raise PhoneAccessError("endpoint must be a host and UDP port, without spaces")

    host: str
    port_text: str
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or value[closing + 1 : closing + 2] != ":":
            raise PhoneAccessError("IPv6 endpoints must use [address]:port")
        host = value[1:closing]
        port_text = value[closing + 2 :]
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise PhoneAccessError("bracketed endpoint host must be an IPv6 address") from exc
        if not isinstance(address, ipaddress.IPv6Address):
            raise PhoneAccessError("bracketed endpoint host must be an IPv6 address")
        host = f"[{address.compressed}]"
    else:
        if value.count(":") != 1:
            raise PhoneAccessError("endpoint must be host:port; bracket IPv6 addresses")
        host, port_text = value.rsplit(":", 1)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not _valid_hostname(host):
                raise PhoneAccessError("endpoint host must be an IP address or DNS name")
        else:
            if isinstance(address, ipaddress.IPv6Address):
                raise PhoneAccessError("IPv6 endpoints must use [address]:port")
            host = str(address)

    if not re.fullmatch(r"[0-9]{1,5}", port_text):
        raise PhoneAccessError("endpoint UDP port must be between 1 and 65535")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise PhoneAccessError("endpoint UDP port must be between 1 and 65535")
    return f"{host}:{port}"


def _validate_portal_url(value: str) -> str:
    if not value:
        return ""
    if len(value) > 2048 or not _no_control_or_space(value):
        raise PhoneAccessError("portal address must not contain spaces or control characters")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise PhoneAccessError("portal address must be an HTTP or HTTPS URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise PhoneAccessError("portal address must be an HTTP or HTTPS URL without credentials")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise PhoneAccessError(
            "portal address must use a literal private LAN IP that is allowed by the WebUI",
        ) from exc
    if isinstance(address, ipaddress.IPv4Address):
        allowed = address.is_link_local or any(address in network for network in _PRIVATE_V4)
    else:
        allowed = address.is_link_local or address in _PRIVATE_V6
    if not allowed:
        raise PhoneAccessError(
            "portal address must use a literal private LAN IP that is allowed by the WebUI"
        )
    return value.rstrip("/")


def _validate_subnet(value: str) -> str:
    if not value:
        return DEFAULT_SETTINGS["subnet"]
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise PhoneAccessError("tunnel subnet must be a network such as 10.77.0.0/24") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise PhoneAccessError("tunnel subnet must be a private IPv4 network")
    if not 16 <= network.prefixlen <= 30:
        raise PhoneAccessError("tunnel subnet must have a prefix between /16 and /30")
    if not any(network.subnet_of(private) for private in _PRIVATE_V4):
        raise PhoneAccessError("tunnel subnet must be inside an RFC1918 private network")
    return str(network)


def _validate_allowed_ips(value: str) -> str:
    if not value:
        return ""
    if len(value) > 1024:
        raise PhoneAccessError("phone routes are too long")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        item = item.strip()
        if not item or not _no_control_or_space(item):
            raise PhoneAccessError("phone routes must be comma-separated IP networks")
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise PhoneAccessError("phone routes must be comma-separated IP networks") from exc
        route = str(network)
        if route not in seen:
            normalized.append(route)
            seen.add(route)
    return ", ".join(normalized)


def _validate_dns(value: str) -> str:
    if not value:
        return ""
    if len(value) > 256:
        raise PhoneAccessError("DNS settings are too long")
    addresses: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item or not _no_control_or_space(item) or "%" in item:
            raise PhoneAccessError("DNS must contain comma-separated IP addresses")
        try:
            addresses.append(str(ipaddress.ip_address(item)))
        except ValueError as exc:
            raise PhoneAccessError("DNS must contain comma-separated IP addresses") from exc
    return ", ".join(addresses)


def _validated_settings(values: dict[str, Any], *, from_disk: bool = False) -> dict[str, str]:
    settings = dict(DEFAULT_SETTINGS)
    for key, value in values.items():
        if key not in SETTING_KEYS:
            if from_disk:
                continue
            raise PhoneAccessError(f"unsupported phone setting: {key}")
        if not isinstance(value, str):
            raise PhoneAccessError(f"{key} must be text")
        settings[key] = value.strip()

    try:
        settings["endpoint"] = _validate_endpoint(settings["endpoint"])
        if settings["server_public_key"] and not _valid_wireguard_key(
            settings["server_public_key"]
        ):
            raise PhoneAccessError("server public key must be a valid 32-byte WireGuard key")
        settings["subnet"] = _validate_subnet(settings["subnet"])
        settings["allowed_ips"] = _validate_allowed_ips(settings["allowed_ips"])
        settings["dns"] = _validate_dns(settings["dns"])
        settings["portal_url"] = _validate_portal_url(settings["portal_url"])
    except PhoneAccessError:
        if from_disk:
            raise PhoneAccessError(
                "saved phone-access settings are invalid; repair the local state file before continuing",
                500,
            ) from None
        raise
    return settings


def _peer_name(value: str) -> str:
    if not isinstance(value, str):
        raise PhoneAccessError("phone name must be text")
    name = value.strip()
    if not name or len(name) > 48 or not _NAME_PATTERN.fullmatch(name):
        raise PhoneAccessError(
            "phone name may use letters, numbers, spaces, dot, underscore, or hyphen"
        )
    if name.endswith((" ", ".")):
        raise PhoneAccessError("phone name must not end with a space or dot")
    return name


def _run_apply_command(command: str, peers_file: Path) -> None:
    """Run a host-admin configured helper without a shell or inherited secrets."""
    argv = shlex.split(command)
    if not argv:
        raise ValueError("empty phone peer apply command")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        PEERS_FILE_ENV: str(peers_file),
    }
    subprocess.run(
        [*argv, str(peers_file)],
        check=True,
        timeout=30,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=env,
    )


class PhoneAccess:
    """Private settings and public peer keys; never persist client private keys."""

    def __init__(
        self,
        directory: Path | None = None,
        keygen: Callable[[], tuple[str, str]] = generate_keypair,
        runner: Callable[[str, Path], None] = _run_apply_command,
        apply_command: str | None = None,
    ):
        self.directory = directory or store_dir()
        self._keygen = keygen
        self._runner = runner
        self._apply_command = (
            os.environ.get(APPLY_COMMAND_ENV, "") if apply_command is None else apply_command
        ).strip()
        self._lock = threading.RLock()

    @property
    def state_path(self) -> Path:
        return self.directory / "state.json"

    @property
    def peers_conf_path(self) -> Path:
        return self.directory / "peers.conf"

    def _empty_state(self) -> dict[str, Any]:
        return {"version": 1, "settings": dict(DEFAULT_SETTINGS), "peers": []}

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty_state()
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            logger.error("phone access state could not be read: %s", type(exc).__name__)
            raise PhoneAccessError("phone-access state is unreadable; repair it locally", 500) from None

        if not isinstance(raw, dict) or raw.get("version", 1) != 1:
            raise PhoneAccessError("phone-access state has an unsupported format", 500)
        raw_settings = raw.get("settings", {})
        raw_peers = raw.get("peers", [])
        if not isinstance(raw_settings, dict) or not isinstance(raw_peers, list):
            raise PhoneAccessError("phone-access state has an invalid structure", 500)
        try:
            settings = _validated_settings(raw_settings, from_disk=True)
            peers = [self._validated_stored_peer(peer, settings) for peer in raw_peers]
        except PhoneAccessError as exc:
            if exc.status == 500:
                raise
            raise PhoneAccessError("phone-access state contains invalid peer data", 500) from None
        ids = [peer["id"] for peer in peers]
        names = [peer["name"].casefold() for peer in peers]
        addresses = [peer["address"] for peer in peers]
        if (
            len(set(ids)) != len(ids)
            or len(set(names)) != len(names)
            or len(set(addresses)) != len(addresses)
        ):
            raise PhoneAccessError("phone-access state contains duplicate peers", 500)
        return {"version": 1, "settings": settings, "peers": peers}

    @staticmethod
    def _validated_stored_peer(peer: Any, settings: dict[str, str]) -> dict[str, Any]:
        if not isinstance(peer, dict):
            raise PhoneAccessError("invalid peer", 500)
        peer_id = peer.get("id")
        name = peer.get("name")
        address = peer.get("address")
        public_key = peer.get("public_key")
        created = peer.get("created")
        if (
            not isinstance(peer_id, str)
            or not _PEER_ID_PATTERN.fullmatch(peer_id)
            or not isinstance(name, str)
            or not isinstance(address, str)
            or not isinstance(public_key, str)
            or not isinstance(created, int)
            or isinstance(created, bool)
            or created < 0
        ):
            raise PhoneAccessError("invalid peer", 500)
        try:
            valid_name = _peer_name(name)
            ip = ipaddress.ip_address(address)
            subnet = ipaddress.ip_network(settings["subnet"])
            server_address = next(subnet.hosts())
        except (PhoneAccessError, ValueError, StopIteration):
            raise PhoneAccessError("invalid peer", 500) from None
        if (
            valid_name != name
            or not isinstance(ip, ipaddress.IPv4Address)
            or ip not in subnet
            or ip == server_address
            or not _valid_wireguard_key(public_key)
        ):
            raise PhoneAccessError("invalid peer", 500)
        return {
            "id": peer_id,
            "name": name,
            "address": str(ip),
            "public_key": public_key,
            "created": created,
        }

    def _write(self, path: Path, text: str) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".phone-access-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _save(self, state: dict[str, Any]) -> None:
        persisted = {
            "version": 1,
            "settings": {key: state["settings"][key] for key in SETTING_KEYS},
            "peers": [dict(peer) for peer in state["peers"]],
        }
        blocks = [
            f"# {peer['name']}\n[Peer]\nPublicKey = {peer['public_key']}\n"
            f"AllowedIPs = {peer['address']}/32\n"
            for peer in state["peers"]
        ]
        # The peer file is the apply helper's input, so publish it before state.
        self._write(self.peers_conf_path, "\n".join(blocks))
        self._write(self.state_path, json.dumps(persisted, indent=2) + "\n")

    def _command_available(self) -> bool:
        if not self._apply_command:
            return False
        try:
            return bool(shlex.split(self._apply_command))
        except ValueError:
            return False

    def _require_apply_command(self) -> None:
        if not self._command_available():
            raise PhoneAccessError(
                f"WireGuard peer apply helper is not configured on the Kollab host "
                f"({APPLY_COMMAND_ENV}); create and revoke are disabled until it is configured",
                503,
            )

    def _apply(self) -> None:
        try:
            self._runner(self._apply_command, self.peers_conf_path)
        except Exception as exc:
            # The helper's command, stderr, and environment may contain secrets.
            logger.warning("phone access server apply failed: %s", type(exc).__name__)
            raise PhoneAccessError(
                "WireGuard server did not confirm the peer change; check the host apply helper and retry",
                502,
            ) from None

    def _commit_peer_change(self, old: dict[str, Any], new: dict[str, Any]) -> None:
        try:
            self._save(new)
        except Exception as exc:
            try:
                self._save(old)
            except Exception as rollback_exc:
                logger.error("phone access local rollback failed: %s", type(rollback_exc).__name__)
            logger.error("phone access state write failed: %s", type(exc).__name__)
            raise PhoneAccessError("phone-access state could not be saved", 500) from None

        try:
            self._apply()
        except PhoneAccessError:
            try:
                self._save(old)
                self._apply()
            except Exception as rollback_exc:
                logger.error("phone access server rollback failed: %s", type(rollback_exc).__name__)
                raise PhoneAccessError(
                    "server apply failed and rollback could not be confirmed; inspect "
                    "the WireGuard server before retrying",
                    502,
                ) from None
            raise PhoneAccessError(
                "server apply failed; the previous peer configuration was restored and the change was not completed",
                502,
            ) from None

    def view(self) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            configured = self._command_available()
            settings_ready = bool(
                state["settings"]["endpoint"] and state["settings"]["server_public_key"]
            )
            return {
                "settings": dict(state["settings"]),
                "peers": [self._public_peer(peer) for peer in state["peers"]],
                "apply_configured": configured,
                "can_create": configured and settings_ready,
            }

    @staticmethod
    def _public_peer(peer: dict[str, Any]) -> dict[str, Any]:
        return {key: peer[key] for key in ("id", "name", "address", "public_key", "created")}

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise PhoneAccessError("settings body must be an object")
        with self._lock:
            old = self._load()
            settings = dict(old["settings"])
            for key, value in values.items():
                if key not in SETTING_KEYS:
                    raise PhoneAccessError(f"unsupported phone setting: {key}")
                if not isinstance(value, str):
                    raise PhoneAccessError(f"{key} must be text")
                settings[key] = value.strip()
            settings = _validated_settings(settings)
            locked_keys = {"endpoint", "server_public_key", "subnet", "allowed_ips", "dns"}
            if old["peers"] and any(
                old["settings"][key] != settings[key] for key in locked_keys
            ):
                raise PhoneAccessError(
                    "revoke all phone peers before changing their WireGuard settings", 409
                )
            state = {"version": 1, "settings": settings, "peers": old["peers"]}
            try:
                self._save(state)
            except Exception as exc:
                logger.error("phone access settings write failed: %s", type(exc).__name__)
                raise PhoneAccessError("phone-access settings could not be saved", 500) from None
            return self.view()

    def _address(self, state: dict[str, Any]) -> str:
        network = ipaddress.ip_network(state["settings"]["subnet"])
        used = {peer["address"] for peer in state["peers"]}
        # Reserve the first usable address (.1) for the WireGuard server.
        hosts = network.hosts()
        next(hosts, None)
        for host in hosts:
            address = str(host)
            if address not in used:
                return address
        raise PhoneAccessError("no free address left in the tunnel subnet", 409)

    def create_peer(self, name: str) -> dict[str, Any]:
        """Create a peer and return its QR once; the private key is never persisted."""
        if not isinstance(name, str):
            raise PhoneAccessError("phone name must be text")
        name = name.strip()
        with self._lock:
            state = self._load()
            settings = state["settings"]
            if not settings["endpoint"] or not settings["server_public_key"]:
                raise PhoneAccessError(
                    "set the server endpoint (host:port) and server public key first"
                )
            self._require_apply_command()
            if name:
                name = _peer_name(name)
            else:
                index = 1
                existing = {peer["name"].casefold() for peer in state["peers"]}
                while f"phone-{index}" in existing:
                    index += 1
                name = f"phone-{index}"
            if any(peer["name"].casefold() == name.casefold() for peer in state["peers"]):
                raise PhoneAccessError("a phone with that name exists", 409)

            private_key, public_key = self._keygen()
            if not _valid_wireguard_key(private_key) or not _valid_wireguard_key(public_key):
                raise PhoneAccessError("WireGuard key generation returned an invalid key", 500)
            peer = {
                "id": secrets.token_hex(16),
                "name": name,
                "address": self._address(state),
                "public_key": public_key,
                "created": int(time.time()),
            }
            network = settings["subnet"]
            allowed_ips = settings["allowed_ips"] or network
            config = [
                "[Interface]",
                f"PrivateKey = {private_key}",
                f"Address = {peer['address']}/32",
            ]
            if settings["dns"]:
                config.append(f"DNS = {settings['dns']}")
            config.extend(
                [
                    "",
                    "[Peer]",
                    f"PublicKey = {settings['server_public_key']}",
                    f"Endpoint = {settings['endpoint']}",
                    f"AllowedIPs = {allowed_ips}",
                    "PersistentKeepalive = 25",
                ]
            )
            qr = segno.make("\n".join(config) + "\n", error="m")
            new = {"version": 1, "settings": settings, "peers": [*state["peers"], peer]}
            self._commit_peer_change(state, new)
            logger.info("phone access: peer %s created", peer["id"])
            return {
                "peer": self._public_peer(peer),
                "qr_svg": qr.svg_inline(scale=4, border=2, dark="#000", light="#fff"),
                "applied": True,
            }

    def revoke_peer(self, peer_id: str) -> dict[str, Any]:
        """Remove exactly one peer and require a successful server-side apply."""
        with self._lock:
            state = self._load()
            kept = [peer for peer in state["peers"] if peer["id"] != peer_id]
            if len(kept) == len(state["peers"]):
                raise PhoneAccessError("unknown phone", 404)
            self._require_apply_command()
            new = {"version": 1, "settings": state["settings"], "peers": kept}
            self._commit_peer_change(state, new)
            logger.info("phone access: peer %s revoked", peer_id)
            return {"peers": [self._public_peer(peer) for peer in kept], "applied": True}


_instance_lock = threading.Lock()
_instance: PhoneAccess | None = None


def get_phone_access() -> PhoneAccess:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = PhoneAccess()
        return _instance


def set_phone_access(instance: PhoneAccess | None) -> None:
    """Replace the process-local service instance (used by tests and app wiring)."""
    global _instance
    with _instance_lock:
        _instance = instance
