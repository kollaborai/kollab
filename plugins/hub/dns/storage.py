"""Persistence layer for Agent DNS.

Atomic JSON writes with flock for shared files.
Key file I/O with restrictive permissions for Ed25519 private keys.
Designation names are sanitized to prevent path traversal.
"""

import json
import logging
import math
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


def _is_finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate_agent_record_data(designation: str, data: dict) -> None:
    """Reject malformed persisted fields before constructing a live record."""
    if data.get("designation") != designation:
        raise TypeError("record designation must match its registry key")

    string_fields = (
        "agent_id",
        "runtime",
        "authority",
        "socket_path",
        "endpoint_uri",
        "project",
        "public_key",
        "approval_state",
        "state",
        "current_task",
        "endpoint_state",
        "caste",
    )
    if any(field in data and not isinstance(data[field], str) for field in string_fields):
        raise TypeError("record string field has an invalid type")
    if "pid" in data and type(data["pid"]) is not int:
        raise TypeError("record pid must be an integer")
    if "is_coordinator" in data and type(data["is_coordinator"]) is not bool:
        raise TypeError("record coordinator flag must be a boolean")

    for field in ("trust_score", "registered_at", "last_seen", "last_endpoint_seen", "ttl"):
        if field in data and not _is_finite_number(data[field]):
            raise TypeError(f"record {field} must be a finite number")
    if "trust_score" in data and not 0 <= data["trust_score"] <= 1:
        raise ValueError("record trust score is out of range")
    if "ttl" in data and data["ttl"] < 0:
        raise ValueError("record ttl is negative")

    protocols = data.get("protocols", ["socket"])
    tags = data.get("tags", {})
    capabilities = data.get("capabilities", [])
    if not isinstance(protocols, list) or any(not isinstance(item, str) for item in protocols):
        raise TypeError("record protocols must be a list of strings")
    if not isinstance(tags, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in tags.items()
    ):
        raise TypeError("record tags must map strings to strings")
    if not isinstance(capabilities, list):
        raise TypeError("record capabilities must be a list")
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise TypeError("record capability must be an object")
        for field in ("name", "version", "evidence"):
            if field in capability and not isinstance(capability[field], str):
                raise TypeError(f"record capability {field} must be a string")
        for field in ("confidence", "last_demonstrated"):
            if field in capability and not _is_finite_number(capability[field]):
                raise TypeError(f"record capability {field} must be a finite number")
        if "confidence" in capability and not 0 <= capability["confidence"] <= 1:
            raise ValueError("record capability confidence is out of range")
        if "last_demonstrated" in capability and capability["last_demonstrated"] < 0:
            raise ValueError("record capability demonstration time is negative")
        if "endorsed_by" in capability and (
            not isinstance(capability["endorsed_by"], list)
            or any(not isinstance(item, str) for item in capability["endorsed_by"])
        ):
            raise TypeError("record capability endorsements must be strings")

    attestation = data.get("attestation")
    if attestation is not None:
        if not isinstance(attestation, dict):
            raise TypeError("record attestation must be an object")
        for field in ("subject", "issuer", "public_key", "signature", "attestation_type"):
            if field in attestation and not isinstance(attestation[field], str):
                raise TypeError(f"record attestation {field} must be a string")
        for field in ("issued_at", "expires_at"):
            if field in attestation and not _is_finite_number(attestation[field]):
                raise TypeError(f"record attestation {field} must be a finite number")


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
                _validate_agent_record_data(designation, record_data)
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
        base_records: Optional[Dict[str, dict]] = None,
    ) -> None:
        """Merge updates atomically; deletion markers reject stale snapshots."""
        with _file_lock(self._registry_path):
            existing, tombstones = self._read_registry_state_unlocked()
            tombstones.difference_update(created_keys)
            for key, record in records.items():
                if key in tombstones and key not in created_keys:
                    continue
                base = (base_records or {}).get(key)
                current = existing.get(key)
                if base is None or current is None or key in created_keys:
                    existing[key] = record
                    continue
                current_data = current.to_dict()
                incoming_data = record.to_dict()
                for field, value in incoming_data.items():
                    if base.get(field) != value:
                        current_data[field] = value
                existing[key] = AgentRecord.from_dict(current_data)
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
            counter_fields = (
                "tasks_completed",
                "tasks_failed",
                "tasks_abandoned",
                "uptime_sessions",
            )
            endorsements = score_data.get("endorsements", [])
            if (
                score_data.get("designation") != designation
                or any(
                    type(score_data.get(field, 0)) is not int
                    or score_data.get(field, 0) < 0
                    for field in counter_fields
                )
                or any(
                    not _is_finite_number(score_data.get(field, default))
                    or score_data.get(field, default) < 0
                    for field, default in (
                        ("avg_response_time_ms", 0.0),
                        ("total_uptime_seconds", 0.0),
                        ("last_updated", 0.0),
                    )
                )
                or not isinstance(endorsements, list)
                or any(
                    not isinstance(item, dict)
                    or not isinstance(item.get("from_designation"), str)
                    or not isinstance(item.get("to_designation"), str)
                    or not isinstance(item.get("capability", ""), str)
                    or not isinstance(item.get("signature", ""), str)
                    or not _is_finite_number(item.get("endorsed_at", 0.0))
                    or item.get("endorsed_at", 0.0) < 0
                    or not _is_finite_number(item.get("weight", 1.0))
                    or item.get("weight", 1.0) < 0
                    for item in endorsements
                )
            ):
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

    def save_reputation(
        self,
        scores: Dict[str, ReputationScore],
        *,
        base_scores: Optional[Dict[str, ReputationScore]] = None,
    ) -> Dict[str, ReputationScore]:
        """Merge reputation scores under the cross-process file lock."""
        import copy
        import time

        with _file_lock(self._reputation_path):
            existing = self._read_reputation_unlocked()
            if base_scores is None:
                conflicting = [
                    designation
                    for designation, score in scores.items()
                    if designation in existing and existing[designation] != score
                ]
                if conflicting:
                    raise ValueError(
                        "base_scores is required to update existing reputation "
                        "scores safely"
                    )
                existing.update(scores)
            else:
                counter_fields = (
                    "tasks_completed",
                    "tasks_failed",
                    "tasks_abandoned",
                    "uptime_sessions",
                    "total_uptime_seconds",
                )
                for designation, score in scores.items():
                    base = base_scores.get(designation)
                    current = existing.get(designation)
                    if base is None or current is None:
                        base = base or ReputationScore(designation=designation)
                        current = current or ReputationScore(designation=designation)
                        if designation not in existing:
                            existing[designation] = copy.deepcopy(score)
                            continue
                    else:
                        current = copy.deepcopy(current)

                    current_task_total = current.total_tasks
                    for field in counter_fields:
                        delta = getattr(score, field) - getattr(base, field)
                        setattr(current, field, getattr(current, field) + delta)
                    response_time_delta = (
                        score.avg_response_time_ms * score.total_tasks
                        - base.avg_response_time_ms * base.total_tasks
                    )
                    if current.total_tasks > 0:
                        current.avg_response_time_ms = (
                            current.avg_response_time_ms * current_task_total
                            + response_time_delta
                        ) / current.total_tasks
                    base_endorsements = {
                        (item.from_designation, item.capability): item
                        for item in base.endorsements
                    }
                    for item in score.endorsements:
                        key = (item.from_designation, item.capability)
                        previous = base_endorsements.get(key)
                        if previous is None or previous != item:
                            for index, current_item in enumerate(current.endorsements):
                                if (
                                    current_item.from_designation,
                                    current_item.capability,
                                ) == key:
                                    current.endorsements[index] = copy.deepcopy(item)
                                    break
                            else:
                                current.endorsements.append(copy.deepcopy(item))
                    current.last_updated = max(current.last_updated, score.last_updated)
                    existing[designation] = current
            data = {
                "scores": {k: v.to_dict() for k, v in existing.items()},
                "version": 1,
                "updated_at": time.time(),
            }
            _atomic_write_unlocked(self._reputation_path, data)
            return existing

    # --- Capability Index ---

    def load_capability_index(self) -> Dict[str, list]:
        """Load the capability reverse index."""
        with _file_lock(self._capability_index_path):
            return self._read_capability_index_unlocked()

    def save_capability_index(
        self,
        index: Dict[str, list],
        *,
        base_entries=None,
        created_entries=None,
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

                for capability_name, entries in index.items():
                    current_entries = entries_by_capability.get(capability_name, {})
                    for entry in entries:
                        designation = entry["designation"]
                        current_entry = current_entries.get(designation)
                        has_baseline = (
                            base_entries is not None
                            and (capability_name, designation) in base_entries
                        )
                        if (
                            current_entry is not None
                            and current_entry != entry
                            and not has_baseline
                        ):
                            raise ValueError(
                                "base_entries is required to update existing "
                                "capabilities safely"
                            )

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
                        designation = entry["designation"]
                        if (
                            created_entries is None
                            or (capability_name, designation) in created_entries
                            or designation in capability_entries
                        ):
                            base_entry = (base_entries or {}).get(
                                (capability_name, designation)
                            )
                            current_entry = capability_entries.get(designation)
                            if base_entry is None or current_entry is None:
                                capability_entries[designation] = entry
                                continue
                            base_capability = base_entry.get("capability", {})
                            current_capability = current_entry.get("capability", {})
                            incoming_capability = entry.get("capability", {})
                            merged_capability = dict(current_capability)
                            for field, value in incoming_capability.items():
                                if base_capability.get(field) != value:
                                    merged_capability[field] = value
                            merged_entry = dict(current_entry)
                            merged_entry.update(entry)
                            merged_entry["capability"] = merged_capability
                            capability_entries[designation] = merged_entry

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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
        for capability_name, entries in data.items():
            for entry in entries:
                capability = entry.get("capability") if isinstance(entry, dict) else None
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("designation"), str)
                    or not isinstance(capability, dict)
                    or capability.get("name") != capability_name
                    or not isinstance(capability.get("name"), str)
                    or not isinstance(capability.get("version", "1.0"), str)
                    or not isinstance(
                        capability.get("evidence", "self-declared"), str
                    )
                    or not _is_finite_number(capability.get("confidence", 0.5))
                    or not 0 <= capability.get("confidence", 0.5) <= 1
                    or not _is_finite_number(
                        capability.get("last_demonstrated", 0.0)
                    )
                    or capability.get("last_demonstrated", 0.0) < 0
                    or not isinstance(capability.get("endorsed_by", []), list)
                    or any(
                        not isinstance(item, str)
                        for item in capability.get("endorsed_by", [])
                    )
                ):
                    raise DNSStorageReadError(
                        "capability index",
                        self._capability_index_path,
                        "an invalid entry schema",
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
