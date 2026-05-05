"""Persistence layer for Agent DNS.

Atomic JSON writes with flock for shared files.
Key file I/O with restrictive permissions for Ed25519 private keys.
Designation names are sanitized to prevent path traversal.
"""

import fcntl
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..presence import get_hub_dir
from .models import AgentRecord, ReputationScore

logger = logging.getLogger(__name__)

# Allowed characters in designation names (alphanumeric, hyphen, underscore)
_SAFE_DESIGNATION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _sanitize_designation(designation: str) -> str:
    """Sanitize a designation for use in filenames.

    Prevents path traversal attacks from user-supplied identities.
    """
    if not _SAFE_DESIGNATION_RE.match(designation):
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", designation)
        logger.warning(
            f"sanitized unsafe designation '{designation}' -> '{safe}'"
        )
        return safe
    return designation


def _locked_atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically with flock for multi-process safety.

    Uses a unique temp file (not fixed .tmp suffix) and flock on
    a .lock file to prevent concurrent writers from clobbering each
    other. Safe for shared files across multiple agent processes.
    """
    lock_path = path.with_suffix(".lock")
    fd = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)

        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    finally:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def get_dns_dir() -> Path:
    """Get the DNS directory, creating if needed."""
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

    def load_registry(self) -> Dict[str, AgentRecord]:
        """Load all agent records from registry.json."""
        if not self._registry_path.exists():
            return {}
        try:
            with open(self._registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = {}
            for designation, record_data in data.get("records", {}).items():
                records[designation] = AgentRecord.from_dict(record_data)
            return records
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"failed to load DNS registry: {e}")
            return {}

    def save_registry(self, records: Dict[str, AgentRecord]) -> None:
        """Save all agent records to registry.json with flock."""
        import time

        data = {
            "records": {k: v.to_dict() for k, v in records.items()},
            "version": 1,
            "updated_at": time.time(),
        }
        _locked_atomic_write(self._registry_path, data)

    # --- Reputation ---

    def load_reputation(self) -> Dict[str, ReputationScore]:
        """Load all reputation scores from reputation.json."""
        if not self._reputation_path.exists():
            return {}
        try:
            with open(self._reputation_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            scores = {}
            for designation, score_data in data.get("scores", {}).items():
                scores[designation] = ReputationScore.from_dict(score_data)
            return scores
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"failed to load DNS reputation: {e}")
            return {}

    def save_reputation(self, scores: Dict[str, ReputationScore]) -> None:
        """Save all reputation scores to reputation.json with flock."""
        import time

        data = {
            "scores": {k: v.to_dict() for k, v in scores.items()},
            "version": 1,
            "updated_at": time.time(),
        }
        _locked_atomic_write(self._reputation_path, data)

    # --- Capability Index ---

    def load_capability_index(self) -> Dict[str, list]:
        """Load the capability reverse index."""
        if not self._capability_index_path.exists():
            return {}
        try:
            with open(self._capability_index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"failed to load capability index: {e}")
            return {}

    def save_capability_index(self, index: Dict[str, list]) -> None:
        """Save the capability reverse index with flock."""
        _locked_atomic_write(self._capability_index_path, index)

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

    # --- Well-Known Export ---

    def write_well_known(self, record: "AgentRecord") -> Optional[Path]:
        """Write /.well-known/agent-keys JSON for AID compliance.

        Writes to ~/.kollab/hub/dns/well-known/agent-keys.json.
        Serve this file via nginx at /.well-known/agent-keys on your domain.

        Format follows AID spec: public key + AID + supported protocols.
        Remote meshes fetch this to verify attestations signed by this coordinator.
        """
        import time

        well_known_dir = self._dns_dir / "well-known"
        well_known_dir.mkdir(parents=True, exist_ok=True)
        out_path = well_known_dir / "agent-keys.json"

        payload: Dict[str, Any] = {
            "v": "aid1",
            "authority": record.authority,
            "coordinator": {
                "designation": record.designation,
                "aid": record.aid,
                "public_key": record.public_key,
                "key_type": "ed25519",
                "protocols": record.protocols,
            },
            "endpoints": {
                "registry": f"https://{record.authority}/.well-known/agent-keys",
                "socket": record.socket_path or "",
            },
            "published_at": time.time(),
        }

        if record.attestation:
            payload["coordinator"]["attestation"] = {
                "issuer": record.attestation.issuer,
                "signature": record.attestation.signature,
                "issued_at": record.attestation.issued_at,
            }

        try:
            _locked_atomic_write(out_path, payload)
            logger.info(f"wrote well-known agent-keys to {out_path}")
            self._sync_well_known(out_path)
            return out_path
        except OSError as e:
            logger.warning(f"failed to write well-known agent-keys: {e}")
            return None

    def _sync_well_known(self, out_path: Path) -> None:
        """Rsync well-known file to arch server if configured.

        Reads KOLLAB_WELL_KNOWN_RSYNC env var:
          almazan@192.168.68.172:~/.kollab/hub/dns/well-known/
        Fires and forgets — failure is non-fatal, just logged.
        """
        import subprocess

        dest = os.environ.get("KOLLAB_WELL_KNOWN_RSYNC", "")
        if not dest:
            return
        try:
            result = subprocess.run(
                ["rsync", "-q", str(out_path), dest],
                timeout=10,
                capture_output=True,
            )
            if result.returncode == 0:
                logger.info(f"synced well-known to {dest}")
            else:
                logger.warning(
                    f"rsync well-known failed (rc={result.returncode}): "
                    f"{result.stderr.decode().strip()}"
                )
        except Exception as e:
            logger.warning(f"rsync well-known error: {e}")

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
