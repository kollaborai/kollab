"""Agent DNS: Discovery, Identity & Trust layer for the hub.

Provides DNS-like infrastructure for agent discovery, identity
attestation, reputation tracking, and capability indexing.

Aligned with emerging standards:
- AID (Agent Identity & Discovery) - DNS TXT records, Ed25519 PKA
- ARDP (Agent Registration & Discovery Protocol) - agent:<id>@<authority>
- ANS (Agent Name Service) - structured capability matching
- MIT NANDA - federable agent index
"""

from .models import (
    AgentRecord,
    Attestation,
    CapabilityEntry,
    Endorsement,
    ReputationScore,
)

__all__ = [
    "AgentRecord",
    "Attestation",
    "CapabilityEntry",
    "Endorsement",
    "ReputationScore",
]
