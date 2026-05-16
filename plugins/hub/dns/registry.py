"""Agent Registry — DNS-like name resolution and capability queries.

The central registry for agent discovery. Provides:
- Name resolution (A-record): designation -> address
- Capability queries (SRV-record): capability -> agents
- Bulk queries: by runtime, by caste
- Liveness maintenance: cross-reference with presence data

Managed by the coordinator (primary writer), readable by all agents
(filesystem is the IPC channel, same as presence).
"""

import logging
import time
from typing import Dict, Iterable, List, Optional

from kollabor_agent.runtime import AgentRuntime

from .models import AgentRecord
from .storage import DNSStorage

logger = logging.getLogger(__name__)


class AgentRegistry:
    """DNS-like agent registry with name resolution and capability queries."""

    def __init__(self, storage: DNSStorage):
        self._storage = storage
        self._records: Dict[str, AgentRecord] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load records from storage on first access."""
        if not self._loaded:
            self._records = self._storage.load_registry()
            self._loaded = True

    # --- Registration ---

    def register(self, record: AgentRecord) -> AgentRecord:
        """Register or update an agent record.

        If the designation already exists, updates the existing record's
        dynamic fields (address, state, last_seen) while preserving
        accumulated data (trust_score, attestation).
        """
        self._ensure_loaded()
        existing = self._records.get(record.designation)
        if existing:
            # Update dynamic fields, preserve accumulated data
            existing.agent_id = record.agent_id
            existing.socket_path = record.socket_path
            existing.endpoint_uri = record.endpoint_uri
            existing.pid = record.pid
            existing.project = record.project
            existing.state = record.state
            existing.current_task = record.current_task
            existing.is_coordinator = record.is_coordinator
            existing.last_seen = time.time()
            existing.runtime = record.runtime
            existing.protocols = record.protocols
            # Update capabilities if provided (don't clear existing)
            if record.capabilities:
                existing.capabilities = record.capabilities
            # Update public key if provided
            if record.public_key:
                existing.public_key = record.public_key
            # Update attestation if provided
            if record.attestation:
                existing.attestation = record.attestation
            self._save()
            logger.debug(f"updated DNS record for {record.designation}")
            return existing
        else:
            record.registered_at = time.time()
            record.last_seen = time.time()
            self._records[record.designation] = record
            self._save()
            logger.info(f"registered DNS record for {record.designation}")
            return record

    def deregister(self, designation: str) -> bool:
        """Remove an agent record (graceful departure)."""
        self._ensure_loaded()
        if designation in self._records:
            del self._records[designation]
            self._save()
            logger.info(f"deregistered DNS record for {designation}")
            return True
        return False

    # --- Name Resolution (A-record) ---

    def resolve(self, designation: str) -> Optional[AgentRecord]:
        """Resolve a designation to its full record."""
        self._ensure_loaded()
        return self._records.get(designation)

    def resolve_address(self, designation: str) -> Optional[str]:
        """Resolve designation to socket_path (fast path)."""
        record = self.resolve(designation)
        if record:
            return record.endpoint_uri or record.socket_path
        return None

    # --- Capability Queries (SRV-record) ---

    def query_capability(
        self, capability: str, min_trust: float = 0.0
    ) -> List[AgentRecord]:
        """Find all agents with a specific capability.

        Optionally filter by minimum trust score.
        Results sorted by trust_score descending.
        """
        self._ensure_loaded()
        results = []
        for record in self._records.values():
            if record.trust_score < min_trust:
                continue
            if any(c.name == capability for c in record.capabilities):
                results.append(record)
        results.sort(key=lambda r: r.trust_score, reverse=True)
        return results

    def find_best_for(
        self,
        capabilities: List[str],
        exclude: Optional[List[str]] = None,
    ) -> Optional[AgentRecord]:
        """Find the best agent for a set of required capabilities.

        Scoring: capability_overlap * 0.5 + trust_score * 0.3 +
                 avg_capability_confidence * 0.2

        Improves on WorkQueue.find_best_agent by incorporating trust
        and capability confidence.
        """
        self._ensure_loaded()
        if not capabilities:
            return None

        exclude = exclude or []
        best_record = None
        best_score = -1.0

        for record in self._records.values():
            if record.designation in exclude:
                continue
            if record.state in ("dead", "blocked"):
                continue

            # Calculate capability overlap
            record_caps = set(record.capability_names)
            required = set(capabilities)
            overlap = len(record_caps & required) / len(required)

            if overlap == 0:
                continue

            # Calculate confidence for matched capabilities
            matched_confidence = []
            for cap in record.capabilities:
                if cap.name in required:
                    matched_confidence.append(cap.confidence)
            avg_conf = (
                sum(matched_confidence) / len(matched_confidence)
                if matched_confidence
                else 0.0
            )

            score = overlap * 0.5 + record.trust_score * 0.3 + avg_conf * 0.2

            if score > best_score:
                best_score = score
                best_record = record

        return best_record

    # --- Bulk Queries ---

    def get_all(self, runtime: Optional[str] = None) -> List[AgentRecord]:
        """Get all records, optionally filtered by runtime."""
        self._ensure_loaded()
        records = list(self._records.values())
        if runtime:
            records = [r for r in records if r.runtime == runtime]
        return records

    def get_by_caste(self, caste: str) -> List[AgentRecord]:
        """Get agents by gem caste (communication, engineering, etc.)."""
        self._ensure_loaded()
        return [r for r in self._records.values() if r.caste == caste]

    def get_online(self) -> List[AgentRecord]:
        """Get all non-stale records."""
        self._ensure_loaded()
        return [r for r in self._records.values() if not r.is_stale]

    # --- State Updates ---

    def update_state(
        self, designation: str, state: str, current_task: str = ""
    ) -> bool:
        """Update an agent's state and task."""
        self._ensure_loaded()
        record = self._records.get(designation)
        if record:
            record.state = state
            record.current_task = current_task
            record.last_seen = time.time()
            self._save()
            return True
        return False

    def update_trust(self, designation: str, trust_score: float) -> bool:
        """Update an agent's trust score."""
        self._ensure_loaded()
        record = self._records.get(designation)
        if record:
            record.trust_score = max(0.0, min(1.0, trust_score))
            self._save()
            return True
        return False

    # --- Maintenance ---

    def refresh_liveness(
        self,
        live_agents: List[AgentRuntime],
        preserve_designations: Optional[Iterable[str]] = None,
    ) -> int:
        """Cross-reference registry with live presence data.

        Updates last_seen for live agents, removes stale records.
        Always persists changes (called on heartbeat interval, not hot path).
        Returns count of records removed.
        """
        self._ensure_loaded()
        preserved = {d for d in (preserve_designations or []) if d}
        live_ids = {a.agent_id for a in live_agents}
        live_designations = set()
        for a in live_agents:
            ident = getattr(a, "identity", None) or getattr(a, "designation", "")
            if ident:
                live_designations.add(ident)

        removed = 0
        stale = []
        for designation, record in self._records.items():
            if (
                record.agent_id in live_ids
                or designation in live_designations
                or designation in preserved
            ):
                record.last_seen = time.time()
            elif record.is_stale:
                stale.append(designation)

        for designation in stale:
            del self._records[designation]
            removed += 1

        # Always persist — last_seen updates and removals
        self._save()
        if removed > 0:
            logger.info(f"removed {removed} stale DNS records")

        return removed

    def save(self) -> None:
        """Persist current records to storage (public API)."""
        self._save()

    def reload(self) -> None:
        """Force reload from storage (useful after external changes)."""
        self._records = self._storage.load_registry()
        self._loaded = True

    # --- Roster Display ---

    def format_roster(self, include_trust: bool = True) -> List[str]:
        """Format registry as human-readable roster lines.

        Used by /hub dns resolve and roster injection.
        """
        self._ensure_loaded()
        lines = []
        for record in sorted(self._records.values(), key=lambda r: r.trust_score, reverse=True):
            parts = [record.designation]
            if include_trust:
                parts.append(f"[{record.trust_score:.2f}]")
            if record.caste:
                parts.append(f"({record.caste})")
            parts.append(record.runtime)
            if record.state != "idle":
                parts.append(f"- {record.state}")
                if record.current_task:
                    parts.append(f": {record.current_task}")
            if record.is_coordinator:
                parts.append("*")
            lines.append(" ".join(parts))
        return lines

    # --- Approval (Coordinator Gatekeeper) ---

    def approve(self, designation: str, approver: str = "coordinator") -> bool:
        """Approve an agent for full mesh participation.

        Called by coordinator for manual approval or auto-approval
        of whitelisted runtimes.
        """
        self._ensure_loaded()
        record = self._records.get(designation)
        if not record:
            return False
        record.approval_state = "approved"
        record.last_seen = time.time()
        self._save()
        logger.info(f"approved agent {designation} (by {approver})")
        return True

    def reject(self, designation: str, reason: str = "") -> bool:
        """Reject an agent — prevents mesh participation.

        Rejected agents can still receive messages but cannot
        send to others or participate in work routing.
        """
        self._ensure_loaded()
        record = self._records.get(designation)
        if not record:
            return False
        record.approval_state = "rejected"
        record.last_seen = time.time()
        self._save()
        logger.info(f"rejected agent {designation}: {reason}")
        return True

    def is_approved(self, designation: str) -> bool:
        """Check if an agent is approved for mesh participation."""
        self._ensure_loaded()
        record = self._records.get(designation)
        if not record:
            return False
        return record.is_approved

    def get_pending(self) -> List[AgentRecord]:
        """Get all agents pending approval."""
        self._ensure_loaded()
        return [
            r for r in self._records.values()
            if r.approval_state == "pending"
        ]

    def auto_approve_runtime(
        self, designation: str, allowed_runtimes: List[str]
    ) -> bool:
        """Auto-approve if the agent's runtime is in the whitelist.

        Called during registration. Returns True if auto-approved.
        """
        self._ensure_loaded()
        record = self._records.get(designation)
        if not record:
            return False
        if record.runtime in allowed_runtimes:
            record.approval_state = "auto_approved"
            record.last_seen = time.time()
            self._save()
            logger.info(
                f"auto-approved {designation} (runtime={record.runtime})"
            )
            return True
        return False

    # --- Internal ---

    def _save(self) -> None:
        """Persist records to storage."""
        self._storage.save_registry(self._records)
