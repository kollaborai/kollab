"""Persistence layer for Agent DNS.

Atomic JSON writes with flock for shared files.
Key file I/O with restrictive permissions for Ed25519 private keys.
Designation names are sanitized to prevent path traversal.
"""

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .. import file_lock
from .models import AgentRecord, ReputationScore

logger = logging.getLogger(__name__)

# Allowed characters in designation names (alphanumeric, hyphen, underscore)
_SAFE_DESIGNATION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class DNSStorageReadError(RuntimeError):
    """Existing DNS persistence data could not be read safely."""

    def __init__(self, label: str, path: Path, reason: str):
        super().__init__(
            f"DNS {label} at {path} has {reason}; the original was preserved. "
            "Back it up, repair the file, then retry."
        )


class RegistryReadError(DNSStorageReadError):
    """An existing DNS registry could not be read safely."""

    def __init__(self, path: Path, reason: str):
        super().__init__("registry", path, reason)


def _sanitize_designation(designation: str) -> str:
    """Sanitize a designation for use in filenames.

    Prevents path traversal attacks from user-supplied identities.
    """
    if not _SAFE_DESIGNATION_RE.match(designation):
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", designation)
        logger.warning(f"sanitized unsafe designation '{designation}' -> '{safe}'")
        return safe
    return designation


def _locked_atomic_write(path: Path, data: dict, *, compact: bool = False) -> None:
    """Write JSON atomically with flock for multi-process safety.

    Uses a unique temp file (not fixed .tmp suffix) and flock on
    a .lock file to prevent concurrent writers from clobbering each
    other. Safe for shared files across multiple agent processes.
    """
    with _file_lock(path):
        _atomic_write_unlocked(path, data, compact=compact)


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Hold the cross-process lock associated with a persisted JSON file."""
    lock_path = path.with_suffix(".lock")
    fd = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        file_lock.lock(fd)
        yield
    finally:
        if fd is not None:
            file_lock.unlock(fd)
            os.close(fd)


def _atomic_write_unlocked(
    path: Path, data: dict, *, compact: bool = False
) -> None:
    """Atomically replace a JSON file while its caller holds its lock."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=not compact,
                indent=None if compact else 2,
                separators=(",", ":") if compact else None,
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        # Pins and publisher revision counters must survive a crash after
        # replacement, not merely be atomically visible to other readers.
        # Directory fsync is a POSIX durability concept: Windows cannot
        # open a directory fd through the CRT (EACCES without
        # FILE_FLAG_BACKUP_SEMANTICS) and exposes no flush for it, so
        # only POSIX gets the crash-survival pin.
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_dns_dir() -> Path:
    """Get the DNS directory, creating if needed."""
    from ..presence import get_hub_dir

    d = get_hub_dir() / "dns"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_keys_dir() -> Path:
    """Get the keys directory, creating if needed."""
    d = get_dns_dir() / "keys"
    d.mkdir(parents=True, exist_ok=True)
    return d


