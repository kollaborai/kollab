"""Trusted identity and stream lifecycle shared across engine request boundaries."""

from dataclasses import dataclass

SESSION_READ_CONTEXT_SCOPE_KEY = "kollabor_engine.session_read_context"
PHONE_STREAM_REVOKED = object()


@dataclass(eq=False, slots=True)
class SessionReadContext:
    """Server-derived local owner and approved PhoneLink identity for one pairing."""

    owner_id: str
    phone_id: str
    revoked: bool = False
