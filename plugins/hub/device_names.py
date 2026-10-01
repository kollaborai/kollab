"""Device names and agent@device handles for the agent network.

A device is one workspace identity with a human name. The constitution
(docs/specs/agent-network-simple-flow.md) says names, never keys, appear in
anything a human reads.
"""

from __future__ import annotations

import hashlib
import re
import socket
import unicodedata
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


def validate_network_name(name: str) -> str:
    """Return the name if valid, else raise ValueError with the rule.

    A network name follows the device-name rule, so it fits anywhere one does.
    """
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("network name: 1-63 chars, a-z 0-9 and dashes, starting with a letter or digit")
    return name


def default_network_name(first_device: str) -> str:
    """`<first device name>-net`: what a network is called until someone names it.

    The first device names it once, when it starts the network; every device
    that joins takes the name it is given. The device part is cut so the whole
    name stays within the 63 characters a name may have.
    """
    return f"{first_device[:59].rstrip('-') or 'device'}-net"


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
    """`abcd...ef01`: first 4 and last 4 hex of a fingerprint, for screens."""
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


# --- one row on a human screen -------------------------------------------------
# A sender picks the name and the introduction a review row shows, so every
# screen that lists requests (the Connect screen, the knock review) builds its
# rows here: measured in terminal columns, never in characters.

# The longest a valid device name can be (NAME_RE). A name is cut for being wider
# than its row, never for being long: callers pass the room their row has.
NAME_DISPLAY_MAX = 63
_MIN_QUOTE = 8
_SEP = "   "


def _char_width(char: str) -> int:
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Terminal columns: wide (CJK) characters take two, combining marks none."""
    return sum(_char_width(char) for char in text)


def clip_display(text: str, width: int) -> str:
    """`text` cut to at most `width` columns, an ellipsis marking the cut."""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    kept, used = [], 0
    for char in text:
        step = _char_width(char)
        if used + step > width - 1:
            break
        kept.append(char)
        used += step
    return "".join(kept) + "\u2026"


def display_name(name: str, limit: int = NAME_DISPLAY_MAX) -> str:
    """A sender-chosen name safe for one row: no control characters, no tabs,
    capped at `limit` columns with an ellipsis. The default keeps any valid name
    whole; a row passes the room it has."""
    printable = "".join(
        char
        for char in str(name)
        if char.isprintable() and not unicodedata.category(char).startswith("C")
    )
    return clip_display(printable, limit)


def request_row(
    head: str,
    tail: str,
    width: int,
    *,
    hint: str = "",
    quote: str = "",
    indent: str = "",
    gap: str = _SEP,
) -> list[str]:
    """`head<gap>tail   "quote"   hint` as one line, or two when it is too wide.

    Two lines are `head` over `indent + tail ...`. Nothing exceeds `width`
    columns. The quote is the only part cut (with an ellipsis, and dropped when
    fewer than eight columns are left); the hint is whole or absent, never cut
    mid-token. `head`, `tail` and `hint` arrive already capped; `quote` must be
    printable text on one line.
    """

    def build(lead: str, last_resort: bool) -> tuple[str, bool]:
        parts = [tail, hint] if hint else [tail]
        fixed = display_width(lead) + sum(map(display_width, parts))
        fixed += len(_SEP) * (len(parts) - 1)
        if last_resort and fixed > width and hint:
            parts = [tail]
            fixed = display_width(lead) + display_width(tail)
        room = width - fixed - len(_SEP) - 2
        shown = bool(quote) and room >= _MIN_QUOTE
        if shown:
            parts.insert(1, f'"{clip_display(quote, room)}"')
        return lead + _SEP.join(parts), fixed <= width and (shown or not quote)

    line, fits = build(head + gap, False)
    if fits:
        return [line]
    return [clip_display(head, width), clip_display(build(indent, True)[0], width)]