class DNSStorage:
    """Persistence for DNS registry, reputation, capability index, and keys."""

    def __init__(self, dns_dir: Optional[Path] = None):
        self._dns_dir = dns_dir or get_dns_dir()
        self._dns_dir.mkdir(parents=True, exist_ok=True)
        self._keys_dir = self._dns_dir / "keys"
        self._keys_dir.mkdir(parents=True, exist_ok=True)

        self._registry_path = self._dns_dir / "registry.json"
        self._reputation_path = self._dns_dir / "reputation.json"
        self._capability_index_path = self._dns_dir / "capability_index.json"
        self._coordinator_pub_path = self._dns_dir / "coordinator.pub"

    # --- Registry ---

    def _read_registry_state_unlocked(self):
        """Read registry records and deletion markers under the file lock."""
        if not self._registry_path.exists():
            return {}, set()
        try:
            with open(self._registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RegistryReadError(self._registry_path, "malformed JSON") from exc
        except OSError as exc:
            raise RegistryReadError(self._registry_path, "I/O error") from exc

        if (
            not isinstance(data, dict)
            or (
                "version" in data
                and (type(data["version"]) is not int or data["version"] != 1)
            )
            or not isinstance(data.get("records"), dict)
            or not isinstance(data.get("deleted", []), list)
            or any(not isinstance(key, str) for key in data.get("deleted", []))
        ):
            raise RegistryReadError(self._registry_path, "invalid schema")

        records: Dict[str, AgentRecord] = {}
        try:
            for designation, record_data in data["records"].items():
                if not isinstance(designation, str) or not isinstance(
                    record_data, dict
                ):
                    raise TypeError("registry records must map names to objects")
                records[designation] = AgentRecord.from_dict(record_data)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RegistryReadError(
                self._registry_path, "invalid record schema"
            ) from exc
        return records, set(data.get("deleted", []))

    def _read_registry_unlocked(self) -> Dict[str, AgentRecord]:
        """Read and validate registry records while holding its file lock."""
        records, _ = self._read_registry_state_unlocked()
        return records

    @staticmethod
    def _registry_document(
        records: Dict[str, AgentRecord], deleted_keys=()
    ) -> dict:
        import time

        return {
            "records": {k: v.to_dict() for k, v in records.items()},
            "deleted": sorted(deleted_keys),
            "version": 1,
            "updated_at": time.time(),
        }

    def load_registry(self) -> Dict[str, AgentRecord]:
        """Load all agent records from registry.json."""
        with _file_lock(self._registry_path):
            return self._read_registry_unlocked()

    def save_registry(self, records: Dict[str, AgentRecord]) -> None:
        """Replace registry.json under its cross-process lock."""
        with _file_lock(self._registry_path):
            _atomic_write_unlocked(
                self._registry_path, self._registry_document(records)
            )

    def merge_save_registry(
        self,
        records: Dict[str, AgentRecord],
        deleted_keys: Optional[List[str]] = None,
        created_keys=(),
    ) -> None:
        """Merge updates atomically; deletion markers reject stale snapshots."""
        with _file_lock(self._registry_path):
            existing, tombstones = self._read_registry_state_unlocked()
            tombstones.difference_update(created_keys)
            existing.update(
                {
                    key: record
                    for key, record in records.items()
                    if key not in tombstones or key in created_keys
                }
            )
            for key in deleted_keys or []:
                existing.pop(key, None)
                tombstones.add(key)
            _atomic_write_unlocked(
                self._registry_path,
                self._registry_document(existing, tombstones),
            )

    # --- Reputation ---

    def _read_reputation_unlocked(self) -> Dict[str, ReputationScore]:
        """Read and validate reputation data while the caller holds its lock."""
        if not self._reputation_path.exists():
            return {}
        try:
            with open(self._reputation_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise DNSStorageReadError(
                "reputation", self._reputation_path, "malformed JSON"
            ) from exc
        except OSError as exc:
            raise DNSStorageReadError(
                "reputation", self._reputation_path, "an I/O error"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("scores"), dict):
            raise DNSStorageReadError(
                "reputation", self._reputation_path, "an invalid schema"
            )
        scores: Dict[str, ReputationScore] = {}
        for designation, score_data in data.get("scores", {}).items():
            if not isinstance(designation, str) or not isinstance(score_data, dict):
                raise DNSStorageReadError(
                    "reputation", self._reputation_path, "an invalid score schema"
                )
            try:
                scores[designation] = ReputationScore.from_dict(score_data)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise DNSStorageReadError(
                    "reputation", self._reputation_path, "an invalid score schema"
                ) from exc
        return scores

    def load_reputation(self) -> Dict[str, ReputationScore]:
        """Load all reputation scores from reputation.json."""
        with _file_lock(self._reputation_path):
            return self._read_reputation_unlocked()

    def save_reputation(self, scores: Dict[str, ReputationScore]) -> None:
        """Merge reputation scores under the cross-process file lock."""
        import time

        with _file_lock(self._reputation_path):
            existing = self._read_reputation_unlocked()
            existing.update(scores)
            data = {
                "scores": {k: v.to_dict() for k, v in existing.items()},
                "version": 1,
                "updated_at": time.time(),
            }
            _atomic_write_unlocked(self._reputation_path, data)

    # --- Capability Index ---

    def load_capability_index(self) -> Dict[str, list]:
        """Load the capability reverse index."""
        with _file_lock(self._capability_index_path):
            return self._read_capability_index_unlocked()

    def save_capability_index(
        self,
        index: Dict[str, list],
        *,
        deleted_entries=(),
        deleted_designations=(),
        replace: bool = False,
    ) -> None:
        """Merge capability updates with the latest index under its file lock."""
        with _file_lock(self._capability_index_path):
            if replace:
                merged = index
            else:
                merged = self._read_capability_index_unlocked()
                entries_by_capability = {
                    name: {entry["designation"]: entry for entry in entries}
                    for name, entries in merged.items()
                }

                for designation in deleted_designations:
                    for entries in entries_by_capability.values():
                        entries.pop(designation, None)
                for capability_name, designation in deleted_entries:
                    entries_by_capability.get(capability_name, {}).pop(
                        designation, None
                    )
                for capability_name, entries in index.items():
                    capability_entries = entries_by_capability.setdefault(
                        capability_name, {}
                    )
                    for entry in entries:
                        capability_entries[entry["designation"]] = entry

                merged = {
                    name: list(entries.values())
                    for name, entries in entries_by_capability.items()
                    if entries
                }
            _atomic_write_unlocked(self._capability_index_path, merged)

    def _read_capability_index_unlocked(self) -> Dict[str, list]:
        """Read and validate the capability index while its lock is held."""
        if not self._capability_index_path.exists():
            return {}
        try:
            with open(self._capability_index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise DNSStorageReadError(
                "capability index", self._capability_index_path, "malformed JSON"
            ) from exc
        except OSError as exc:
            raise DNSStorageReadError(
                "capability index", self._capability_index_path, "an I/O error"
            ) from exc
        if not isinstance(data, dict) or any(
            not isinstance(name, str) or not isinstance(entries, list)
            for name, entries in data.items()
        ):
            raise DNSStorageReadError(
                "capability index", self._capability_index_path, "an invalid schema"
            )
        return data

    # --- Key Files ---

    def save_private_key(self, designation: str, key_bytes: bytes) -> None:
        """Save a private key atomically with 0o600 from creation."""
        safe = _sanitize_designation(designation)
        key_path = self._keys_dir / f"{safe}.key"
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self._keys_dir, prefix=f".{safe}.", suffix=".key.tmp"
        )
        try:
            os.fchmod(tmp_fd, 0o600)
            with os.fdopen(tmp_fd, "wb") as f:
                tmp_fd = -1  # fd now owned by file object
                f.write(key_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, key_path)
        except BaseException:
            if tmp_fd >= 0:
                os.close(tmp_fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load_private_key(self, designation: str) -> Optional[bytes]:
        """Load a private key, or None if not found."""
        safe = _sanitize_designation(designation)
        key_path = self._keys_dir / f"{safe}.key"
        if not key_path.exists():
            return None
        try:
            return key_path.read_bytes()
        except OSError as e:
            logger.warning(f"failed to load private key for {safe}: {e}")
            return None

    def save_public_key(self, designation: str, key_hex: str) -> None:
        """Save a public key (hex string)."""
        safe = _sanitize_designation(designation)
        key_path = self._keys_dir / f"{safe}.pub"
        key_path.write_text(key_hex, encoding="utf-8")

    def load_public_key(self, designation: str) -> Optional[str]:
        """Load a public key (hex string), or None if not found."""
        safe = _sanitize_designation(designation)
        key_path = self._keys_dir / f"{safe}.pub"
        if not key_path.exists():
            return None
        try:
            return key_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning(f"failed to load public key for {safe}: {e}")
            return None

    def save_coordinator_pub(self, key_hex: str) -> None:
        """Save the coordinator's public key for verification by all agents."""
        self._coordinator_pub_path.write_text(key_hex, encoding="utf-8")

    def load_coordinator_pub(self) -> Optional[str]:
        """Load the coordinator's public key."""
        if not self._coordinator_pub_path.exists():
            return None
        try:
            return self._coordinator_pub_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning(f"failed to load coordinator public key: {e}")
            return None

    # --- Reputation Events (append-only for non-coordinators) ---

    def append_reputation_event(self, event: Dict[str, Any]) -> None:
        """Append a reputation event to the pending events log.

        Non-coordinators write events here; the coordinator processes
        them on each heartbeat cycle.
        """
        events_path = self._dns_dir / "reputation_events.jsonl"
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def drain_reputation_events(self) -> list:
        """Read and clear all pending reputation events.

        Coordinator-only. Uses atomic rotate: rename to a temp file,
        then read from it. This prevents losing events appended
        between read and truncate.
        """
        events_path = self._dns_dir / "reputation_events.jsonl"
        if not events_path.exists():
            return []
        rotated = events_path.with_suffix(f".{os.getpid()}.draining")
        try:
            os.replace(events_path, rotated)
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.warning(f"failed to rotate reputation events: {e}")
            return []
        try:
            with open(rotated, "r", encoding="utf-8") as f:
                lines = f.readlines()
            events = []
            for line in lines:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
            return events
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"failed to read rotated reputation events: {e}")
            return []
        finally:
            try:
                os.unlink(rotated)
            except OSError:
                pass
