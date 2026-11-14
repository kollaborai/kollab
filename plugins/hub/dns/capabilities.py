"""Capability Registry — structured capability indexing.

Goes beyond flat string lists: tracks evidence level, confidence,
version, and endorsements for each capability. Maintains a reverse
index for fast capability-to-agent queries.

Compatible with ANS semantic capability matching and ARDP
capability advertisement.
"""

import logging
import time
from typing import TYPE_CHECKING, Dict, List, Tuple

if TYPE_CHECKING:
    from .models import AgentRecord

from .models import CapabilityEntry
from .storage import DNSStorage

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """Structured capability advertisement and querying."""

    def __init__(self, storage: DNSStorage):
        self._storage = storage
        # Reverse index: capability_name -> [(designation, CapabilityEntry)]
        self._index: Dict[str, List[Tuple[str, CapabilityEntry]]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            raw = self._storage.load_capability_index()
            self._index = {}
            for cap_name, entries in raw.items():
                self._index[cap_name] = [
                    (e["designation"], CapabilityEntry.from_dict(e["capability"]))
                    for e in entries
                ]
            self._loaded = True

    # --- Declaration ---

    def declare(self, designation: str, capability: CapabilityEntry) -> None:
        """Declare a capability for an agent.

        If the capability already exists for this designation, updates it.
        """
        self._ensure_loaded()
        cap_name = capability.name

        if cap_name not in self._index:
            self._index[cap_name] = []

        # Update or append
        for i, (d, _) in enumerate(self._index[cap_name]):
            if d == designation:
                self._index[cap_name][i] = (designation, capability)
                self._save(updated_entries={(cap_name, designation)})
                return

        self._index[cap_name].append((designation, capability))
        self._save(updated_entries={(cap_name, designation)})
        logger.debug(f"{designation} declared capability: {cap_name} v{capability.version}")

    def declare_many(self, designation: str, capabilities: List[CapabilityEntry]) -> None:
        """Declare multiple capabilities at once (batched save)."""
        self._ensure_loaded()
        updated_entries = set()
        for cap in capabilities:
            cap_name = cap.name
            if cap_name not in self._index:
                self._index[cap_name] = []

            updated = False
            for i, (d, _) in enumerate(self._index[cap_name]):
                if d == designation:
                    self._index[cap_name][i] = (designation, cap)
                    updated = True
                    break
            if not updated:
                self._index[cap_name].append((designation, cap))
            updated_entries.add((cap_name, designation))

        self._save(updated_entries=updated_entries)

    def revoke(self, designation: str, capability_name: str) -> None:
        """Revoke a capability declaration."""
        self._ensure_loaded()
        if capability_name in self._index:
            self._index[capability_name] = [
                (d, c) for d, c in self._index[capability_name] if d != designation
            ]
            if not self._index[capability_name]:
                del self._index[capability_name]
            self._save(deleted_entries={(capability_name, designation)})

    def revoke_all(self, designation: str) -> None:
        """Revoke all capabilities for a designation (on deregister)."""
        self._ensure_loaded()
        empty_caps = []
        for cap_name in self._index:
            self._index[cap_name] = [
                (d, c) for d, c in self._index[cap_name] if d != designation
            ]
            if not self._index[cap_name]:
                empty_caps.append(cap_name)
        for cap_name in empty_caps:
            del self._index[cap_name]
        self._save(deleted_designations={designation})

    # --- Queries ---

    def query(
        self, capability_name: str, min_confidence: float = 0.0
    ) -> List[Tuple[str, CapabilityEntry]]:
        """Find agents with a specific capability.

        Returns (designation, CapabilityEntry) pairs, sorted by
        confidence descending.
        """
        self._ensure_loaded()
        entries = self._index.get(capability_name, [])
        if min_confidence > 0:
            entries = [(d, c) for d, c in entries if c.confidence >= min_confidence]
        entries.sort(key=lambda x: x[1].confidence, reverse=True)
        return entries

    def get_capabilities(self, designation: str) -> List[CapabilityEntry]:
        """Get all capabilities for a designation."""
        self._ensure_loaded()
        caps = []
        for entries in self._index.values():
            for d, c in entries:
                if d == designation:
                    caps.append(c)
        return caps

    def list_all_capabilities(self) -> List[str]:
        """List all known capability names."""
        self._ensure_loaded()
        return sorted(self._index.keys())

    # --- Evidence Promotion ---

    def promote(
        self,
        designation: str,
        capability_name: str,
        evidence: str = "task-proven",
    ) -> None:
        """Promote a capability's evidence level.

        Evidence levels (ascending): self-declared -> task-proven -> endorsed
        Confidence is auto-adjusted based on evidence level.
        """
        self._ensure_loaded()
        evidence_confidence = {
            "self-declared": 0.5,
            "task-proven": 0.8,
            "endorsed": 0.9,
        }
        entries = self._index.get(capability_name, [])
        for i, (d, c) in enumerate(entries):
            if d == designation:
                c.evidence = evidence
                c.confidence = max(c.confidence, evidence_confidence.get(evidence, 0.5))
                c.last_demonstrated = time.time()
                entries[i] = (d, c)
                self._save(updated_entries={(capability_name, designation)})
                logger.debug(
                    f"promoted {designation}/{capability_name} to {evidence} "
                    f"(confidence={c.confidence:.2f})"
                )
                return

    def endorse_capability(
        self, endorser: str, target: str, capability_name: str
    ) -> None:
        """Endorse another agent's capability.

        Adds endorser to the capability's endorsed_by list and
        promotes evidence to 'endorsed' if not already.
        """
        self._ensure_loaded()
        entries = self._index.get(capability_name, [])
        for i, (d, c) in enumerate(entries):
            if d == target:
                if endorser not in c.endorsed_by:
                    c.endorsed_by.append(endorser)
                if c.evidence != "endorsed":
                    c.evidence = "endorsed"
                    c.confidence = max(c.confidence, 0.9)
                entries[i] = (d, c)
                self._save(updated_entries={(capability_name, target)})
                return

    # --- Rebuild ---

    def rebuild_from_records(
        self, records: Dict[str, "AgentRecord"]
    ) -> None:
        """Rebuild the capability index from registry records.

        Called on startup to ensure index consistency.
        """
        self._index = {}
        for designation, record in records.items():
            for cap in record.capabilities:
                if cap.name not in self._index:
                    self._index[cap.name] = []
                self._index[cap.name].append((designation, cap))
        self._save(replace=True)
        self._loaded = True
        logger.debug(f"rebuilt capability index: {len(self._index)} capabilities")

    def reload(self) -> None:
        """Force reload from storage."""
        self._loaded = False
        self._ensure_loaded()

    # --- Internal ---

    def _save(
        self,
        *,
        updated_entries=(),
        deleted_entries=(),
        deleted_designations=(),
        replace: bool = False,
    ) -> None:
        """Persist only this registry operation's changes to storage."""
        raw = {}
        if replace:
            updated_entries = {
                (cap_name, designation)
                for cap_name, entries in self._index.items()
                for designation, _ in entries
            }
        for cap_name, designation in updated_entries:
            capability = next(
                (
                    capability
                    for entry_designation, capability in self._index.get(
                        cap_name, []
                    )
                    if entry_designation == designation
                ),
                None,
            )
            if capability is not None:
                raw.setdefault(cap_name, []).append(
                    {
                        "designation": designation,
                        "capability": capability.to_dict(),
                    }
                )
        self._storage.save_capability_index(
            raw,
            deleted_entries=deleted_entries,
            deleted_designations=deleted_designations,
            replace=replace,
        )
