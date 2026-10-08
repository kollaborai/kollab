"""The knock reply ticket, shared by the relay and the devices.

A knock rings the recipient device for at most RING_SECONDS and the directory
stores nothing about it. The knocker signs a ticket into the knock naming both
keys, the knock id and when the ring ends; the recipient's answer travels back
under that ticket, and its signature is all the directory checks.
"""

from __future__ import annotations

import re
import time
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

RING_SECONDS = 5 * 60
# Device clocks drift; the enrollment frames allow the same two minutes.
SKEW_SECONDS = 120
MAX_CIPHERTEXT_CHARS = 8 * 1024
TICKET_FIELDS = frozenset({"v", "from", "to", "id", "ends", "sig"})

_KEY = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")
_SIG = re.compile(r"[0-9a-f]{128}")


def ticket_message(origin: str, sender: str, recipient: str, knock_id: str, ends: int) -> bytes:
    return "\n".join(
        ("kollab-knock-ticket/1", origin, sender, recipient, knock_id, str(ends))
    ).encode()


def sign_ticket(
    signing_key: SigningKey, origin: str, recipient: str, knock_id: str, ends: int
) -> dict[str, Any]:
    sender = signing_key.verify_key.encode().hex()
    message = ticket_message(origin, sender, recipient, knock_id, ends)
    return {
        "v": 1,
        "from": sender,
        "to": recipient,
        "id": knock_id,
        "ends": ends,
        "sig": signing_key.sign(message).signature.hex(),
    }


def check_ticket(ticket: Any, origin: str, *, now: float | None = None) -> dict[str, Any]:
    """The ticket if it is well formed, signed by its `from` key and still ringing.

    Raises ValueError otherwise. Callers check which keys it binds.
    """
    now = time.time() if now is None else now
    if not isinstance(ticket, dict) or set(ticket) != TICKET_FIELDS:
        raise ValueError("invalid knock ticket")
    sender, recipient, knock_id = ticket["from"], ticket["to"], ticket["id"]
    ends, sig = ticket["ends"], ticket["sig"]
    if (
        type(ticket["v"]) is not int
        or ticket["v"] != 1
        or not isinstance(sender, str)
        or not _KEY.fullmatch(sender)
        or not isinstance(recipient, str)
        or not _KEY.fullmatch(recipient)
        or sender == recipient
        or not isinstance(knock_id, str)
        or not _ID.fullmatch(knock_id)
        or type(ends) is not int
        or not isinstance(sig, str)
        or not _SIG.fullmatch(sig)
    ):
        raise ValueError("invalid knock ticket")
    if not now - SKEW_SECONDS <= ends <= now + RING_SECONDS + SKEW_SECONDS:
        raise ValueError("knock ticket is not ringing")
    try:
        VerifyKey(bytes.fromhex(sender)).verify(
            ticket_message(origin, sender, recipient, knock_id, ends),
            bytes.fromhex(sig),
        )
    except (BadSignatureError, ValueError) as exc:
        raise ValueError("invalid knock ticket signature") from exc
    return ticket
