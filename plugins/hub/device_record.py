"""Machine-level device identity and workspace records for hub networking.

A device is a computer (issue #158): one device record per machine under
``~/.kollab/device/`` with a root signing key, per-network membership keys
under ``~/.kollab/device/networks/<origin-slug>/``, and per-workspace
network records under ``~/.kollab/workspaces/<workspace-digest>/networks/
<origin-slug>/`` holding today's ``RelayState`` unchanged. The wire key for
(device, network) stays the migrated folder key, so peers' approvals,
rooms and conversations keep validating with no re-joins.

Migration is one-shot and idempotent: an un-migrated
``~/.kollab/network/<digest>/`` state moves into the new layout on first
touch, preserving file bytes and permissions. Nothing is deleted; the old
directory stays so a v1 binary keeps a consistent view. Wire key
continuity is the contract: the moved ``device.key`` IS the membership key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

from nacl.signing import SigningKey

from .config_sync import kollab_root
from .device_names import validate_device_name
from .relay_state import KEY, RelayError, strict_json

DEVICE_DIR_NAME = "device"
WORKSPACES_DIR_NAME = "workspaces"
NETWORKS_DIR_NAME = "networks"
LEGACY_NETWORK_DIR_NAME = "network"
MAX_MEMBER_NETWORKS = 64  # matches provisioned MAX_NETWORKS
DEVICE_ID_HEX = re.compile(r"[0-9a-f]{32}\Z")
SLUG = re.compile(r"[0-9a-z][0-9a-z-]{0,62}\Z")


class DeviceRecordError(RelayError):
    """Device-record failure (layout, key, migration)."""


def origin_slug(origin: str) -> str:
    """Stable directory name for a canonical relay origin."""
    if not isinstance(origin, str) or not origin:
        raise DeviceRecordError("origin required for slug")
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]


def _check_private_dir(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise DeviceRecordError("device record directories must be private")


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    _check_private_dir(path)
    return path


def _read_private(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise DeviceRecordError("device record files must be private and owned by this user")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise DeviceRecordError("device record file too large")
        return raw


def _write_private(path: Path, data: bytes) -> None:
    """Atomic, durable, private write (0600 file in a 0700 tree)."""
    _ensure_private_dir(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_key(path: Path) -> SigningKey:
    seed = _read_private(path, 128).decode().strip()
    if not KEY.fullmatch(seed):
        raise DeviceRecordError(f"invalid key file: {path.name}")
    return SigningKey(bytes.fromhex(seed))


def _write_key(path: Path, key: SigningKey) -> None:
    _write_private(path, (key.encode().hex() + "\n").encode())


def device_dir(root: Path | None = None) -> Path:
    return kollab_root(root) / DEVICE_DIR_NAME


def workspaces_dir(root: Path | None = None) -> Path:
    return kollab_root(root) / WORKSPACES_DIR_NAME


def workspace_dir(workspace: Path, root: Path | None = None) -> Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    return workspaces_dir(root) / digest


class DeviceRecord:
    """The machine's device identity and its network memberships.

    Layout under ``~/.kollab/device/``::

        device.json   {"device_id", "device_name", "networks": {slug: entry}, "primary"}
        root.key      Ed25519 root signing key (never on any wire)
        networks/<slug>/key   membership key per (device, network)

    The root key exists so migration creates it once and local enrollment
    can later prove device ownership without a join code; it never appears
    in a wire protocol.
    """

    def __init__(self, root: Path | None = None):
        self.root = kollab_root(root)
        self.path = _ensure_private_dir(device_dir(self.root))
        self.networks_dir = _ensure_private_dir(self.path / NETWORKS_DIR_NAME)
        self.root_key_path = self.path / "root.key"
        self.device_json_path = self.path / "device.json"
        self._load()

    def _load(self) -> None:
        if self.root_key_path.exists():
            self.root_key = _read_key(self.root_key_path)
            payload = strict_json(_read_private(self.device_json_path, 65536).decode())
            self._validate(payload)
            self.payload = payload
            return
        self.root_key = SigningKey.generate()
        _write_key(self.root_key_path, self.root_key)
        self.payload = {
            "device_id": secrets.token_hex(16),
            "device_name": "",
            "networks": {},
            "primary": "",
        }
        self.save()

    def save(self) -> None:
        self._validate(self.payload)
        blob = json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()
        _write_private(self.device_json_path, blob)

    def _validate(self, payload: dict) -> None:
        # device_primary (PR #164) may write/read the flat spelling
        # ``primary_network`` (the network's name) alongside the slug
        # spelling ``primary``; it pops the flat field on every write of
        # its own, but a record carrying it must load, not raise. Accept
        # and normalize: tolerate the field, and when ``primary`` is unset
        # resolve the flat name against the networks map so both spellings
        # mean the same choice.
        if "primary_network" in payload:
            flat = payload.pop("primary_network")
            if "primary" not in payload:
                payload["primary"] = ""  # unknown/damaged name: no choice
            if not payload["primary"] and isinstance(flat, str) and flat:
                networks = payload.get("networks")
                if isinstance(networks, dict):
                    for slug, entry in networks.items():
                        if (
                            isinstance(entry, dict)
                            and entry.get("network_name") == flat
                        ):
                            payload["primary"] = slug
                            break
        if set(payload) - {"device_id", "device_name", "networks", "primary"}:
            raise DeviceRecordError("unsupported device record fields")
        device_id = payload.get("device_id")
        if not isinstance(device_id, str) or not DEVICE_ID_HEX.fullmatch(device_id):
            raise DeviceRecordError("invalid device identity")
        name = payload.get("device_name")
        if not isinstance(name, str):
            raise DeviceRecordError("invalid device name")
        if name:
            validate_device_name(name)
        networks = payload.get("networks")
        if not isinstance(networks, dict) or len(networks) > MAX_MEMBER_NETWORKS:
            raise DeviceRecordError("invalid networks map")
        for slug, entry in networks.items():
            if not isinstance(slug, str) or not SLUG.fullmatch(slug):
                raise DeviceRecordError("invalid network slug")
            if not isinstance(entry, dict) or set(entry) - {"origin", "network_name", "joined_at"}:
                raise DeviceRecordError("unsupported network entry fields")
            if not isinstance(entry.get("origin"), str) or not entry["origin"]:
                raise DeviceRecordError("network entry requires an origin")
            if not isinstance(entry.get("network_name"), str):
                raise DeviceRecordError("network entry requires a network name")
            if type(entry.get("joined_at")) is not int or entry["joined_at"] < 0:
                raise DeviceRecordError("network entry requires a non-negative joined_at")
        primary = payload.get("primary")
        if not isinstance(primary, str):
            raise DeviceRecordError("invalid primary network")
        if primary and primary not in networks:
            raise DeviceRecordError("primary must reference a known network slug")

    # -- accessors ---------------------------------------------------------

    @property
    def device_id(self) -> str:
        return self.payload["device_id"]

    @property
    def device_name(self) -> str:
        return self.payload["device_name"]

    @property
    def primary(self) -> str:
        return self.payload["primary"]

    def networks(self) -> dict[str, dict]:
        return dict(self.payload["networks"])

    def membership_key_path(self, slug: str) -> Path:
        return self.networks_dir / slug / "key"

    def membership_key(self, slug: str) -> SigningKey:
        path = self.membership_key_path(slug)
        if not path.exists():
            raise DeviceRecordError(f"no membership key for network {slug}")
        return _read_key(path)

    # -- mutation ----------------------------------------------------------

    def set_device_name(self, name: str) -> None:
        validate_device_name(name)
        self.payload["device_name"] = name
        self.save()

    def add_network(self, origin: str, network_name: str = "", *, joined_at: int = 0) -> str:
        """Register a network; mints a membership key when absent."""
        slug = origin_slug(origin)
        key_path = self.membership_key_path(slug)
        if not key_path.exists():
            _write_key(key_path, SigningKey.generate())
        if slug not in self.payload["networks"]:
            if len(self.payload["networks"]) >= MAX_MEMBER_NETWORKS:
                raise DeviceRecordError("network membership limit reached")
            self.payload["networks"][slug] = {
                "origin": origin,
                "network_name": network_name,
                "joined_at": joined_at,
            }
            if not self.payload["primary"]:
                self.payload["primary"] = slug
            self.save()
        return slug

    def adopt_membership_key(self, slug: str, key: SigningKey) -> None:
        """Install an existing (migrated) key as this network's membership key."""
        _write_key(self.membership_key_path(slug), key)


