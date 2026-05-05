"""Reputation Tracker — trust scoring with exponential decay.

Tracks agent reliability over time:
- Task completion/failure/abandonment
- Session uptime
- Peer endorsements

Composite score: 60% completion + 20% uptime + 20% endorsements.
Exponential decay with configurable half-life (default 24h).
Old reputation fades toward 0.5 (neutral), rewarding consistently
active agents over dormant ones with stale scores.
"""

import logging
import time
from typing import Dict, List, Tuple

from .models import Endorsement, ReputationScore
from .storage import DNSStorage

logger = logging.getLogger(__name__)

# Default decay half-life: 24 hours
DEFAULT_DECAY_HALFLIFE = 86400.0


class ReputationTracker:
    """Track agent trust scores with time decay."""

    def __init__(
        self,
        storage: DNSStorage,
        decay_halflife: float = DEFAULT_DECAY_HALFLIFE,
    ):
        self._storage = storage
        self._decay_halflife = decay_halflife
        self._scores: Dict[str, ReputationScore] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._scores = self._storage.load_reputation()
            self._loaded = True

    def _get_or_create(self, designation: str) -> ReputationScore:
        self._ensure_loaded()
        if designation not in self._scores:
            self._scores[designation] = ReputationScore(designation=designation)
        return self._scores[designation]

    # --- Event Recording ---

    def record_task_completed(
        self, designation: str, response_time_ms: float = 0.0
    ) -> float:
        """Record a successful task completion. Returns new trust score."""
        score = self._get_or_create(designation)
        score.tasks_completed += 1
        if response_time_ms > 0:
            # Running average
            total = score.total_tasks
            if total > 1:
                score.avg_response_time_ms = (
                    score.avg_response_time_ms * (total - 1) + response_time_ms
                ) / total
            else:
                score.avg_response_time_ms = response_time_ms
        score.last_updated = time.time()
        self._save()
        trust = self.get_trust(designation)
        logger.debug(f"task completed by {designation}, trust={trust:.2f}")
        return trust

    def record_task_failed(self, designation: str) -> float:
        """Record a failed task. Returns new trust score."""
        score = self._get_or_create(designation)
        score.tasks_failed += 1
        score.last_updated = time.time()
        self._save()
        trust = self.get_trust(designation)
        logger.debug(f"task failed by {designation}, trust={trust:.2f}")
        return trust

    def record_task_abandoned(self, designation: str) -> float:
        """Record an abandoned task (agent departed mid-task).

        Abandonment weighs more heavily than failure — it indicates
        unreliability, not just inability.
        """
        score = self._get_or_create(designation)
        score.tasks_abandoned += 1
        score.last_updated = time.time()
        self._save()
        trust = self.get_trust(designation)
        logger.debug(f"task abandoned by {designation}, trust={trust:.2f}")
        return trust

    def record_session(self, designation: str, uptime_seconds: float) -> None:
        """Record a session ending (uptime tracking)."""
        score = self._get_or_create(designation)
        score.uptime_sessions += 1
        score.total_uptime_seconds += uptime_seconds
        score.last_updated = time.time()
        self._save()

    def add_endorsement(self, endorsement: Endorsement) -> float:
        """Add a peer endorsement. Returns new trust score for target."""
        score = self._get_or_create(endorsement.to_designation)
        # Avoid duplicate endorsements from same source for same capability
        for existing in score.endorsements:
            if (
                existing.from_designation == endorsement.from_designation
                and existing.capability == endorsement.capability
            ):
                # Update existing endorsement
                existing.endorsed_at = endorsement.endorsed_at
                existing.weight = endorsement.weight
                self._save()
                return self.get_trust(endorsement.to_designation)
        score.endorsements.append(endorsement)
        score.last_updated = time.time()
        self._save()
        trust = self.get_trust(endorsement.to_designation)
        logger.debug(
            f"endorsement from {endorsement.from_designation} "
            f"to {endorsement.to_designation}, trust={trust:.2f}"
        )
        return trust

    # --- Score Retrieval ---

    def get_score(self, designation: str) -> ReputationScore:
        """Get raw reputation score (no decay applied)."""
        return self._get_or_create(designation)

    def get_trust(self, designation: str) -> float:
        """Get composite trust score (0.0-1.0) with time decay applied."""
        score = self._get_or_create(designation)
        return self._compute_trust(score)

    def get_leaderboard(self, limit: int = 10) -> List[Tuple[str, float]]:
        """Get top agents by trust score (with decay)."""
        self._ensure_loaded()
        scored = []
        for designation, score in self._scores.items():
            trust = self._compute_trust(score)
            scored.append((designation, trust))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # --- Bulk Operations ---

    def process_events(self, events: list) -> None:
        """Process pending reputation events (coordinator-only).

        Called on heartbeat to drain reputation_events.jsonl.
        """
        for event in events:
            event_type = event.get("type", "")
            designation = event.get("designation", "")
            if not designation:
                continue
            if event_type == "task_completed":
                self.record_task_completed(
                    designation, event.get("response_time_ms", 0.0)
                )
            elif event_type == "task_failed":
                self.record_task_failed(designation)
            elif event_type == "task_abandoned":
                self.record_task_abandoned(designation)
            elif event_type == "session_end":
                self.record_session(
                    designation, event.get("uptime_seconds", 0.0)
                )
            elif event_type == "endorsement":
                endorsement = Endorsement(
                    from_designation=event.get("from_designation", ""),
                    to_designation=designation,
                    capability=event.get("capability", ""),
                    weight=event.get("weight", 1.0),
                )
                self.add_endorsement(endorsement)

    def reload(self) -> None:
        """Force reload from storage."""
        self._scores = self._storage.load_reputation()
        self._loaded = True

    # --- Internal ---

    def _compute_trust(self, score: ReputationScore) -> float:
        """Compute composite trust score with exponential time decay.

        Components (weighted):
          60% completion rate (tasks completed / total)
          20% uptime factor (log-scaled session count)
          20% endorsement factor (weighted by endorser trust)

        Decay: exponential with half-life, decaying toward 0.5 (neutral).
        A fresh agent with no history starts at 0.5.
        """
        if score.total_tasks == 0 and score.uptime_sessions == 0:
            return 0.5  # neutral for new agents

        # Completion rate (0.0 - 1.0)
        completion = score.completion_rate

        # Uptime factor: log-scaled so first few sessions matter most
        import math

        uptime = min(1.0, math.log1p(score.uptime_sessions) / math.log1p(20))

        # Endorsement factor: weighted average of endorser weights
        endorsement = 0.0
        if score.endorsements:
            total_weight = sum(e.weight for e in score.endorsements)
            # Cap at 1.0 — diminishing returns beyond ~5 endorsements
            endorsement = min(1.0, total_weight / 5.0)

        # Raw composite
        raw = completion * 0.6 + uptime * 0.2 + endorsement * 0.2

        # Apply time decay: decays toward 0.5 (neutral)
        age = time.time() - score.last_updated
        decay_factor = 0.5 ** (age / self._decay_halflife)
        # Blend: fully fresh = raw score, fully decayed = 0.5
        return raw * decay_factor + 0.5 * (1 - decay_factor)

    def _save(self) -> None:
        """Persist scores to storage."""
        self._storage.save_reputation(self._scores)
