"""Thin signed locator for an actually running, same-origin A2A service.

The standalone identity publisher never calls this helper. The A2A receiver
serves it alongside its real Card; generic interfaces and skills stay in that
Card. Refresh on GET, with a persistent revision counter and stable signer.
"""

import fcntl
import os
import time
from pathlib import Path

import rfc8785
from nacl.signing import SigningKey

from .discovery import (
    MAX_DOCUMENT,
    MAX_INTEGER,
    SCHEMA,
    WELL_KNOWN,
    DiscoveryError,
    decode_document,
    normalize_target,
    verify_manifest,
)
from .storage import _locked_atomic_write


def build_a2a_service_locator(origin: str, signing_key: SigningKey | bytes | str, state_path: Path) -> dict:
    """Return/renew a signed locator; never changes keys or resets revision state."""
    target = normalize_target(origin)
    if target.explicit:
        raise DiscoveryError("invalid_origin", "service locator requires an HTTPS origin")
    key = signing_key
    if isinstance(key, str):
        key = bytes.fromhex(key)
    if isinstance(key, bytes):
        key = SigningKey(key)
    if not isinstance(key, SigningKey):
        raise ValueError("signing_key must be an Ed25519 seed or SigningKey")
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(state_path) + ".transaction.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        now, revision = int(time.time()), 0
        public_key = key.verify_key.encode().hex()
        card_url = target.origin + "/.well-known/agent-card.json"
        if state_path.exists():
            with state_path.open("rb") as stream:
                previous = decode_document(stream.read(MAX_DOCUMENT + 1))
            verify_manifest(previous, target, now=previous["published_at"])
            if previous["coordinator"]["public_key"] != public_key:
                raise DiscoveryError("key_changed", "service key changed; explicit key rotation is required")
            if previous["endpoints"] != {"registry": target.origin + WELL_KNOWN, "agent_card": card_url}:
                raise DiscoveryError("locator_conflict", "state belongs to another service locator")
            revision = previous["revision"]
            if previous["published_at"] > now + 60:
                raise DiscoveryError("clock_rollback", "clock is older than persisted publication")
            if previous["published_at"] <= now < previous["published_at"] + 60:
                return previous
        if revision >= MAX_INTEGER:
            raise DiscoveryError("invalid_revision", "service revision cannot advance")
        payload = {
            "v": "aid1",
            "schema": SCHEMA,
            "authority": target.authority,
            "coordinator": {
                "designation": "discovery",
                "aid": f"agent:discovery@{target.authority}",
                "public_key": public_key,
                "key_type": "ed25519",
                "protocols": [SCHEMA],
            },
            "endpoints": {"registry": target.origin + WELL_KNOWN, "agent_card": card_url},
            "discovery": {"principal_id": "ed25519:" + public_key, "roles": [], "bootstrap": []},
            "revision": revision + 1,
            "published_at": now,
            "expires_at": now + 300,
        }
        payload["signature"] = key.sign(rfc8785.dumps(payload)).signature.hex()
        verify_manifest(payload, target)
        _locked_atomic_write(state_path, payload, compact=True)
        return payload
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
