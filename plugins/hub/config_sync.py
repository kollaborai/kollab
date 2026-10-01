"""Sealed config sync: what travels, how it is sealed, how a secondary applies it.

The primary (the device that issued the join code) sends its global settings
to every device it accepted. Constitution section 9:

  synced      global ``~/.kollab/config.json`` overrides, ``agents/``,
              ``skills/``, MCP servers, API keys
  never       OAuth logins, project ``.kollab/``, vaults, conversations,
              scratchpads, and the keys listed in ``LOCAL_ONLY`` below
  primary wins on every key it sends; a secondary marks them managed

This module is transport-free: it builds the snapshot, seals and opens
bundles, and applies them to disk. ``config_sync_service`` moves the bundles
over the network's secure conversation path, so the relay only ever carries
ciphertext. Nothing here logs or raises with a value: errors are fixed codes.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.public import SealedBox
from nacl.signing import SigningKey, VerifyKey

from kollabor_ai.profile_manager import KEYRING_SENTINEL_PREFIX, _keyring_get
from kollabor_config.config_utils import get_config_directory
from kollabor_config.managed_config import (
    ManagedConfig,
    read_managed_config,
    write_managed_config,
)

from .device_names import NAME_RE
from .provisioning import _canonical_json, _strict_json

logger = logging.getLogger(__name__)

# --- what travels ---------------------------------------------------------------

# Keys that describe this machine or the network's own plumbing, not the user's
# taste. They never leave the primary and a secondary refuses them on arrival:
# update-check cache, version stamps, the hub plugin (a primary must not be able
# to switch off or re-point another device's link), audio hardware, and this
# machine's approval mode (a laptop's trust must not silently widen a server's).
LOCAL_ONLY: tuple[tuple[str, ...], ...] = (
    ("kollabor", "updates"),
    ("kollabor", "permissions"),
    ("plugins", "hub"),
    ("plugins", "voice"),
    ("config_version",),
    ("last_app_version",),
    ("_config_version",),
)
# OAuth material is never config, wherever it might have landed.
TOKEN_NAMES = frozenset({"access_token", "refresh_token", "id_token", "oauth_tokens"})
SYNC_DIRS = ("agents", "skills")
SKIP_DIR_NAMES = frozenset({"__pycache__", ".git", ".hg", ".svn", "node_modules"})
SKIP_FILE_NAMES = frozenset({".DS_Store"})
SKIP_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp")
SENTINEL = KEYRING_SENTINEL_PREFIX

MAX_FILES = 1500
MAX_FILE_BYTES = 512 * 1024
# One secure request carries at most 64 KiB and the blob is base64 in the outer
# JSON, so a file (or batch) that compresses to more than this cannot travel.
# ponytail: such files are skipped and counted; add chunked put if a real skill
# needs one.
MAX_ZFILE_BYTES = 30_000
MAX_BATCH_FILES = 60
# The whole manifest is one request too: sealed and base64 encoded it must stay
# under the service MAX_BUNDLE_CHARS (about 1000 files worth, fewer than MAX_FILES).
MAX_MANIFEST_ZBYTES = 44_000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_CORE_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_AGE_SECONDS = 24 * 60 * 60

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DOMAIN = b"kollab-config-sync/1\0"
_MAGIC = b"KCS1"
_KINDS = frozenset({"core", "sync", "put"})


class ConfigSyncError(ValueError):
    """A fixed, value-free failure code (plus small integers a reply may carry)."""

    def __init__(self, code: str, **extra: int):
        self.code = code
        self.extra = extra
        super().__init__(code)


# --- leaves ---------------------------------------------------------------------


def walk_leaves(
    node: Any, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every non-dict value under ``node`` with its key path. Empty dicts vanish."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield from walk_leaves(value, path + (key,))
    elif path:
        yield path, node


def set_leaf(root: dict, path: tuple[str, ...], value: Any) -> None:
    node = root
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[path[-1]] = copy.deepcopy(value)


def delete_leaf(root: dict, path: tuple[str, ...]) -> None:
    """Remove one leaf and the parents it leaves empty."""
    chain = [root]
    for part in path[:-1]:
        child = chain[-1].get(part)
        if not isinstance(child, dict):
            return
        chain.append(child)
    chain[-1].pop(path[-1], None)
    for depth in range(len(path) - 1, 0, -1):
        if chain[depth]:
            break
        chain[depth - 1].pop(path[depth - 1], None)


def _dig(node: Any, *keys: str) -> Any:
    """``node[k1][k2]...`` or None when any level is missing or not a dict."""
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def is_local_only(path: tuple[str, ...]) -> bool:
    return path[-1] in TOKEN_NAMES or any(
        path[: len(prefix)] == prefix for prefix in LOCAL_ONLY
    )


def _default_keyring_get(name: str) -> str | None:
    return _keyring_get(name)


# --- snapshot -------------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    path: str  # relative to ~/.kollab, "/" separated
    sha256: str
    size: int
    executable: bool
    zsize: int


@dataclass(frozen=True)
class Snapshot:
    """Everything the primary sends. ``config`` and ``mcp`` hold secrets."""

    config: dict = field(repr=False)
    mcp: dict = field(repr=False)
    files: tuple[FileEntry, ...]
    digest: str
    files_digest: str
    skipped: int
    keep: tuple[tuple[str, ...], ...] = ()  # secrets the keyring would not give up

    def __repr__(self) -> str:
        return (
            f"Snapshot(digest={self.digest[:12]}, files={len(self.files)}, "
            f"skipped={self.skipped}, config=<redacted>)"
        )


def manifest_rows(files: list[FileEntry]) -> list[list]:
    return [[e.path, e.sha256, e.size, int(e.executable)] for e in files]


def _fit_manifest(files: list[FileEntry], skipped: int) -> tuple[list[FileEntry], int]:
    """Drop the last files until the manifest fits one secure request.

    They are skipped and counted like any file that cannot travel, and the
    partial flag keeps them on secondaries. ponytail: cuts a tenth per step.
    """
    while (
        files
        and len(pack_json({"manifest": manifest_rows(files)})) > MAX_MANIFEST_ZBYTES
    ):
        cut = max(1, len(files) // 10)
        files, skipped = files[:-cut], skipped + cut
    return files, skipped


def sync_body(snapshot: Snapshot) -> dict:
    """The signed header of a manifest. ``partial`` means some files did not fit,
    so a file missing from the manifest is not one the primary dropped."""
    return {"digest": snapshot.digest, "partial": snapshot.skipped > 0}


def core_blob(snapshot: Snapshot, primary_name: str) -> bytes:
    """What a core push seals: the settings and the device name that owns them."""
    return pack_json(
        {
            "config": snapshot.config,
            "mcp": snapshot.mcp,
            "keep": [list(path) for path in snapshot.keep],
            "primary_name": primary_name,
        }
    )


def _read_object(path: Path) -> dict:
    """A JSON object from ``path``; {} when the file does not exist."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ConfigSyncError("unreadable") from None
    if not isinstance(data, dict):
        raise ConfigSyncError("unreadable")
    return data


