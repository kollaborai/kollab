"""Origin-scoped publisher pins and revision history, separate from agent admission."""

import fcntl
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import rfc8785

from .discovery import (
    MAX_DOCUMENT,
    DiscoveryError,
    DiscoveryResult,
    decode_document,
    normalize_target,
    verify_manifest,
)
from .storage import _locked_atomic_write


class DiscoveryStore:
    """A discovered publisher never becomes an approved or routable Hub agent.

    The stored signed document doubles as the durable key pin and revision
    high-water mark. Expiry invalidates its advertised service information,
    but does not erase identity continuity or rollback protection.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def accept(self, result: DiscoveryResult) -> DiscoveryResult:
        target = normalize_target(result.discovery_url)
        # Revalidate at the persistence boundary, including elapsed expiry.
        current = verify_manifest(result.manifest, target)
        encoded = json.dumps(current.manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_DOCUMENT:
            raise DiscoveryError("document_too_large", "stored discovery document exceeds 64 KiB")
        if current.origin != result.origin:
            raise DiscoveryError("invalid_origin", "discovery source changed")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = hashlib.sha256(current.origin.encode("ascii")).hexdigest()
        path = self.directory / f"{name}.json"
        # Lock the entire read/compare/write, not only the atomic replacement.
        fd = os.open(self.directory / f"{name}.transaction.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if path.exists():
                with path.open("rb") as stream:
                    raw = stream.read(MAX_DOCUMENT + 1)
                try:
                    previous = decode_document(raw)
                    # Recheck the stored signature at its publication time. An
                    # expired document remains a pin, never a live route.
                    old = verify_manifest(previous, target, now=previous["published_at"])
                except (DiscoveryError, KeyError, TypeError) as exc:
                    raise DiscoveryError(
                        "cache_invalid", "stored publisher pin is invalid; refusing to reset trust"
                    ) from exc
                if old.publisher_principal_id != current.publisher_principal_id:
                    raise DiscoveryError("key_changed", "publisher key changed; explicit human re-pairing is required")
                if current.manifest["revision"] < previous["revision"]:
                    raise DiscoveryError("rollback", "publisher revision is older than the accepted revision")
                if current.manifest["revision"] == previous["revision"] and rfc8785.dumps(
                    current.manifest
                ) != rfc8785.dumps(previous):
                    raise DiscoveryError("revision_conflict", "same revision has different signed content")
                current = replace(current, identity_evidence="pinned-key")
            # Compact output preserves the same bound used for network input.
            _locked_atomic_write(path, current.manifest, compact=True)
            return current
        except (OSError, json.JSONDecodeError) as exc:
            raise DiscoveryError("cache_unavailable", "could not safely persist publisher identity") from exc
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
