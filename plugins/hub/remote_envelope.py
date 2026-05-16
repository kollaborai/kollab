"""Remote hub message envelope contract.

This module defines the trust boundary for agents talking across machines.
The first slice lands the contract and quarantine behavior; signature
verification can become stricter once the hub standardizes on a key backend.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteEnvelope:
    sender: str
    authority: str
    message_id: str
    timestamp: float
    body_hash: str
    signature: str


@dataclass(frozen=True)
class RemoteVerificationResult:
    accepted: bool
    reason: str
    quarantine: bool = False


class RemoteEnvelopeVerifier:
    """Verify whether a remote hub envelope can enter the local mesh."""

    def __init__(self, *, approved_keys: dict[str, str]):
        self.approved_keys = approved_keys

    def verify(self, envelope: RemoteEnvelope) -> RemoteVerificationResult:
        if not envelope.signature:
            return RemoteVerificationResult(False, "missing signature")
        if envelope.sender not in self.approved_keys:
            return RemoteVerificationResult(False, "unknown remote sender", True)
        return RemoteVerificationResult(True, "remote sender approved")