def _stat_signature(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def kollab_root(root: Path | None = None) -> Path:
    """``~/.kollab``, unless a caller (a test playing a second device) says otherwise."""
    return root if root is not None else get_config_directory()


def mcp_settings_path(root: Path | None = None) -> Path:
    return kollab_root(root) / "mcp" / "mcp_settings.json"


def record_path(root: Path | None) -> Path | None:
    """The managed-config record for ``root``; None means the default location."""
    return None if root is None else root / "private" / "managed-config.json"


def eligible_file(name: str) -> bool:
    return name not in SKIP_FILE_NAMES and not name.endswith(SKIP_SUFFIXES)


class SnapshotBuilder:
    """Builds snapshots cheaply: hashes only files whose stat changed."""

    def __init__(
        self,
        keyring_get: Callable[[str], str | None] | None = None,
        root: Path | None = None,
    ):
        self._root = root
        self._keyring_get = keyring_get or _default_keyring_get
        self._hashes: dict[str, tuple[int, int, str, int]] = {}
        self._resolved: dict[str, str] = {}
        self._core_signature: tuple | None = None
        self._core: tuple[dict, dict, tuple] = ({}, {}, ())
        self._missing: set[str] = set()  # keyring names the last pass could not read
        self._retry: set[str] | None = None  # the names the next pass reads again

    def retry_unresolved(self) -> None:
        """Read the keyring once more, for the names it could not give, on the next build.

        Called once per reconnect (`ConfigSyncService.tick`), so a keyring that
        unlocked after launch is picked up without waiting for config.json to
        change, and a missing macOS Keychain entry prompts at most once per
        reconnect, never on every poll. Names already read are not read again.
        """
        if self._missing:
            self._retry = set(self._missing)
            self._core_signature = None

    def build(self) -> Snapshot:
        config, mcp, keep = self._core_parts()
        files, skipped = _fit_manifest(*self._scan_files())
        files_digest = hashlib.sha256(_canonical_json(manifest_rows(files))).hexdigest()
        digest = hashlib.sha256(
            _canonical_json(
                {"config": config, "mcp": mcp, "keep": keep, "files": files_digest}
            )
        ).hexdigest()
        return Snapshot(config, mcp, tuple(files), digest, files_digest, skipped, keep)

    def _core_parts(self) -> tuple[dict, dict, tuple]:
        config_path = kollab_root(self._root) / "config.json"
        mcp_path = mcp_settings_path(self._root)
        signature = (_stat_signature(config_path), _stat_signature(mcp_path))
        if signature == self._core_signature:
            return self._core
        raw_config = _read_object(config_path)
        retry, self._retry = self._retry, None
        fetched: dict[str, str | None] = {}  # one keyring read per name per pass
        missing: set[str] = set()
        leaves: dict[tuple[str, ...], Any] = {}
        unresolved: list[tuple[str, ...]] = []
        profiles = _dig(raw_config, "kollabor", "llm", "profiles")
        for path, value in walk_leaves(raw_config):
            if is_local_only(path):
                continue
            if isinstance(value, str) and value.startswith(SENTINEL):
                # The sentinel means "the real key is in this machine's keyring".
                # A locked or busy keyring must not look like a deleted key, or
                # every device would lose it: fall back to the last value read.
                name = value[len(SENTINEL) :]
                if name not in fetched:
                    # A retry reads only the names that failed; the rest keep
                    # the value read before.
                    fetched[name] = (
                        self._keyring_get(name) if retry is None or name in retry else None
                    )
                value = fetched[name] or self._resolved.get(name)
                if not value:
                    # Say so, or secondaries read the gap as a deleted key. Not
                    # retried until config.json changes or the device reconnects
                    # (`retry_unresolved`): a missing macOS Keychain entry pops a
                    # dialog on every read.
                    unresolved.append(path)
                    missing.add(name)
                    continue
                self._resolved[name] = value
            if path[-1] == "api_key" and _is_oauth_profile(profiles, path):
                continue
            leaves[path] = value
        config: dict = {}
        for path, value in leaves.items():
            set_leaf(config, path, value)
        servers = _read_object(mcp_path).get("servers")
        mcp = (
            {
                name: server
                for name, server in servers.items()
                if isinstance(name, str) and isinstance(server, dict)
            }
            if isinstance(servers, dict)
            else {}
        )
        self._missing = missing
        self._core_signature, self._core = signature, (config, mcp, tuple(sorted(unresolved)))
        return self._core

    def _scan_files(self) -> tuple[list[FileEntry], int]:
        base = kollab_root(self._root)
        found: list[FileEntry] = []
        skipped = 0
        total = 0
        seen: set[str] = set()
        for top in SYNC_DIRS:
            stack = [(base / top, top)]
            while stack:
                directory, rel = stack.pop()
                try:
                    entries = sorted(os.scandir(directory), key=lambda e: e.name)
                except OSError:
                    continue
                for entry in entries:
                    child = f"{rel}/{entry.name}"
                    try:
                        if entry.is_symlink():
                            continue  # never follow: a link can point anywhere
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in SKIP_DIR_NAMES:
                                stack.append((Path(entry.path), child))
                            continue
                        if not entry.is_file(
                            follow_symlinks=False
                        ) or not eligible_file(entry.name):
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    hashed = self._hash(child, Path(entry.path), info)
                    if (
                        hashed is None
                        or len(found) >= MAX_FILES
                        or total + info.st_size > MAX_TOTAL_BYTES
                    ):
                        skipped += 1
                        continue
                    total += info.st_size
                    seen.add(child)
                    found.append(
                        FileEntry(
                            child,
                            hashed[0],
                            info.st_size,
                            bool(info.st_mode & 0o111),
                            hashed[1],
                        )
                    )
        for gone in set(self._hashes) - seen:
            self._hashes.pop(gone, None)
        found.sort(key=lambda e: e.path)
        return found, skipped

    def _hash(
        self, rel: str, path: Path, info: os.stat_result
    ) -> tuple[str, int] | None:
        """(sha256, compressed size), or None when the file cannot travel."""
        if info.st_size > MAX_FILE_BYTES:
            return None
        cached = self._hashes.get(rel)
        if cached and cached[:2] == (info.st_mtime_ns, info.st_size):
            sha, zsize = cached[2], cached[3]
        else:
            try:
                data = path.read_bytes()
            except OSError:
                return None
            sha, zsize = hashlib.sha256(data).hexdigest(), len(zlib.compress(data, 6))
            self._hashes[rel] = (info.st_mtime_ns, info.st_size, sha, zsize)
        return (sha, zsize) if zsize <= MAX_ZFILE_BYTES else None


def _is_oauth_profile(profiles: Any, path: tuple[str, ...]) -> bool:
    """`kollabor.llm.profiles.<name>.api_key` of a profile that logs in with OAuth."""
    if (
        len(path) != 5
        or path[:3] != ("kollabor", "llm", "profiles")
        or not isinstance(profiles, dict)
    ):
        return False
    profile = profiles.get(path[3])
    return isinstance(profile, dict) and profile.get("auth_type") == "oauth"


# --- sealing --------------------------------------------------------------------


def pack_json(value: Any) -> bytes:
    return zlib.compress(_canonical_json(value), 6)


def unpack_json(blob: bytes, *, limit: int) -> Any:
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(blob, limit + 1)
    except zlib.error:
        raise ConfigSyncError("invalid") from None
    if len(raw) > limit or not inflater.eof or inflater.unconsumed_tail:
        raise ConfigSyncError("invalid")
    try:
        return _strict_json(raw)
    except (ValueError, UnicodeError):
        raise ConfigSyncError("invalid") from None


def seal(
    kind: str,
    body: dict,
    blob: bytes,
    *,
    issuer_key: SigningKey,
    recipient_public_key: bytes,
    revision: int,
    now: int | None = None,
) -> bytes:
    """Sign ``body`` + ``blob`` as the primary and seal it to one device key.

    Same construction as the join-time provisioning bundle: Ed25519 signature
    with a domain prefix, then a libsodium SealedBox to the recipient's key, so
    only that device can read it and it can tell who wrote it.
    """
    if kind not in _KINDS or type(revision) is not int or not 0 <= revision < 2**53:
        raise ConfigSyncError("invalid")
    header = {
        "v": 1,
        "kind": kind,
        "issuer": bytes(issuer_key.verify_key).hex(),
        "recipient": recipient_public_key.hex(),
        "revision": revision,
        "issued_at": int(time.time()) if now is None else int(now),
        "body": body,
        "blob_sha256": hashlib.sha256(blob).hexdigest(),
    }
    header["signature"] = issuer_key.sign(
        _DOMAIN + _canonical_json(header)
    ).signature.hex()
    head = _canonical_json(header)
    plaintext = _MAGIC + len(head).to_bytes(4, "big") + head + blob
    try:
        return SealedBox(
            VerifyKey(recipient_public_key).to_curve25519_public_key()
        ).encrypt(plaintext)
    except (CryptoError, ValueError, TypeError):
        raise ConfigSyncError("invalid") from None


def open_sealed(
    sealed: bytes,
    *,
    recipient_key: SigningKey,
    issuer_public_key: bytes,
    kind: str,
    now: int | None = None,
) -> tuple[dict, bytes, int]:
    """Open a bundle addressed to ``recipient_key`` and signed by the primary."""
    try:
        plaintext = SealedBox(recipient_key.to_curve25519_private_key()).decrypt(sealed)
    except (CryptoError, ValueError, TypeError):
        raise ConfigSyncError("wrong_recipient") from None
    if len(plaintext) < 8 or plaintext[:4] != _MAGIC:
        raise ConfigSyncError("invalid")
    head_length = int.from_bytes(plaintext[4:8], "big")
    if not 2 <= head_length <= MAX_HEADER_BYTES or 8 + head_length > len(plaintext):
        raise ConfigSyncError("invalid")
    blob = plaintext[8 + head_length :]
    try:
        header = _strict_json(plaintext[8 : 8 + head_length])
    except (ValueError, UnicodeError):
        raise ConfigSyncError("invalid") from None
    fields = {
        "v", "kind", "issuer", "recipient", "revision", "issued_at", "body",
        "blob_sha256", "signature",
    }  # fmt: skip
    if not isinstance(header, dict) or set(header) != fields:
        raise ConfigSyncError("invalid")
    signature = header["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{128}", signature):
        raise ConfigSyncError("bad_signature")
    unsigned = {name: value for name, value in header.items() if name != "signature"}
    try:
        VerifyKey(issuer_public_key).verify(
            _DOMAIN + _canonical_json(unsigned), bytes.fromhex(signature)
        )
    except (BadSignatureError, CryptoError, ValueError, TypeError):
        raise ConfigSyncError("bad_signature") from None
    revision, issued_at = header["revision"], header["issued_at"]
    if (
        header["v"] != 1
        or type(header["v"]) is not int
        or header["kind"] != kind
        or header["issuer"] != issuer_public_key.hex()
        or header["recipient"] != bytes(recipient_key.verify_key).hex()
        or type(revision) is not int
        or not 0 <= revision < 2**53
        or type(issued_at) is not int
        or not isinstance(header["body"], dict)
        or header["blob_sha256"] != hashlib.sha256(blob).hexdigest()
    ):
        raise ConfigSyncError("invalid")
    if abs((int(time.time()) if now is None else now) - issued_at) > MAX_AGE_SECONDS:
        raise ConfigSyncError("expired")
    return header["body"], blob, revision


def pack_files(items: list[tuple[FileEntry, bytes]]) -> tuple[dict, bytes]:
    """A batch of files as (body, blob): each file zlib'd, the blobs concatenated."""
    meta, parts = [], []
    for entry, data in items:
        packed = zlib.compress(data, 6)
        meta.append(
            {"p": entry.path, "n": len(packed), "sha": entry.sha256,
             "sz": entry.size, "x": int(entry.executable)}  # fmt: skip
        )
        parts.append(packed)
    return {"files": meta}, b"".join(parts)


def unpack_files(body: dict, blob: bytes) -> list[tuple[dict, bytes]]:
    """Files of a verified batch, each checked against its signed sha256."""
    meta = body.get("files")
    if not isinstance(meta, list) or not 1 <= len(meta) <= MAX_BATCH_FILES:
        raise ConfigSyncError("invalid")
    out, offset = [], 0
    for item in meta:
        if (
            not isinstance(item, dict)
            or set(item) != {"p", "n", "sha", "sz", "x"}
            or type(item["n"]) is not int
            or type(item["sz"]) is not int
            or not 0 <= item["sz"] <= MAX_FILE_BYTES
            or not isinstance(item["sha"], str)
            or not _SHA.fullmatch(item["sha"])
            or item["x"] not in (0, 1)
            or not 0 < item["n"] <= MAX_ZFILE_BYTES
        ):
            raise ConfigSyncError("invalid")
        packed = blob[offset : offset + item["n"]]
        offset += item["n"]
        inflater = zlib.decompressobj()
        try:
            data = inflater.decompress(packed, MAX_FILE_BYTES + 1)
        except zlib.error:
            raise ConfigSyncError("invalid") from None
        if (
            len(data) != item["sz"]
            or not inflater.eof
            or hashlib.sha256(data).hexdigest() != item["sha"]
        ):
            raise ConfigSyncError("invalid")
        out.append((item, data))
    if offset != len(blob):
        raise ConfigSyncError("invalid")
    return out


def encode_need(flags: list[bool]) -> str:
    packed = bytearray((len(flags) + 7) // 8)
    for index, flag in enumerate(flags):
        if flag:
            packed[index // 8] |= 1 << (index % 8)
    return base64.b64encode(bytes(packed)).decode("ascii")


def decode_need(text: Any, count: int) -> list[bool]:
    if not isinstance(text, str):
        raise ConfigSyncError("invalid")
    try:
        packed = base64.b64decode(text, validate=True)
    except ValueError:
        raise ConfigSyncError("invalid") from None
    if len(packed) != (count + 7) // 8:
        raise ConfigSyncError("invalid")
    return [bool(packed[index // 8] >> (index % 8) & 1) for index in range(count)]


# --- applying on a secondary ----------------------------------------------------


@dataclass
class Applied:
    """What a core apply changed, so the running app can refresh."""

    config_changed: bool = False
    profiles_changed: bool = False
    mcp_changed: bool = False
    skipped_mcp: tuple[str, ...] = ()  # servers whose command is not installed here


def safe_parts(rel: Any) -> tuple[str, ...] | None:
    """The path segments of a syncable file, or None when it must be refused."""
    if (
        not isinstance(rel, str)
        or not 0 < len(rel) <= 512
        or "\\" in rel
        or "\0" in rel
        or rel.startswith("/")
    ):
        return None
    parts = tuple(rel.split("/"))
    if (
        len(parts) < 2
        or parts[0] not in SYNC_DIRS
        or any(part in ("", ".", "..") or len(part) > 255 for part in parts)
        or any(part in SKIP_DIR_NAMES for part in parts[:-1])
        or not eligible_file(parts[-1])
    ):
        return None
    return parts


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".kollab-sync-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_json(path: Path, value: dict) -> None:
    _write_atomic(
        path, json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8"), 0o600
    )


def _tighten(path: Path) -> None:
    """0600 even when an apply changed nothing: the file may hold the same key
    at a looser mode from before this device was managed (section 9)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # nothing on disk to tighten


def _file_path(parts: tuple[str, ...], root: Path | None = None) -> Path:
    """The target of a synced file. Refuses any symlink below the top folder."""
    current = kollab_root(root) / parts[0]  # the user may link the folder itself
    for part in parts[1:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ConfigSyncError("unsafe")
    target = current / parts[-1]
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ConfigSyncError("unsafe")
    return target


def _local_sha(parts: tuple[str, ...], root: Path | None = None) -> str | None:
    try:
        target = _file_path(parts, root)
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            return None
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except (OSError, ConfigSyncError):
        return None


def _local_executable(parts: tuple[str, ...], root: Path | None = None) -> bool:
    try:
        return bool(_file_path(parts, root).stat().st_mode & 0o111)
    except (OSError, ConfigSyncError):
        return False


def parse_manifest(value: Any) -> list[tuple[str, str, int, bool]]:
    if not isinstance(value, list) or len(value) > MAX_FILES:
        raise ConfigSyncError("invalid")
    entries, seen, total = [], set(), 0
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 4
            or safe_parts(item[0]) is None
            or item[0] in seen
            or not isinstance(item[1], str)
            or not _SHA.fullmatch(item[1])
            or type(item[2]) is not int
            or not 0 <= item[2] <= MAX_FILE_BYTES
            or item[3] not in (0, 1)
        ):
            raise ConfigSyncError("invalid")
        seen.add(item[0])
        total += item[2]
        entries.append((item[0], item[1], item[2], bool(item[3])))
    if total > MAX_TOTAL_BYTES:
        raise ConfigSyncError("invalid")
    return entries


def _mcp_missing(server: dict) -> bool:
    """A server whose command is not installed here: a bare name that ``shutil.which``
    cannot find, or a path that is missing or not executable. URL servers have none."""
    command = server.get("command")
    return isinstance(command, str) and bool(command) and shutil.which(command) is None


class Receiver:
    """The secondary's side: opens bundles from its primary and applies them.

    Blocking file work; the service runs each call in a thread, one at a time.
    A refusal is an ``{"error": code}`` reply, never an exception the transport
    would treat as a broken session.
    """

    def __init__(self, recipient_key: SigningKey, root: Path | None = None):
        self._key = recipient_key
        self._root = root
        # path -> (sha256, executable) of the files the last manifest still needs
        self._pending: dict[str, tuple[str, bool]] = {}
        self._pending_revision = -1

    # ---- core: config, mcp ----

    def core(
        self, sealed: bytes, *, primary_key: str, primary_name: str
    ) -> tuple[dict, Applied | None]:
        try:
            body, blob, revision = open_sealed(
                sealed,
                recipient_key=self._key,
                issuer_public_key=bytes.fromhex(primary_key),
                kind="core",
            )
            digest = body.get("digest")
            core = unpack_json(blob, limit=MAX_CORE_BYTES)
            if (
                not isinstance(digest, str)
                or not _SHA.fullmatch(digest)
                or not isinstance(core, dict)
            ):
                raise ConfigSyncError("invalid")
            record = read_managed_config(record_path(self._root))
            self._check(record, primary_key, revision, digest)
            name = core.get("primary_name")
            if not isinstance(name, str) or not NAME_RE.fullmatch(name):
                name = primary_name
            return {"ok": True}, self._apply_core(
                core, record, primary_key, name, revision, digest
            )
        except ConfigSyncError as error:
            return {"error": error.code, **error.extra}, None

    @staticmethod
    def _check(
        record: ManagedConfig | None, primary_key: str, revision: int, digest: str
    ) -> None:
        if record is None:
            return
        if record.primary_key != primary_key:
            raise ConfigSyncError("other_primary")
        if revision < record.revision or (
            revision == record.revision and digest != record.digest
        ):
            raise ConfigSyncError("stale", revision=record.revision)

    def _apply_core(
        self,
        core: dict,
        record: ManagedConfig | None,
        primary_key: str,
        primary_name: str,
        revision: int,
        digest: str,
    ) -> Applied:
        config, servers = core.get("config"), core.get("mcp")
        if not isinstance(config, dict) or not isinstance(servers, dict):
            raise ConfigSyncError("invalid")
        keep = core.get("keep", [])
        if not isinstance(keep, list) or not all(
            isinstance(path, list) and path and all(isinstance(part, str) for part in path)
            for path in keep
        ):
            raise ConfigSyncError("invalid")
        kept = {tuple(path) for path in keep}  # the primary could not read these
        # The primary already filters; a secondary never trusts that.
        leaves = {
            path: value
            for path, value in walk_leaves(config)
            if not is_local_only(path)
        }
        applied = Applied()
        config_path = kollab_root(self._root) / "config.json"
        current = _read_object(config_path)
        before = copy.deepcopy(current)
        previous = {tuple(path) for path in record.keys} if record else set()
        for path in previous - leaves.keys() - kept:
            delete_leaf(current, path)
        for path, value in leaves.items():
            set_leaf(current, path, value)
        if current != before:
            _write_json(config_path, current)
            applied.config_changed = True
            applied.profiles_changed = _llm_profiles(before) != _llm_profiles(current)
        _tighten(config_path)  # keys live here: 0600 on every apply, write or not
        mcp_path = mcp_settings_path(self._root)
        settings = _read_object(mcp_path)
        existing = settings.get("servers")
        existing = existing if isinstance(existing, dict) else {}
        merged = dict(existing)
        synced_mcp = set(record.mcp_servers) if record else set()
        for name in synced_mcp - servers.keys():
            merged.pop(name, None)
        skipped = set()
        for name, server in servers.items():
            if isinstance(name, str) and isinstance(server, dict):
                if _mcp_missing(server):  # never written, never a reason to touch a local one
                    skipped.add(name)
                else:
                    merged[name] = copy.deepcopy(server)
        applied.skipped_mcp = tuple(sorted(skipped))
        if merged != existing:
            settings["servers"] = merged
            _write_json(mcp_path, settings)
            applied.mcp_changed = True
        _tighten(mcp_path)  # server env can carry tokens: 0600 on every apply
        write_managed_config(
            ManagedConfig(
                primary_key=primary_key,
                primary_name=primary_name,
                revision=revision,
                digest=digest,
                keys=tuple(sorted(leaves.keys() | (previous & kept))),
                mcp_servers=tuple(
                    sorted(
                        n
                        for n in servers
                        if isinstance(n, str) and (n not in skipped or n in synced_mcp)
                    )
                ),
                files=dict(record.files) if record else {},
            ),
            record_path(self._root),
        )
        return applied

    # ---- files ----

    def sync(self, sealed: bytes, *, primary_key: str) -> dict:
        """Compare the primary's manifest with disk; finish when nothing is missing."""
        try:
            body, blob, revision = open_sealed(
                sealed,
                recipient_key=self._key,
                issuer_public_key=bytes.fromhex(primary_key),
                kind="sync",
            )
            prune = body.get("partial") is not True
            manifest = parse_manifest(
                unpack_json(blob, limit=MAX_MANIFEST_BYTES).get("manifest")
            )
            record = read_managed_config(record_path(self._root))
            if record is None:
                raise ConfigSyncError("no_core")
            self._check(record, primary_key, revision, record.digest)
            if revision != record.revision:
                raise ConfigSyncError("stale", revision=record.revision)
            need = [  # a mode-only change counts: the bytes alone would never move it
                _local_sha(safe_parts(rel), self._root) != sha
                or _local_executable(safe_parts(rel), self._root) != x
                for rel, sha, _size, x in manifest
            ]
            if any(need):
                self._pending = {
                    rel: (sha, x)
                    for (rel, sha, _size, x), n in zip(manifest, need)
                    if n
                }
                self._pending_revision = revision
                return {"need": encode_need(need), "n": sum(need), "applied": False}
            self._finish(record, manifest, prune=prune)
            self._pending, self._pending_revision = {}, -1
            return {"need": encode_need(need), "n": 0, "applied": True}
        except ConfigSyncError as error:
            return {"error": error.code, **error.extra}

    def put(self, sealed: bytes, *, primary_key: str) -> dict:
        """Write the files the last manifest asked for, each checked twice."""
        try:
            body, blob, revision = open_sealed(
                sealed,
                recipient_key=self._key,
                issuer_public_key=bytes.fromhex(primary_key),
                kind="put",
            )
            if revision != self._pending_revision:
                raise ConfigSyncError("resync")
            written = 0
            for item, data in unpack_files(body, blob):
                rel = item["p"]
                if self._pending.get(rel) != (item["sha"], bool(item["x"])):
                    raise ConfigSyncError("invalid")
                parts = safe_parts(rel)
                if parts is None:
                    raise ConfigSyncError("invalid")
                _write_atomic(
                    _file_path(parts, self._root), data, 0o755 if item["x"] else 0o644
                )
                written += 1
            return {"ok": written}
        except ConfigSyncError as error:
            return {"error": error.code, **error.extra}

    def _finish(
        self,
        record: ManagedConfig,
        manifest: list[tuple[str, str, int, bool]],
        prune: bool = True,
    ) -> None:
        """Every file is in place: remove what the primary dropped, remember the set.

        A partial manifest (``prune`` False) proves nothing about absent files:
        they stay, and stay remembered, until a complete manifest names them.
        """
        keep = {rel for rel, *_ in manifest}
        for rel in (set(record.files) - keep) if prune else ():
            parts = safe_parts(rel)
            if parts is None:
                continue
            try:
                target = _file_path(parts, self._root)
                if target.is_file():
                    target.unlink()
                parent, top = target.parent, kollab_root(self._root) / parts[0]
                while parent != top and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
            except (OSError, ConfigSyncError):
                continue
        write_managed_config(
            ManagedConfig(
                primary_key=record.primary_key,
                primary_name=record.primary_name,
                revision=record.revision,
                digest=record.digest,
                keys=record.keys,
                mcp_servers=record.mcp_servers,
                files={
                    **({} if prune else record.files),
                    **{rel: sha for rel, sha, _size, _x in manifest},
                },
            ),
            record_path(self._root),
        )


def _llm_profiles(config: dict) -> Any:
    return [
        _dig(config, "kollabor", "llm", name)
        for name in ("profiles", "active_profile", "default_profile")
    ]
