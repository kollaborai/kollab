"""Device names and agent@device handles for the agent network.

A device is one workspace identity with a human name. The constitution
(docs/specs/agent-network-simple-flow.md) says names, never keys, appear in
anything a human reads.
"""

from __future__ import annotations

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
