"""Phone-only app access over Kollab's existing /connect transport.

Each phone has an isolated two-party relay room, explicit local approval, and
ephemeral session encryption inside the normal authenticated relay envelopes.
Only the engine's own ASGI application is reachable; it is never a URL proxy.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import re
import secrets
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

import aiohttp
import dns.asyncresolver
import segno
from nacl.public import Box, PrivateKey, PublicKey
from nacl.secret import SecretBox

from kollabor_config.config_utils import get_config_directory
from kollabor_config.file_lock import try_lock
from kollabor_config.proc_ctl import pid_alive
from plugins.hub.dns.discovery import _PublicResolver, discover
from plugins.hub.dns.discovery_store import DiscoveryStore
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_state import ID, KEY, RelayError, canonical_origin, strict_json

from .auth import get_token, read_token_from_disk
from .hub_bridge import network_daemons
from .phone_audio import PhoneAudioManager, PhoneCallError
from .phonelink_events import (
    PhoneLinkEventLog,
    normalize_phone_diagnostic,
    safe_event_label,
)

PHONE_PROTOCOL = "kollab-phonelink/1"
MAX_PHONES = 8
LEGACY_CHUNK = 8192
CHUNK = 160 * 1024
APP_CHUNK = CHUNK
PHONE_FRAME_MAX_BYTES = 256 * 1024
_PHONE_FRAME_MAGIC = b"KLPZ1"
_PHONE_FRAME_COMPRESSION_THRESHOLD = 512
MAX_BODY = 8 * 1024 * 1024
# The phone runs the full app (src/phonelink/app-main.tsx renders App): these are the
# roots that app calls (#368). Everything else, PhoneLink management among it, is the
# computer's only.
API_ROOTS = frozenset(
    {
        "sessions", "agents", "profiles", "workspaces", "mcp", "hub", "sidebar", "rosters", "teams",
        "prompts", "voice", "phone-app",
    }
)
APP_PROTOCOL = "kollab-phonelink-app/1"
APP_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
SEEN_SAVE_SECONDS = 60


def encode_phone_frame(raw: bytes) -> bytes:
    """Compress a phone protocol JSON packet when gzip meaningfully shrinks it."""
    if not isinstance(raw, bytes) or len(raw) > PHONE_FRAME_MAX_BYTES:
        raise ValueError("PhoneLink packet exceeds the maximum frame size.")
    if len(raw) < _PHONE_FRAME_COMPRESSION_THRESHOLD:
        return raw
    compressed = gzip.compress(raw, mtime=0)
    if len(compressed) + len(_PHONE_FRAME_MAGIC) >= len(raw):
        return raw
    return _PHONE_FRAME_MAGIC + compressed


def decode_phone_frame(raw: bytes) -> bytes:
    """Decode a marked gzip frame without allowing an unbounded expansion."""
    if not isinstance(raw, bytes) or len(raw) > PHONE_FRAME_MAX_BYTES + len(_PHONE_FRAME_MAGIC):
        raise ValueError("PhoneLink packet exceeds the maximum frame size.")
    if not raw.startswith(_PHONE_FRAME_MAGIC):
        if len(raw) > PHONE_FRAME_MAX_BYTES:
            raise ValueError("PhoneLink packet exceeds the maximum frame size.")
        return raw
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw[len(_PHONE_FRAME_MAGIC):])) as stream:
            decoded = stream.read(PHONE_FRAME_MAX_BYTES + 1)
            if len(decoded) > PHONE_FRAME_MAX_BYTES or stream.read(1):
                raise ValueError("Decompressed PhoneLink packet exceeds its size limit.")
            return decoded
    except (gzip.BadGzipFile, EOFError, OSError, zlib.error) as error:
        raise ValueError("Invalid compressed PhoneLink packet.") from error



def pairing_code(key: str) -> str:
    """The code both screens show for a phone key: 12 characters in groups of four."""
    head = key[:12].upper()
    return "-".join(head[i : i + 4] for i in range(0, 12, 4))


def host_fingerprint(host_key: str) -> str:
    """What the computer shows and the phone recomputes from the key in its QR."""
    digest = hashlib.sha256(b"kollab-phonelink-fingerprint/1\n" + bytes.fromhex(host_key)).hexdigest()[:20]
    return "-".join(digest[i : i + 4] for i in range(0, 20, 4)).upper()


def app_directory() -> Path | None:
    """The built phone app. It is served only by this computer, never by the relay."""
    try:
        from kollabor_webui.server import _assistant_static_dir
    except ImportError:
        return None
    root = _assistant_static_dir()
    candidate = root / "phonelink" / "app" if root else None
    return candidate if candidate and (candidate / "phone-app.js.gz").is_file() else None


def _app_serial() -> int:
    """The signed app version: 10**10 + major * 10**6 + minor * 10**3 + patch.

    Phones refuse an app whose serial is below the one they saved. The 10**10
    floor keeps every version above the file-time serials (epoch seconds) phones
    saved before, so the first update is never refused as a downgrade.
    """
    try:
        version = importlib.metadata.version("kollabor-engine")
    except importlib.metadata.PackageNotFoundError:
        version = ""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        # Any fallback would sit below a release a phone already saved.
        raise PhoneLinkError("The phone app needs this computer's release version.")
    major, minor, patch = (int(part) for part in match.groups())
    if minor > 999 or patch > 999:
        raise PhoneLinkError("This release version does not fit the phone app serial.")
    return 10**10 + major * 10**6 + minor * 10**3 + patch


def signed_app_manifest(directory: Path, host_key: str, sign) -> bytes:
    """List the app files, then sign that list's hash with the host's device key.

    The phone recomputes the hash from `serial` and `files` and checks the
    signature against the host key from its QR before it runs any of the code.
    """
    files = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or not APP_NAME.fullmatch(path.name):
            continue
        data = path.read_bytes()
        files.append({"name": path.name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    if not files:
        raise PhoneLinkError("The phone app is not built on this computer.")
    serial = _app_serial()
    bundle = app_bundle_hash(serial, files)
    signature = sign(app_signed_message(host_key, bundle))
    return json.dumps(
        {"protocol": APP_PROTOCOL, "serial": serial, "files": files, "bundle": bundle, "host": host_key,
         "signature": signature},
        separators=(",", ":"),
    ).encode()


def app_bundle_hash(serial: int, files: list[dict]) -> str:
    listing = f"{serial}\n" + "\n".join(f"{f['name']} {f['sha256']} {f['size']}" for f in files)
    return hashlib.sha256(listing.encode()).hexdigest()


def app_signed_message(host_key: str, bundle: str) -> bytes:
    return f"{APP_PROTOCOL}\n{host_key}\n{bundle}".encode()


class PhoneLinkError(ValueError):
    pass


logger = logging.getLogger(__name__)

OWNER_RECORD = "owner.json"


class PhoneLinkHeldElsewhere(PhoneLinkError):
    """Another engine holds PhoneLink; ``owner`` is its record, or None if it left none."""

    def __init__(self, owner: dict | None):
        self.owner = owner
        super().__init__(held_elsewhere_message(owner))


def read_owner(directory: Path) -> dict | None:
    """The engine holding PhoneLink, from owner.json, or None (absent, unreadable, gone)."""
    try:
        data = json.loads((directory / OWNER_RECORD).read_text())
        owner = {
            "pid": int(data["pid"]),
            "port": int(data["port"]) if data.get("port") is not None else None,
            "started": float(data.get("started") or 0),
            "cwd": str(data.get("cwd") or ""),
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return owner if pid_alive(owner["pid"]) else None


def _tilde(path: str) -> str:
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def held_elsewhere_message(owner: dict | None) -> str:
    """The lock error, naming the engine that holds PhoneLink when it left a record."""
    detail = ""
    if owner:
        parts = [f"port {owner['port']}"] if owner.get("port") else []
        parts.append(f"process {owner['pid']}")
        if owner.get("started"):
            at = datetime.fromtimestamp(owner["started"])
            hour = at.hour % 12 or 12
            parts.append(f"started {at:%b} {at.day}, {hour}:{at:%M} {at:%p}")
        if owner.get("cwd"):
            parts.append(f"in {_tilde(owner['cwd'])}")
        detail = " (" + ", ".join(parts) + ")"
    return (
        f"Another Kollab engine is managing PhoneLink on this computer{detail}. "
        "Manage phones there, or close that engine and try again."
    )


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".phone-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def session_keys(shared: bytes, host_session: str, phone_session: str) -> tuple[bytes, bytes]:
    binding = PHONE_PROTOCOL.encode() + bytes.fromhex(host_session + phone_session)
    return (
        hashlib.sha256(shared + binding + b"phone-to-host").digest(),
        hashlib.sha256(shared + binding + b"host-to-phone").digest(),
    )


@dataclass
class PhoneSession:
    incoming: SecretBox
    outgoing: SecretBox
    host_session: str
    phone_session: str
    received: int = -1
    frame_compression: bool = False
    chunk_size: int = LEGACY_CHUNK
    coalesce_reads: bool = False
    zero_body_autorun: bool = False

    sent: int = 0
    used: float = field(default_factory=time.monotonic)
    last_request_at: float | None = None


@dataclass
class HttpExchange:
    method: str
    path: str
    headers: list
    size: int
    body: bytearray = field(default_factory=bytearray)
    chunk_size: int = LEGACY_CHUNK
    coalesce_headers: bool = False

    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16))
    pending: dict | None = None
    static: tuple[str, bytes] | None = None
    task: asyncio.Task | None = None
    used: float = field(default_factory=time.monotonic)
    sequence: int = 0


CALL_OPS = frozenset({"call_open", "call_tick", "call_close"})

# The only voice routes a phone may reach (sprint plan section 2): create, status,
# events and stop. Join tickets, settings, turns and worker events stay computer-only.
_PHONE_VOICE_ROUTES = (
    ("POST", re.compile(r"/sessions/[^/]+/voice")),
    ("GET", re.compile(r"/voice/sessions/[^/]+")),
    ("GET", re.compile(r"/voice/sessions/[^/]+/events")),
    ("DELETE", re.compile(r"/voice/sessions/[^/]+")),
)


def voice_route_allowed(method: str, path: str) -> bool:
    """True unless the path is a voice route a phone may not reach."""
    if "voice" not in path.split("/"):
        return True
    return any(
        method == route_method and pattern.fullmatch(path)
        for route_method, pattern in _PHONE_VOICE_ROUTES
    )


async def _no_phone_bridge(voice_session_id: str, generation: int):
    raise PhoneCallError("call_unavailable")


class PhoneLink:
    def __init__(
        self,
        app,
        directory: Path | None = None,
        port: int | None = None,
        open_phone_bridge=None,
    ):
        self.port = port
        self.app = app
        self.directory = directory or get_config_directory() / "phonelink"
        self.records: dict[str, dict] = {}
        self.clients: dict[str, RelayClient] = {}
        self.sessions: dict[str, PhoneSession] = {}
        self._idle_announced: set[str] = set()
        self.exchanges: dict[tuple[str, str], HttpExchange] = {}
        # Calls stay unavailable until the voice bridge is injected (lane B).
        self.audio = PhoneAudioManager(open_phone_bridge or _no_phone_bridge)
        self._call_stops: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._loaded = False
        self._owner_fd: int | None = None
        self._network_cache: tuple[float, dict | None] = (0, None)
        self._capabilities: dict[str, tuple[float, bool]] = {}
        self._closed = False
        self.event_log = PhoneLinkEventLog()
        self._pending_announced: set[tuple[str, str]] = set()
        self._expired_announced: set[str] = set()
        self._maintenance_errors: dict[str, tuple[str, float]] = {}

    def _event(
        self,
        event: str,
        *,
        identifier: str | None = None,
        peer: str | None = None,
        actor: dict | None = None,
        **fields,
    ) -> dict:
        row = self.records.get(identifier) if identifier is not None else None
        if row is not None:
            fields.setdefault("device_name", row.get("name"))
            fields.setdefault("network", row.get("network"))
        return self.event_log.emit(
            event, identifier=identifier, peer=peer, actor=actor, **fields
        )

    async def event_history(self) -> dict:
        return {"events": self.event_log.snapshot()}

    @staticmethod
    def _error_kind(error: BaseException) -> str:
        name = type(error).__name__.casefold()
        module = type(error).__module__.casefold()
        if "timeout" in name:
            return "timeout"
        if any(part in name for part in ("ssl", "tls", "certificate")):
            return "tls"
        if "dns" in name or module.startswith("dns."):
            return "dns"
        if "http" in name or "response" in name:
            return "http"
        if isinstance(error, PhoneLinkError):
            return "configuration"
        if isinstance(error, RelayError):
            return "relay"
        if isinstance(error, (aiohttp.ClientError, OSError)):
            return "socket"
        return "unexpected"

    def _report_error(self, key: str, error: BaseException, identifier: str | None = None) -> None:
        error_kind = self._error_kind(error)
        now = time.monotonic()
        previous = self._maintenance_errors.get(key)
        if previous and previous[0] == error_kind and now - previous[1] < 60:
            return
        self._maintenance_errors[key] = (error_kind, now)
        self._event("relay_error", identifier=identifier, error_kind=error_kind)

    async def network(self) -> dict | None:
        if time.monotonic() - self._network_cache[0] < 5:
            return self._network_cache[1]
        selected = None
        async for row, status in network_daemons():
            # network_status is the existing local /connect interface. Its
            # display contract is '<network> via <domain>', or just <domain>.
            domain = str(status.get("network", "")).rpartition(" via ")[-1]
            try:
                origin = canonical_origin("https://" + domain)
            except RelayError:
                continue
            selected = {
                "origin": origin,
                "network": status["network"],
                "device": status.get("device", ""),
                "workspace": row.get("workspace", ""),
            }
            break
        self._network_cache = (time.monotonic(), selected)
        return selected

    async def directory_ready(self, origin: str) -> bool:
        cached = self._capabilities.get(origin)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        # Exactly the discovery + durable publisher pin used by /connect.
        result = await discover(origin)
        await asyncio.to_thread(DiscoveryStore(self.directory / "discovery").accept, result)
        if result.manifest.get("endpoints", {}).get("control") != origin + "/relay/v1":
            return False
        connector = aiohttp.TCPConnector(
            resolver=_PublicResolver(dns.asyncresolver.Resolver(), ()), use_dns_cache=False
        )
        async with aiohttp.ClientSession(
            connector=connector, trust_env=False, timeout=aiohttp.ClientTimeout(total=10)
        ) as http:
            async with http.get(origin + "/relay/v1/phone/status", allow_redirects=False) as response:
                if response.status != 200:
                    ready = False
                else:
                    raw = await response.content.read(2049)
                    data = strict_json(raw, limit=2048)
                    ready = data.get("protocol") == PHONE_PROTOCOL and data.get("ready") is True
        self._capabilities[origin] = (time.monotonic(), ready)
        return ready

    def _claim_owner(self) -> int:
        """One engine owns the device-wide cache and all of its live rooms."""
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            self.directory / "owner.lock",
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            if not try_lock(fd):
                raise PhoneLinkHeldElsewhere(read_owner(self.directory))
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _write_owner(self) -> None:
        """Record this engine as PhoneLink's holder, so other engines can name and reach it."""
        path = self.directory / OWNER_RECORD
        tmp = path.with_name(f".{OWNER_RECORD}.{os.getpid()}.tmp")
        try:
            record = {
                "pid": os.getpid(),
                "port": self.port,
                "started": time.time(),
                "cwd": os.getcwd(),
            }
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(record, handle)
            os.replace(tmp, path)
        except OSError:
            logger.warning("PhoneLink owner record could not be written", exc_info=True)

    def _drop_owner(self) -> None:
        """Remove owner.json while it still names this process (the lock goes with the fd)."""
        path = self.directory / OWNER_RECORD
        try:
            if json.loads(path.read_text()).get("pid") == os.getpid():
                path.unlink()
        except (OSError, ValueError, AttributeError):
            pass

    async def start(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            try:
                owner_fd = self._claim_owner()
            except OSError:
                raise PhoneLinkError(
                    "PhoneLink state could not be locked. Its saved data has been preserved."
                ) from None
            path = self.directory / "phones.json"
            try:
                data = strict_json(path.read_bytes(), limit=32768)
                records = data["phones"]
                if data.get("version") != 1 or not isinstance(records, dict) or len(records) > MAX_PHONES:
                    raise ValueError()
                for identifier, row in records.items():
                    if not ID.fullmatch(identifier) or not isinstance(row, dict):
                        raise ValueError()
                    origin = canonical_origin(row["origin"])
                    row["origin"] = origin
                    row["network"] = safe_event_label(
                        row.get("network") or urlsplit(origin).hostname or "unknown",
                        max_length=128,
                    )
                    if (
                        not isinstance(row["name"], str)
                        or not 1 <= len(row["name"]) <= 48
                        or not row["name"].isprintable()
                    ):
                        raise ValueError()
                    if type(row["expires"]) is not int or type(row["created"]) is not int:
                        raise ValueError()
                    if not isinstance(row["key"], str) or (row["key"] and not KEY.fullmatch(row["key"])):
                        raise ValueError()
                    if not KEY.fullmatch(row.setdefault("host", "")) and row["host"]:
                        raise ValueError()
                    if type(row.setdefault("seen", 0)) is not int:
                        raise ValueError()
                self.records = records
            except FileNotFoundError:
                self.records = {}
            except OSError, ValueError, KeyError, TypeError:
                os.close(owner_fd)
                raise PhoneLinkError("Saved PhoneLink data could not be read. It has been preserved.") from None
            except BaseException:
                os.close(owner_fd)
                raise
            self._owner_fd = owner_fd
            self._write_owner()
            self._loaded = True
            self._closed = False
            for identifier, row in self.records.items():
                record_state = (
                    "linked"
                    if row["key"]
                    else "expired"
                    if row["expires"] < time.time()
                    else "pending"
                )
                self._event(
                    "restored_at_startup",
                    identifier=identifier,
                    peer=row["key"] or None,
                    record_state=record_state,
                )
            self._task = asyncio.create_task(self._maintain(), name="kollab-phonelink")

    def _persist(self) -> None:
        _save(self.directory / "phones.json", {"version": 1, "phones": self.records})

    async def _client(self, identifier: str) -> RelayClient:
        if identifier in self.clients:
            return self.clients[identifier]
        row = self.records[identifier]
        client = RelayClient(self.directory, state_dir=self.directory / identifier)
        if row["key"]:
            # The locally approved metadata pin is authoritative. Recover a
            # crash after its save and before the transport's separate save.
            client.approve(row["key"])
        client.set_request_handler(lambda peer, method, payload: self._receive(identifier, peer, method, payload))
        client.add_peer_session_listener(
            lambda event: self._invalidate(
                identifier,
                cause=event.kind,
                device_name=row["name"],
                network=row["network"],
            )
        )

        def relay_lifecycle(event) -> None:
            event_names = {
                "socket_opened": "relay_socket_opened",
                "socket_closed": "relay_socket_closed",
                "reconnecting": "relay_reconnecting",
                "error": "relay_error",
            }
            event_name = event_names.get(getattr(event, "kind", ""))
            if event_name is None:
                return
            fields = {}
            for field_name in (
                "close_code",
                "close_reason",
                "retry_count",
                "retry_seconds",
                "error_kind",
            ):
                value = getattr(event, field_name, None)
                if value is not None:
                    fields[field_name] = value
            self._event(
                event_name,
                identifier=identifier,
                device_name=row["name"],
                network=row["network"],
                **fields,
            )

        client.add_connection_listener(relay_lifecycle)
        self.clients[identifier] = client
        try:
            await client.connect(row["origin"], ws_url=row["origin"].replace("https://", "wss://", 1) + "/relay/v1/ws")
            if self.records.get(identifier) is not row:
                raise PhoneLinkError("That invitation has been removed.")
        except BaseException:
            self.clients.pop(identifier, None)
            await client.close()
            raise
        return client

    def _invalidate(
        self,
        identifier: str,
        *,
        cause: str = "session_invalidated",
        peer: str | None = None,
        device_name: str | None = None,
        network: str | None = None,
    ) -> None:
        session = self.sessions.pop(identifier, None)
        self._idle_announced.discard(identifier)
        if session is not None:
            row = self.records.get(identifier, {})
            self._event(
                "disconnected",
                identifier=identifier,
                peer=peer or row.get("key"),
                cause=cause,
                device_name=device_name or row.get("name"),
                network=network or row.get("network"),
                idle_seconds=time.monotonic() - session.used,
                last_request_at=(
                    int(session.last_request_at)
                    if session.last_request_at is not None
                    else None
                ),
            )
        for key, exchange in tuple(self.exchanges.items()):
            if key[0] == identifier:
                if exchange.task:
                    exchange.task.cancel()
                self.exchanges.pop(key, None)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop: nothing could have opened a call yet
        stop = loop.create_task(self.audio.invalidate(identifier, cause=cause))
        self._call_stops.add(stop)
        stop.add_done_callback(self._call_stops.discard)

    def _announce_idle_sessions(self) -> None:
        """Log a linked phone once when it stops sending requests (the 45 s status() window)."""
        now = time.monotonic()
        for identifier, session in tuple(self.sessions.items()):
            if now - session.used < 45 or identifier in self._idle_announced:
                continue
            self._idle_announced.add(identifier)
            row = self.records.get(identifier, {})
            self._event(
                "disconnected",
                identifier=identifier,
                peer=row.get("key"),
                cause="idle_timeout",
                device_name=row.get("name"),
                network=row.get("network"),
                idle_seconds=now - session.used,
                last_request_at=(
                    int(session.last_request_at)
                    if session.last_request_at is not None
                    else None
                ),
            )

    async def _maintain(self) -> None:
        try:
            while not self._closed:
                if not self.records:
                    await asyncio.sleep(5)
                    continue
                async with self._lock:
                    pruned = {}
                    for identifier, row in tuple(self.records.items()):
                        expired = not row["key"] and row["expires"] < time.time()
                        if expired:
                            if identifier not in self._expired_announced:
                                self._event(
                                    "invite_expired",
                                    identifier=identifier,
                                    cause="ttl_expired",
                                    record_state="expired",
                                )
                                self._expired_announced.add(identifier)
                            self._invalidate(identifier, cause="invite_expired")
                            self._pending_announced = {
                                item for item in self._pending_announced if item[0] != identifier
                            }
                            client = self.clients.pop(identifier, None)
                            if client:
                                await client.close()
                            pruned[identifier] = self.records.pop(identifier)
                    if pruned:
                        # Expired invitations go, so they never count toward
                        # MAX_PHONES or linger in phones.json.
                        try:
                            self._persist()
                        except Exception as error:
                            self.records.update(pruned)
                            self._report_error("prune", error)
                        else:
                            self._expired_announced.difference_update(pruned)
                    for key, exchange in tuple(self.exchanges.items()):
                        if time.monotonic() - exchange.used > 45:
                            if exchange.task:
                                exchange.task.cancel()
                            self.exchanges.pop(key, None)
                    self._announce_idle_sessions()
                # Saved phone approval is device-wide. A busy or absent agent
                # daemon must not disconnect it, and connection attempts must
                # never hold the lock needed to revoke another phone.
                for identifier, row in tuple(self.records.items()):
                    if identifier not in self.clients and (row["key"] or row["expires"] >= time.time()):
                        try:
                            if await self.directory_ready(row["origin"]) and identifier in self.records:
                                await self._client(identifier)
                                self._maintenance_errors.pop(f"maintenance:{identifier}", None)
                        except Exception as exc:
                            # Keep the transport resilient without exposing raw URLs or errors.
                            self._report_error(f"maintenance:{identifier}", exc, identifier)
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def status(self, *, refresh: bool = False, actor: dict | None = None) -> dict:
        await self.start()
        if refresh:
            self._network_cache = (0, None)
            self._capabilities.clear()
        network = await self.network()
        state, message = (
            "connect_needed",
            "PhoneLink needs a Kollab connection. Open Network to check or connect this computer.",
        )
        if network:
            try:
                ready = await self.directory_ready(network["origin"])
                state = "ready" if ready else "service_update"
                message = (
                    "Scan with your phone's camera, then approve it here."
                    if ready
                    else "Your connection service needs a PhoneLink update before it can open Kollab on your phone."
                )
            except Exception as exc:
                self._report_error("status", exc)
                state, message = (
                    "offline",
                    "The connection service could not be reached. Check your connection and try again.",
                )
            else:
                self._maintenance_errors.pop("status", None)
        phones = []
        for identifier, row in self.records.items():
            client = self.clients.get(identifier)
            peers = client.peers() if client else []
            pending = []
            if not row["key"] and row["expires"] >= time.time():
                pending = [{"key": p["key"], "code": pairing_code(p["key"])} for p in peers if not p["approved"]]
                for peer_item in pending:
                    marker = (identifier, peer_item["key"])
                    if marker not in self._pending_announced:
                        self._pending_announced.add(marker)
                        self._event(
                            "invite_opened",
                            identifier=identifier,
                            peer=peer_item["key"],
                            # The phone opened the invite; a web UI poll only noticed it.
                            actor=None,
                            record_state="pending",
                        )
                        self._event(
                            "pairing_code_shown",
                            identifier=identifier,
                            peer=peer_item["key"],
                            actor=actor,
                            record_state="pending",
                        )
            session = self.sessions.get(identifier)
            connected = bool(
                session
                and client
                and client.status()["state"] == "online"
                and any(p["key"] == row["key"] and p["session"] == session.phone_session for p in peers)
                and time.monotonic() - session.used < 45
            )
            phones.append(
                {
                    "id": identifier,
                    "name": row["name"],
                    "linked": bool(row["key"]),
                    "connected": connected,
                    "expires_at": row["expires"],
                    "last_seen": row["seen"] or None,
                    "fingerprint": host_fingerprint(row["host"]) if row["host"] else "",
                    "pending": pending,
                }
            )
        return {
            "state": state,
            "message": message,
            "network": network["network"] if network else "",
            "device": network["device"] if network else "",
            "phones": phones,
        }

    async def offer(self, name: str, actor: dict | None = None) -> dict:
        await self.start()
        if not isinstance(name, str) or len(name.strip()) > 48 or (name.strip() and not name.strip().isprintable()):
            self._event(
                "denied", actor=actor, cause="invalid_name", device_name=name
            )
            raise PhoneLinkError("Use a phone name of at most 48 printable characters.")
        status = await self.status(refresh=True, actor=actor)
        if status["state"] != "ready":
            self._event(
                "denied",
                actor=actor,
                cause="service_unavailable",
                device_name=name,
                network=status.get("network"),
            )
            raise PhoneLinkError(status["message"])
        network = await self.network()
        async with self._lock:
            if len(self.records) >= MAX_PHONES:
                self._event(
                    "denied",
                    actor=actor,
                    cause="phone_limit",
                    device_name=name.strip() or "My Phone",
                    network=network.get("network") if network else None,
                )
                raise PhoneLinkError("Up to eight phones can be saved. Remove a phone before adding another.")
            identifier = secrets.token_hex(16)
            self.records[identifier] = {
                "name": name.strip() or "My Phone",
                "origin": network["origin"],
                "network": safe_event_label(network.get("network"), max_length=128),
                "key": "",
                "host": "",
                "seen": 0,
                "created": int(time.time()),
                "expires": int(time.time()) + 300,
            }
        try:
            client = await self._client(identifier)
            async with self._lock:
                if identifier not in self.records:
                    raise PhoneLinkError("That invitation has been removed.")
                self._persist()
        except BaseException:
            async with self._lock:
                self.records.pop(identifier, None)
                self._persist()
                client = self.clients.pop(identifier, None)
                if client:
                    await client.close()
            raise
        async with self._lock:
            if identifier not in self.records:
                raise PhoneLinkError("That invitation has been removed.")
            self.records[identifier]["host"] = client.public_key
            self._persist()
            fragment = urlencode(
                {
                    "v": "1",
                    "id": identifier,
                    "room": client.state.room,
                    "host": client.public_key,
                    "expires": self.records[identifier]["expires"],
                }
            )
            link = network["origin"] + "/relay/v1/phone/#" + fragment
            result = {
                "id": identifier,
                "name": self.records[identifier]["name"],
                "expires_at": self.records[identifier]["expires"],
                "fingerprint": host_fingerprint(client.public_key),
                "link": link,
                "qr_svg": segno.make(link, error="m").svg_inline(scale=4, border=4, dark="#000", light="#fff"),
            }
            self._event(
                "invite_created",
                identifier=identifier,
                actor=actor,
                record_state="pending",
            )
            return result

    async def approve(self, identifier: str, key: str, actor: dict | None = None) -> dict:
        await self.start()
        async with self._lock:
            row = self.records.get(identifier)
            client = self.clients.get(identifier)
            if not row or not client or row["key"] or row["expires"] < time.time():
                self._event(
                    "denied",
                    identifier=identifier,
                    actor=actor,
                    cause="invitation_not_waiting",
                )
                raise PhoneLinkError("That invitation is no longer waiting for approval. Create a new QR code.")
            if not any(peer["key"] == key and not peer["approved"] for peer in client.peers()):
                self._event(
                    "denied",
                    identifier=identifier,
                    actor=actor,
                    cause="phone_not_waiting",
                )
                raise PhoneLinkError("That phone is no longer waiting to connect.")
            # Pin in durable metadata first; no incoming request runs before
            # both metadata and the existing transport's approval are saved.
            row["key"] = key
            try:
                self._persist()
                client.approve(key)
            except BaseException:
                row["key"] = ""
                self._persist()
                raise
        self._pending_announced = {
            item for item in self._pending_announced if item[0] != identifier
        }
        self._event(
            "approved",
            identifier=identifier,
            peer=key,
            actor=actor,
            record_state="linked",
        )
        return await self.status(actor=actor)

    async def revoke(self, identifier: str, actor: dict | None = None) -> dict:
        await self.start()
        async with self._lock:
            row = self.records.get(identifier)
            if row is None:
                self._event(
                    "denied",
                    identifier=identifier,
                    actor=actor,
                    cause="phone_not_saved",
                )
                raise PhoneLinkError("That phone is no longer saved.")
            del self.records[identifier]
            try:
                self._persist()
            except BaseException:
                self.records[identifier] = row
                raise
            self._maintenance_errors.pop(f"maintenance:{identifier}", None)
            event_name = "revoked" if row["key"] else "invitation_cancelled"
            self._event(
                event_name,
                identifier=identifier,
                peer=row["key"] or None,
                actor=actor,
                cause="user_action",
                record_state="linked" if row["key"] else "pending",
                device_name=row["name"],
                network=row["network"],
            )
            self._invalidate(
                identifier,
                cause=event_name,
                peer=row["key"] or None,
                device_name=row["name"],
                network=row["network"],
            )
            self._pending_announced = {
                item for item in self._pending_announced if item[0] != identifier
            }
            self._expired_announced.discard(identifier)
            client = self.clients.pop(identifier, None)
            if client:
                # Disconnect the whole phone-only room. It cannot route to
                # any agent-network peer, and no identity files are deleted.
                await client.close()
        return await self.status(actor=actor)

    async def _receive(self, identifier: str, peer: str, method: str, payload: dict) -> dict:
        row = self.records.get(identifier)
        client = self.clients.get(identifier)
        if not row or row["key"] != peer or not client:
            raise RelayError("phone is not approved")
        phone_session = next((p["session"] for p in client.peers() if p["key"] == peer), "")
        host_session = client.status()["session"]
        if method == "phone_session":
            if not isinstance(payload.get("public_key"), str) or not KEY.fullmatch(payload["public_key"]):
                raise RelayError("invalid phone session key")
            private = PrivateKey.generate()
            shared = Box(private, PublicKey(bytes.fromhex(payload["public_key"]))).shared_key()
            incoming, outgoing = session_keys(shared, host_session, phone_session)
            self._invalidate(identifier, cause="session_replaced")
            frame_compression = payload.get("frame_compression") is True
            coalesce_reads = payload.get("coalesce_reads") is True
            zero_body_autorun = payload.get("zero_body_autorun") is True
            requested_chunk = payload.get("max_chunk")
            chunk_size = (
                requested_chunk
                if type(requested_chunk) is int and LEGACY_CHUNK <= requested_chunk <= CHUNK
                else LEGACY_CHUNK
            )
            self.sessions[identifier] = PhoneSession(
                SecretBox(incoming),
                SecretBox(outgoing),
                host_session,
                phone_session,
                frame_compression=frame_compression,
                chunk_size=chunk_size,
                coalesce_reads=coalesce_reads,
                zero_body_autorun=zero_body_autorun,
            )
            self._event(
                "connected",
                identifier=identifier,
                peer=peer,
                cause="phone_session",
                record_state="linked",
            )
            return {
                "public_key": private.public_key.encode().hex(),
                "name": row["name"],
                "protocol": PHONE_PROTOCOL,
                "frame_compression": frame_compression,
                "chunk_size": chunk_size,
                "coalesce_reads": coalesce_reads,
                "zero_body_autorun": zero_body_autorun,
            }
        session = self.sessions.get(identifier)
        if method == "phone_diagnostic":
            if (
                not session
                or (session.host_session, session.phone_session)
                != (host_session, phone_session)
            ):
                raise RelayError("phone session is unavailable")
            diagnostic = normalize_phone_diagnostic(payload)
            if diagnostic is None:
                # Never valid, so answer rather than fail: the phone drops it
                # instead of resending it ahead of every later report.
                return {"accepted": False}
            self._event(
                "phone_diagnostic",
                identifier=identifier,
                peer=peer,
                phone_event=diagnostic["event"],
                close_code=diagnostic.get("close_code"),
                close_reason=diagnostic.get("close_reason"),
                retry_count=diagnostic.get("retry_count"),
            )
            return {"accepted": True}
        if (
            method != "phone_http"
            or not session
            or (session.host_session, session.phone_session) != (host_session, phone_session)
        ):
            raise RelayError("phone session is unavailable")
        try:
            raw = base64.b64decode(payload["packet"], validate=True)
            packet = session.incoming.decrypt(raw)
            if not session.frame_compression and packet.startswith(_PHONE_FRAME_MAGIC):
                raise ValueError("compressed frames were not negotiated")
            request = strict_json(decode_phone_frame(packet), limit=PHONE_FRAME_MAX_BYTES)
            sequence = request.pop("seq")
            if type(sequence) is not int or sequence <= session.received:
                raise ValueError()
            session.received = sequence
        except Exception:
            raise RelayError("invalid phone session packet") from None
        session.used = time.monotonic()
        session.last_request_at = time.time()
        self._idle_announced.discard(identifier)
        self._touch(identifier, row)
        if request.get("op") in CALL_OPS:
            result = await self._call(identifier, session, request)
        else:
            try:
                result = await self._http(
                    identifier,
                    request,
                    chunk_size=session.chunk_size,
                    coalesce_headers=session.coalesce_reads,
                    zero_body_autorun=session.zero_body_autorun,
                )
            except PhoneLinkError as exc:
                result = {"error": str(exc)}
        if self.sessions.get(identifier) is not session:
            raise RelayError("phone session changed")
        result["seq"] = session.sent
        session.sent += 1
        response_packet = json.dumps(result, separators=(",", ":")).encode()
        if len(response_packet) > PHONE_FRAME_MAX_BYTES:
            raise RelayError("phone session response exceeds its frame limit")
        if session.frame_compression:
            response_packet = encode_phone_frame(response_packet)
        return {
            "packet": base64.b64encode(session.outgoing.encrypt(response_packet)).decode()
        }

    async def _call(self, identifier: str, session: PhoneSession, request: dict) -> dict:
        """Route one call op to the broker, bound to this phone's session object."""
        op = request.get("op")
        try:
            if op == "call_open":
                return await self.audio.open(
                    identifier,
                    session,
                    request.get("id"),
                    request.get("voice_session_id"),
                    request.get("generation"),
                )
            if op == "call_tick":
                try:
                    mic = base64.b64decode(request.get("mic"), validate=True)
                except (TypeError, ValueError):
                    raise PhoneCallError("call_invalid") from None
                return await self.audio.tick(
                    identifier, session, request.get("id"), request.get("n"), mic
                )
            await self.audio.close(identifier, session, request.get("id"))
            return {"ok": True}
        except PhoneCallError as exc:
            return {"error": exc.code}

    def _touch(self, identifier: str, row: dict) -> None:
        """Remember when this phone last spoke; saved at most once a minute."""
        now = int(time.time())
        if now - row["seen"] >= SEEN_SAVE_SECONDS:
            row["seen"] = now
            try:
                self._persist()
            except OSError:
                pass  # last-seen is informational; never fail the phone's request
            else:
                self._event(
                    "last_seen_updated",
                    identifier=identifier,
                    peer=row["key"],
                    last_seen_at=now,
                )

    def _phone_app(self, identifier: str, path: str) -> tuple[str, bytes]:
        directory = app_directory()
        client = self.clients.get(identifier)
        if directory is None or client is None:
            raise PhoneLinkError("The phone app is not built on this computer.")
        name = unquote(urlsplit(path).path).removeprefix("/phone-app/")
        if name == "manifest.json":
            return "application/json", signed_app_manifest(directory, client.public_key, client.sign)
        if not APP_NAME.fullmatch(name) or not (directory / name).is_file() or (directory / name).is_symlink():
            raise PhoneLinkError("That phone app file does not exist.")
        return "application/octet-stream", (directory / name).read_bytes()

    async def _http(
        self,
        phone: str,
        request: dict,
        *,
        chunk_size: int = LEGACY_CHUNK,
        coalesce_headers: bool = False,
        zero_body_autorun: bool = False,
    ) -> dict:
        operation = request.get("op")
        identifier = request.get("id")
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise PhoneLinkError("Invalid app request.")
        key = (phone, identifier)
        exchange = self.exchanges.get(key)
        if operation == "start":
            if exchange or sum(k[0] == phone for k in self.exchanges) >= 16:
                raise PhoneLinkError("Too many app requests. Try again shortly.")
            method, path, headers, size = (request.get(n) for n in ("method", "path", "headers", "size"))
            try:
                parsed = urlsplit(path) if isinstance(path, str) else None
            except ValueError:
                raise PhoneLinkError("Invalid app address.") from None
            decoded = unquote(parsed.path) if parsed else ""
            if (
                method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
                or not parsed
                or parsed.scheme
                or parsed.netloc
                or not decoded.startswith("/")
                or "\\" in decoded
                or "\x00" in decoded
                or any(part in {".", ".."} for part in decoded.split("/"))
                or parsed.fragment
                or decoded.split("/")[1] not in API_ROOTS
                or not voice_route_allowed(method, decoded)
            ):
                raise PhoneLinkError("This action is available only on the computer.")
            if type(size) is not int or not 0 <= size <= MAX_BODY or not isinstance(headers, list):
                raise PhoneLinkError("App request is too large.")
            clean_headers = []
            for entry in headers:
                if (
                    not isinstance(entry, list)
                    or len(entry) != 2
                    or any(not isinstance(v, str) or len(v) > 2048 or "\r" in v or "\n" in v for v in entry)
                ):
                    raise PhoneLinkError("Invalid app headers.")
                if entry[0].lower() in {"content-type", "accept"}:
                    clean_headers.append(entry)
            exchange = self.exchanges[key] = HttpExchange(
                method,
                path,
                clean_headers,
                size,
                chunk_size=chunk_size,
                coalesce_headers=coalesce_headers,
            )
            if decoded.split("/")[1] == "phone-app":
                if method != "GET" or size:
                    self.exchanges.pop(key, None)
                    raise PhoneLinkError("The phone app can only be read.")
                try:
                    exchange.static = self._phone_app(phone, path)
                except PhoneLinkError:
                    self.exchanges.pop(key, None)
                    raise
            if size == 0 and zero_body_autorun:
                serve = self._serve_static if exchange.static else self._serve
                exchange.task = asyncio.create_task(serve(exchange), name="kollab-phone-http")

            return {"ok": True}
        if exchange is None:
            raise PhoneLinkError("This app request has ended. Try again.")
        exchange.used = time.monotonic()
        if operation == "write":
            try:
                chunk = base64.b64decode(request["data"], validate=True)
            except KeyError, ValueError, TypeError:
                raise PhoneLinkError("Invalid upload chunk.") from None
            if (
                exchange.task
                or request.get("offset") != len(exchange.body)
                or len(chunk) > exchange.chunk_size
                or len(exchange.body) + len(chunk) > exchange.size
            ):
                raise PhoneLinkError("Upload changed or exceeded its limit.")
            exchange.body.extend(chunk)
            return {"offset": len(exchange.body)}
        if operation == "run":
            if exchange.task is None:
                if len(exchange.body) != exchange.size:
                    raise PhoneLinkError("Upload is incomplete.")
                serve = self._serve_static if exchange.static else self._serve
                exchange.task = asyncio.create_task(serve(exchange), name="kollab-phone-http")
            return {"ok": True}
        if operation == "cancel":
            if exchange.task:
                exchange.task.cancel()
            self.exchanges.pop(key, None)
            return {"ok": True}
        if operation == "read" and exchange.task:
            try:
                if exchange.pending is not None:
                    event, exchange.pending = exchange.pending, None
                else:
                    event = await asyncio.wait_for(exchange.events.get(), timeout=15)
            except TimeoutError:
                return {"type": "waiting"}
            event_type = event.get("type")
            if (event_type == "headers" and exchange.coalesce_headers) or event_type == "data":
                # Piggyback initial headers with the first body bytes. A short
                # bounded wait lets the ASGI sender queue the body and end marker
                # without turning streaming responses into an unbounded poll.
                body = bytearray(
                    base64.b64decode(event["data"]) if event_type == "data" else b""
                )
                expected_length = None
                if event_type == "headers":
                    for name, value in event.get("headers", []):
                        if str(name).lower() == "content-length":
                            try:
                                expected_length = max(0, int(value))
                            except (TypeError, ValueError):
                                expected_length = None
                            break
                target = (
                    min(exchange.chunk_size, expected_length)
                    if expected_length is not None
                    else exchange.chunk_size
                )
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 0.02 if event_type == "headers" else loop.time()
                ended = expected_length == 0
                while not ended and len(body) < target:
                    try:
                        extra = exchange.events.get_nowait()
                    except asyncio.QueueEmpty:
                        remaining = deadline - loop.time()
                        if event_type != "headers" or remaining <= 0:
                            break
                        try:
                            extra = await asyncio.wait_for(exchange.events.get(), timeout=remaining)
                        except asyncio.TimeoutError:
                            break
                    if extra.get("type") == "end":
                        if exchange.coalesce_headers:
                            ended = True
                        else:
                            exchange.pending = extra
                        break
                    if extra.get("type") != "data":
                        exchange.pending = extra
                        break
                    chunk = base64.b64decode(extra["data"])
                    if len(body) + len(chunk) > exchange.chunk_size:
                        exchange.pending = extra
                        break
                    body.extend(chunk)
                if expected_length is not None and len(body) >= expected_length:
                    ended = True
                    try:
                        trailing = exchange.events.get_nowait()
                    except asyncio.QueueEmpty:
                        trailing = None
                    if trailing is not None and trailing.get("type") != "end":
                        exchange.pending = trailing
                if body:
                    encoded_body = base64.b64encode(body).decode()
                    if event_type == "headers":
                        event["data"] = encoded_body
                    else:
                        event = {"type": "data", "data": encoded_body}
                if ended:
                    event["end"] = True
            if event.get("type") in ("end", "error") or event.get("end") is True:
                self.exchanges.pop(key, None)
            return event
        raise PhoneLinkError("Unknown app request.")

    async def _serve_static(self, exchange: HttpExchange) -> None:
        content_type, body = exchange.static
        await exchange.events.put({"type": "headers", "status": 200, "headers": [["content-type", content_type]]})
        for offset in range(0, len(body), exchange.chunk_size):
            await exchange.events.put(
                {"type": "data", "data": base64.b64encode(body[offset : offset + exchange.chunk_size]).decode()}
            )
        await exchange.events.put({"type": "end"})

    async def _serve(self, exchange: HttpExchange) -> None:
        parsed = urlsplit(exchange.path)
        headers = [(k.lower().encode(), v.encode()) for k, v in exchange.headers]
        headers += [
            (b"authorization", ("Bearer " + (get_token() or read_token_from_disk())).encode()),
            (b"host", b"127.0.0.1"),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": exchange.method,
            "scheme": "http",
            "path": unquote(parsed.path),
            "raw_path": parsed.path.encode(),
            "query_string": parsed.query.encode(),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 0),
        }
        received = False

        async def receive():
            nonlocal received
            if received:
                await asyncio.Future()  # cancellation is the disconnect signal
            received = True
            body = bytes(exchange.body)
            exchange.body.clear()
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                allowed = {b"content-type", b"content-disposition"}
                await exchange.events.put(
                    {
                        "type": "headers",
                        "status": message["status"],
                        "headers": [
                            [k.decode(), v.decode()] for k, v in message.get("headers", []) if k.lower() in allowed
                        ],
                    }
                )
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                for offset in range(0, len(body), exchange.chunk_size):
                    await exchange.events.put(
                        {"type": "data", "data": base64.b64encode(body[offset : offset + exchange.chunk_size]).decode()}
                    )
                if not message.get("more_body", False):
                    await exchange.events.put({"type": "end"})

        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            raise
        except Exception:
            await exchange.events.put({"type": "error", "error": "The app request failed on the computer."})

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            try:
                if self._task:
                    self._task.cancel()
                    await asyncio.gather(self._task, return_exceptions=True)
                for identifier in tuple(self.clients):
                    self._invalidate(identifier, cause="shutdown")
                await asyncio.gather(*(client.close() for client in self.clients.values()), return_exceptions=True)
            finally:
                self.clients.clear()
                self.records.clear()
                self._task = None
                self._loaded = False
                if self._owner_fd is not None:
                    self._drop_owner()
                    os.close(self._owner_fd)
                    self._owner_fd = None
