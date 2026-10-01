"""Which global settings a network's primary device manages here.

A secondary device applies the primary's sealed config (docs/specs/
agent-network-simple-flow.md section 9) into its own ``~/.kollab`` and records
what it applied in one small private file. ``/config`` reads that file to mark
the synced keys read-only with ``managed by <primary>``. The file holds names,
paths and digests only; the values live in ``config.json`` itself.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config_utils import get_config_directory

logger = logging.getLogger(__name__)

RECORD_VERSION = 1
MAX_RECORD_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ManagedConfig:
    """What the primary device manages on this device.

    ``keys`` are leaf paths in the global ``config.json`` as segment tuples
    (a profile named ``gpt-5.4`` keeps its dot inside one segment).
    ``files`` maps a path under ``~/.kollab`` (``skills/x/SKILL.md``) to the
    sha256 of the content the primary sent.
    """

    primary_key: str
    primary_name: str
    revision: int = 0
    digest: str = ""
    keys: tuple[tuple[str, ...], ...] = ()
    mcp_servers: tuple[str, ...] = ()
    files: dict[str, str] = field(default_factory=dict)


def managed_config_path() -> Path:
    """The private record; the directory is ``~/.kollab/private`` (mode 0700)."""
    return get_config_directory() / "private" / "managed-config.json"


def _to_json(record: ManagedConfig) -> dict:
    return {
        "version": RECORD_VERSION,
        "primary_key": record.primary_key,
        "primary_name": record.primary_name,
        "revision": record.revision,
        "digest": record.digest,
        "keys": [list(path) for path in record.keys],
        "mcp_servers": list(record.mcp_servers),
        "files": dict(record.files),
    }


def _from_json(data: object) -> ManagedConfig:
    if not isinstance(data, dict) or data.get("version") != RECORD_VERSION:
        raise ValueError("unsupported managed config record")
    keys = data["keys"]
    files = data["files"]
    if (
        not isinstance(data["primary_key"], str)
        or not isinstance(data["primary_name"], str)
        or type(data["revision"]) is not int
        or not isinstance(data["digest"], str)
        or not isinstance(keys, list)
        or not isinstance(data["mcp_servers"], list)
        or not isinstance(files, dict)
        or any(
            not isinstance(path, list)
            or not path
            or any(not isinstance(part, str) for part in path)
            for path in keys
        )
        or any(not isinstance(name, str) for name in data["mcp_servers"])
        or any(
            not isinstance(path, str) or not isinstance(digest, str)
            for path, digest in files.items()
        )
    ):
        raise ValueError("invalid managed config record")
    return ManagedConfig(
        primary_key=data["primary_key"],
        primary_name=data["primary_name"],
        revision=data["revision"],
        digest=data["digest"],
        keys=tuple(tuple(path) for path in keys),
        mcp_servers=tuple(data["mcp_servers"]),
        files=dict(files),
    )


def read_managed_config(path: Path | None = None) -> ManagedConfig | None:
    """The record, or None when this device is not managed. Never raises."""
    path = path or managed_config_path()
    try:
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ValueError("managed config record is too large")
        return _from_json(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        # A damaged record must not break /config; the next sync rewrites it.
        logger.warning("managed config record is unreadable; ignoring it")
        return None


def write_managed_config(record: ManagedConfig, path: Path | None = None) -> None:
    """Atomically replace the record (0600, in a 0700 directory)."""
    path = path or managed_config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps(_to_json(record), sort_keys=True, separators=(",", ":"))
    descriptor, temporary = tempfile.mkstemp(
        prefix=".managed-config.", suffix=".tmp", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def clear_managed_config(
    primary_key: str | None = None, path: Path | None = None
) -> bool:
    """Stop managing this device. With a key, only when that primary set it.

    Returns whether a record was removed. The synced values stay where they
    are: they become ordinary settings this device owns.
    """
    record = read_managed_config(path)
    if record is None:
        return False
    if primary_key is not None and record.primary_key != primary_key:
        return False
    try:
        (path or managed_config_path()).unlink()
    except FileNotFoundError:
        return False
    return True


def managed_by(config_path: str, record: ManagedConfig | None = None) -> str | None:
    """The primary's device name when ``config_path`` (dotted) is synced here.

    A path counts when it is a managed leaf or an ancestor of one, so a row
    bound to a whole section is marked when any key inside it is managed.
    """
    if record is None:
        record = read_managed_config()
    if record is None or not config_path:
        return None
    for segments in record.keys:
        dotted = ".".join(segments)
        if dotted == config_path or dotted.startswith(config_path + "."):
            return record.primary_name
    return None


__all__ = [
    "ManagedConfig",
    "clear_managed_config",
    "managed_by",
    "managed_config_path",
    "read_managed_config",
    "write_managed_config",
]