def migrate_legacy_network_state(
    workspace: Path, root: Path | None = None
) -> bool:
    """Move a v1 ``~/.kollab/network/<digest>/`` state into the device +
    workspace layout.

    Returns True when a migration happened. Idempotent: a second call
    finds the workspace record present and does nothing. Raises
    DeviceRecordError (moving nothing) on inconsistency.

    Wire-key continuity: the legacy folder ``device.key`` becomes the
    (device, network) membership key, so peers' approvals keep validating.
    The workspace's ``state.json`` (RelayState, conversations scope
    ``workspace_id``) moves byte-for-byte under
    ``workspaces/<digest>/networks/<slug>/``.
    """
    base = kollab_root(root)
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    legacy = base / LEGACY_NETWORK_DIR_NAME / digest
    if not legacy.is_dir():
        return False

    state_path = legacy / "state.json"
    payload: dict = {}
    if state_path.exists():
        payload = strict_json(_read_private(state_path, 65536).decode())
    key_path = legacy / "device.key"
    if not key_path.exists():
        raise DeviceRecordError("legacy state without a device key cannot migrate")

    origin = str(payload.get("origin") or "")
    record = DeviceRecord(root=root)
    # A never-joined legacy state still migrates: it pins a device name and
    # workspace identity worth keeping, but registers no network.
    if origin:
        slug = origin_slug(origin)
        membership_key_path = record.membership_key_path(slug)
        # The wire key for (device, network) IS the legacy folder key.
        # Adopt it BEFORE registering the network so add_network finds a
        # key present and never mints a replacement. When another folder
        # already claimed this network with a different key, the first key
        # stays canonical (design v2 §3) and this workspace keeps its own
        # legacy key inside its workspace record below.
        if not membership_key_path.exists():
            record.adopt_membership_key(slug, _read_key(key_path))
        record.add_network(
            origin, network_name=str(payload.get("network_name") or "")
        )
    else:
        slug = ""

    target_dir = workspace_dir(workspace, root) / NETWORKS_DIR_NAME / (slug or "unjoined")
    if target_dir.exists():
        return False  # already migrated by an earlier run
    _ensure_private_dir(target_dir)
    if state_path.exists():
        _write_private(target_dir / "state.json", _read_private(state_path, 65536))
    _write_private(target_dir / "device.key", _read_private(key_path, 128))
    return True
