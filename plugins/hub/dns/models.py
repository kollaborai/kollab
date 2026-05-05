"""Data models for the Agent DNS system.

Follows existing hub patterns: @dataclass with to_dict() / from_dict().
Designed for standards alignment:
- AID: to_aid_txt() exports DNS TXT record format
- ARDP: agent:<designation>@<authority> identity format
- ANS: structured capability entries with evidence
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# CapabilityEntry — structured capability with evidence tracking
# ---------------------------------------------------------------------------


@dataclass
class CapabilityEntry:
    """A structured capability advertisement.

    Beyond flat string lists: tracks evidence level, confidence,
    version, and endorsements. Compatible with ANS capability
    descriptors and ARDP capability advertisement.
    """

    name: str  # "code", "test", "review", "deploy", "security"
    version: str = "1.0"
    evidence: str = "self-declared"  # "self-declared" | "task-proven" | "endorsed"
    confidence: float = 0.5  # 0.0-1.0
    last_demonstrated: float = 0.0
    endorsed_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "last_demonstrated": self.last_demonstrated,
            "endorsed_by": self.endorsed_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Endorsement — peer vouching
# ---------------------------------------------------------------------------


@dataclass
class Endorsement:
    """An agent vouching for another agent's capability or general trust.

    Endorsements carry the endorser's trust score at time of endorsement
    as weight, so endorsements from high-trust agents matter more.
    """

    from_designation: str
    to_designation: str
    capability: str = ""  # specific capability, or "" for general endorsement
    signature: str = ""  # Ed25519 signature (hex), optional
    endorsed_at: float = field(default_factory=time.time)
    weight: float = 1.0  # endorser's trust score at time of endorsement

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_designation": self.from_designation,
            "to_designation": self.to_designation,
            "capability": self.capability,
            "signature": self.signature,
            "endorsed_at": self.endorsed_at,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Endorsement":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Attestation — signed identity proof
# ---------------------------------------------------------------------------


@dataclass
class Attestation:
    """A signed statement proving identity ownership.

    Compatible with AID PKA handshake (Ed25519 signatures) and
    ARDP proof-of-control (JWS-signed registration body).

    The coordinator signs attestations for designation assignments,
    binding a public key to a designation. Self-attestation is used
    for the coordinator's own identity (bootstrap).
    """

    subject: str  # designation being attested
    issuer: str  # who signed: coordinator designation, or "self"
    public_key: str  # subject's Ed25519 public key (hex)
    signature: str = ""  # Ed25519 signature of (subject + public_key + issued_at) (hex)
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0  # 0 = no expiry
    attestation_type: str = "registration"  # "registration" | "endorsement" | "revocation"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "public_key": self.public_key,
            "signature": self.signature,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "attestation_type": self.attestation_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attestation":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def is_expired(self) -> bool:
        if self.expires_at == 0.0:
            return False
        return time.time() > self.expires_at


# ---------------------------------------------------------------------------
# ReputationScore — trust tracking with decay
# ---------------------------------------------------------------------------


@dataclass
class ReputationScore:
    """Tracks agent reliability over time.

    Composite score: 60% completion rate + 20% uptime + 20% endorsements.
    Exponential decay with 24h half-life: old reputation fades toward
    0.5 (neutral). This prevents stale reputation from persisting while
    rewarding consistently active agents.
    """

    designation: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_abandoned: int = 0
    avg_response_time_ms: float = 0.0
    uptime_sessions: int = 0
    total_uptime_seconds: float = 0.0
    endorsements: List[Endorsement] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)

    @property
    def completion_rate(self) -> float:
        total = self.tasks_completed + self.tasks_failed + self.tasks_abandoned
        return self.tasks_completed / total if total > 0 else 0.5

    @property
    def total_tasks(self) -> int:
        return self.tasks_completed + self.tasks_failed + self.tasks_abandoned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "designation": self.designation,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "tasks_abandoned": self.tasks_abandoned,
            "avg_response_time_ms": self.avg_response_time_ms,
            "uptime_sessions": self.uptime_sessions,
            "total_uptime_seconds": self.total_uptime_seconds,
            "endorsements": [e.to_dict() for e in self.endorsements],
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReputationScore":
        endorsements = [
            Endorsement.from_dict(e) for e in data.pop("endorsements", [])
        ]
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered, endorsements=endorsements)


# ---------------------------------------------------------------------------
# AgentRecord — the "DNS record"
# ---------------------------------------------------------------------------

# Default authority for ARDP-style agent IDs
# Overridable via config: plugins.hub.authority
DEFAULT_AUTHORITY = "kollabor.ai"

# Supported runtimes
RUNTIMES = ("kollab", "claude", "codex", "gemini", "opencode")

# Supported protocols
PROTOCOLS = ("mcp", "a2a", "grpc", "socket", "websocket")


@dataclass
class AgentRecord:
    """The DNS record for an agent.

    Combines A-record (address), SRV-record (capabilities), and
    TXT-record (attestation) semantics. Designed for export to
    AID DNS TXT format and ARDP registration payloads.

    Identity format follows ARDP: agent:<designation>@<authority>
    e.g. agent:peridot@kollabor
    """

    # Identity
    designation: str  # gem name: "peridot", "lapis-2"
    agent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    runtime: str = "kollab"
    authority: str = DEFAULT_AUTHORITY  # domain for ARDP identity

    # Address resolution (A-record)
    socket_path: str = ""  # unix socket for local
    endpoint_uri: str = ""  # HTTPS for remote
    pid: int = 0
    project: str = ""

    # Capabilities (SRV-record)
    capabilities: List[CapabilityEntry] = field(default_factory=list)
    protocols: List[str] = field(default_factory=lambda: ["socket"])

    # Attestation (TXT-record)
    public_key: str = ""  # Ed25519 public key (hex)
    attestation: Optional[Attestation] = None

    # Approval (coordinator gatekeeper)
    approval_state: str = "pending"  # "pending" | "approved" | "rejected" | "auto_approved"

    # Trust
    trust_score: float = 0.5  # 0.0-1.0, starts neutral

    # Metadata
    state: str = "idle"
    current_task: str = ""
    is_coordinator: bool = False
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    ttl: float = 30.0  # seconds before record considered stale
    caste: str = ""  # gem caste: communication, engineering, etc.
    tags: Dict[str, str] = field(default_factory=dict)

    # --- Derived properties ---

    @property
    def aid(self) -> str:
        """ARDP-format agent identity: agent:<designation>@<authority>."""
        return f"agent:{self.designation}@{self.authority}"

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.last_seen) > self.ttl

    @property
    def is_approved(self) -> bool:
        """True if agent is approved or auto-approved for mesh participation."""
        return self.approval_state in ("approved", "auto_approved")

    @property
    def capability_names(self) -> List[str]:
        return [c.name for c in self.capabilities]

    @property
    def avg_capability_confidence(self) -> float:
        if not self.capabilities:
            return 0.0
        return sum(c.confidence for c in self.capabilities) / len(self.capabilities)

    # --- Serialization ---

    def to_dict(self) -> Dict[str, Any]:
        return {
            "designation": self.designation,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            "authority": self.authority,
            "aid": self.aid,
            "socket_path": self.socket_path,
            "endpoint_uri": self.endpoint_uri,
            "pid": self.pid,
            "project": self.project,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "protocols": self.protocols,
            "public_key": self.public_key,
            "attestation": self.attestation.to_dict() if self.attestation else None,
            "approval_state": self.approval_state,
            "trust_score": self.trust_score,
            "state": self.state,
            "current_task": self.current_task,
            "is_coordinator": self.is_coordinator,
            "registered_at": self.registered_at,
            "last_seen": self.last_seen,
            "ttl": self.ttl,
            "caste": self.caste,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentRecord":
        caps = [CapabilityEntry.from_dict(c) for c in data.pop("capabilities", [])]
        att_data = data.pop("attestation", None)
        att = Attestation.from_dict(att_data) if att_data else None
        data.pop("aid", None)  # derived, not a constructor arg
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered, capabilities=caps, attestation=att)

    # --- Standards export ---

    def to_aid_txt(self, proto: str = "mcp") -> str:
        """Export as AID-format DNS TXT record.

        Format: v=aid1;u=<endpoint>;p=<proto>;k=<pubkey>;s=<desc>
        For publishing at _agent.<domain> DNS TXT record.
        """
        parts = ["v=aid1"]
        uri = self.endpoint_uri or f"unix://{self.socket_path}"
        parts.append(f"u={uri}")
        parts.append(f"p={proto}")
        if self.public_key:
            parts.append(f"k={self.public_key}")
        desc = f"{self.designation} ({self.runtime})"
        if self.caste:
            desc += f" [{self.caste}]"
        parts.append(f"s={desc[:60]}")
        return ";".join(parts)

    def to_ardp_json(self) -> Dict[str, Any]:
        """Export as ARDP registration payload.

        Compatible with IETF draft-pioli-agent-discovery.
        """
        bindings = []
        if self.socket_path:
            bindings.append({
                "binding_id": f"{self.designation}-socket",
                "transport": "unix-socket",
                "uri": f"unix://{self.socket_path}",
                "protocols": ["socket"],
            })
        if self.endpoint_uri:
            bindings.append({
                "binding_id": f"{self.designation}-https",
                "transport": "https",
                "uri": self.endpoint_uri,
                "protocols": [p for p in self.protocols if p != "socket"],
            })
        return {
            "aid": self.aid,
            "bindings": bindings,
            "capabilities": [
                {
                    "name": c.name,
                    "version": c.version,
                    "confidence": c.confidence,
                }
                for c in self.capabilities
            ],
            "metadata": {
                "runtime": self.runtime,
                "caste": self.caste,
                "trust_score": self.trust_score,
                "state": self.state,
            },
            "ttl": int(self.ttl),
        }
