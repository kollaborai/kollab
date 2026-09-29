"""Device names and agent@device handles for the agent network.

A device is one workspace identity with a human name. The constitution
(docs/specs/agent-network-simple-flow.md) says names, never keys, appear in
anything a human reads.
"""

from __future__ import annotations

import hashlib
import re
import socket
from pathlib import Path

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
HANDLE_RE = re.compile(r"(?P<agent>[a-z0-9][a-z0-9-]{0,62})@(?P<device>[a-z0-9][a-z0-9-]{0,62})")
TRUST_LEVELS = ("open", "agents", "manual")
DEFAULT_TRUST = "open"


def slug(text: str) -> str:
    """Lowercase, keep [a-z0-9-], collapse runs, trim; empty if nothing is left."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:63]


def validate_device_name(name: str) -> str:
    """Return the name if valid, else raise ValueError with the rule."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("device name: 1-63 chars, a-z 0-9 and dashes, starting with a letter or digit")
    return name


def default_device_name(workspace: Path | str | None) -> str:
    """`<hostname>-<workspace folder>`; the home folder is `<hostname>-home`."""
    host = slug(socket.gethostname().split(".")[0]) or "device"
    path = Path(workspace).expanduser() if workspace else Path.home()
    folder = "home" if path == Path.home() else (slug(path.name) or "workspace")
    return f"{host}-{folder}"[:63].rstrip("-")


def parse_handle(value: str) -> tuple[str, str] | None:
    """`agent@device` -> (agent, device); None when the text is not a handle."""
    match = HANDLE_RE.fullmatch((value or "").strip().lower())
    return (match.group("agent"), match.group("device")) if match else None


def format_handle(agent: str, device: str) -> str:
    return f"{agent}@{device}"


def validate_trust(level: str) -> str:
    if level not in TRUST_LEVELS:
        raise ValueError("trust: open, agents or manual")
    return level


def _require_key(public_key_hex: str) -> bytes:
    if not isinstance(public_key_hex, str) or not re.fullmatch(r"[0-9a-f]{64}", public_key_hex):
        raise ValueError("public key must be 64 lowercase hexadecimal characters")
    return bytes.fromhex(public_key_hex)


def device_key_fingerprint(public_key_hex: str) -> str:
    """Full sha256 device fingerprint of a 64-hex Ed25519 key.

    Shared by join requests (enrollment_client.py) and knock rows
    (contact_requests.py); humans see it through short_fingerprint().
    """
    return hashlib.sha256(
        b"kollab-relay-enrollment-device-fingerprint-v1\0" + _require_key(public_key_hex)
    ).hexdigest()


def short_fingerprint(fingerprint_hex: str) -> str:
    """`4d04...9f2e`: first 4 and last 4 hex of a fingerprint, for screens."""
    return f"{fingerprint_hex[:4]}\u2026{fingerprint_hex[-4:]}"


def contact_route_hex(public_key_hex: str) -> str:
    """The 16-hex fragment of a contact route, `<domain>/c/<hex>`.

    The relay's route index and the client's route check both derive it here,
    so a relay cannot point a route at a key that does not hash to it.
    """
    return hashlib.sha256(
        b"kollab-contact-route-v1\0" + _require_key(public_key_hex)
    ).hexdigest()[:16]


def key_label(public_key_hex: str) -> str:
    """A stand-in device name for a peer with no bound name: 8 hash-derived hex.

    Never the raw key (constitution section 13); the contact route already
    exposes exactly this much.
    """
    return contact_route_hex(public_key_hex)[:8]
