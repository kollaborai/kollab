"""Nudge engine - context-aware tool reminders for hub agents.

Tracks agent behavior and injects short reminders when they should
be using a tool but aren't. Each tool has an activation scenario
that fires based on observed behavior, not random chance.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Cooldown between nudges of the same type (seconds)
DEFAULT_COOLDOWN = 300  # 5 minutes

# How many turns without scratchpad before nudging
SCRATCHPAD_IDLE_THRESHOLD = 5

# How many turns without checkpoint before nudging (for task holders)
CHECKPOINT_IDLE_THRESHOLD = 8

# How many consecutive hub-only turns before loop_detected nudge fires
HUB_LOOP_THRESHOLD = 3

# Cooldown between loop_detected nudges (longer than other nudes)
LOOP_NUDGE_COOLDOWN = 600  # 10 minutes


@dataclass
class AgentTracker:
    """Per-agent behavior tracking."""

    identity: str = ""
    turns: int = 0
    turns_since_scratchpad: int = 0
    turns_since_checkpoint: int = 0
    unclaimed_files: List[str] = field(default_factory=list)
    has_file_watches: bool = False
    has_active_task: bool = False
    turns_hub_only: int = 0  # consecutive turns with hub_msg but no real work
    last_nudge_at: Dict[str, float] = field(default_factory=dict)
    last_nudge_type: str = ""


class NudgeEngine:
    """Evaluates agent behavior and generates contextual tool nudges.

    Each nudge is a short 1-2 line reminder with exact tag syntax.
    Only fires when the agent should have used a tool but didn't.
    Respects cooldowns so we don't nag.
    """

    def __init__(self, cooldown: int = DEFAULT_COOLDOWN, loop_threshold: int = HUB_LOOP_THRESHOLD):
        self._trackers: Dict[str, AgentTracker] = {}
        self._cooldown = cooldown
        self._loop_threshold = loop_threshold

    def _get_tracker(self, identity: str) -> AgentTracker:
        if not identity:
            identity = "_unknown"
        if identity not in self._trackers:
            self._trackers[identity] = AgentTracker(identity=identity)
        return self._trackers[identity]

    def _can_nudge(self, tracker: AgentTracker, nudge_type: str) -> bool:
        """Check cooldown for a nudge type."""
        now = time.time()
        last = tracker.last_nudge_at.get(nudge_type, 0)
        # Loop detection has its own longer cooldown
        cooldown = (
            LOOP_NUDGE_COOLDOWN if nudge_type == "loop_detected"
            else self._cooldown
        )
        if now - last < cooldown:
            return False
        # Don't repeat same nudge type back to back
        if tracker.last_nudge_type == nudge_type and tracker.turns < 2:
            return False
        return True

    def _record_nudge(self, tracker: AgentTracker, nudge_type: str) -> None:
        tracker.last_nudge_at[nudge_type] = time.time()
        tracker.last_nudge_type = nudge_type

    def observe_response(
        self,
        identity: str,
        response: str,
        used_scratchpad: bool = False,
        used_state_update: bool = False,
        used_checkpoint: bool = False,
        used_hub_msg: bool = False,
        used_real_tools: bool = False,
        edited_files: Optional[List[str]] = None,
        claimed_files: Optional[List[str]] = None,
    ) -> None:
        """Record what the agent did in this response.

        Called by plugin after parsing tags.
        """
        tracker = self._get_tracker(identity)
        tracker.turns += 1

        # Hub loop detection: track consecutive hub-only turns
        if used_hub_msg and not used_real_tools:
            tracker.turns_hub_only += 1
        else:
            tracker.turns_hub_only = 0

        # Scratchpad usage
        if used_scratchpad:
            tracker.turns_since_scratchpad = 0
        else:
            tracker.turns_since_scratchpad += 1

        # Checkpoint usage
        if used_checkpoint:
            tracker.turns_since_checkpoint = 0
        else:
            tracker.turns_since_checkpoint += 1

        # Track unclaimed files
        if edited_files:
            for f in edited_files:
                if f not in tracker.unclaimed_files:
                    tracker.unclaimed_files.append(f)

        if claimed_files:
            for f in claimed_files:
                if f in tracker.unclaimed_files:
                    tracker.unclaimed_files.remove(f)

        # Detect promises in natural language
        # ("i'll do X", "sending to Y", "let me check Z")
        promise_patterns = [
            "i'll ",
            "i will ",
            "let me ",
            "sending ",
            "going to ",
            "i can ",
            "on it",
        ]
        response_lower = response.lower()
        for pattern in promise_patterns:
            if pattern in response_lower and not used_state_update:
                # Agent made a promise but didn't track it
                pass  # Handled in evaluate()

    def observe_file_watches(self, identity: str, has_watches: bool) -> None:
        """Update whether agent has file watches active."""
        tracker = self._get_tracker(identity)
        if tracker:
            tracker.has_file_watches = has_watches

    def observe_task_assignment(self, identity: str, has_task: bool) -> None:
        """Update whether agent has an active task."""
        tracker = self._get_tracker(identity)
        tracker.has_active_task = has_task

    def evaluate(
        self,
        identity: str,
        peers_online: int = 0,
    ) -> Optional[str]:
        """Check if agent should be nudged. Returns nudge text or None.

        Priority order:
        1. Unclaimed file edits (immediate conflict risk)
        2. Task without checkpoints (high priority work tracking)
        3. No scratchpad usage after N turns
        4. No file watches when peers are active
        """
        tracker = self._get_tracker(identity)

        # 0. Hub loop detection
        if tracker.turns_hub_only >= self._loop_threshold:
            if self._can_nudge(tracker, "loop_detected"):
                self._record_nudge(tracker, "loop_detected")
                return (
                    "[system: hub loop detected]\n"
                    "you have spent " + str(tracker.turns_hub_only) + " turns "
                    "in a row exchanging hub messages without doing any work "
                    "(file reads, edits, terminal commands, scratchpad writes).\n\n"
                    "if your task is finished, end your next turn with:\n"
                    "  <wait_for_user/>\n\n"
                    "this will put you in waiting state. peer agents "
                    "that try to message you will get an error telling "
                    "them you are in cooldown. the coordinator can "
                    "still break through. cooldown is 60s.\n\n"
                    "if you are still working, make a tool call next "
                    "turn (a file read, terminal command, or scratchpad "
                    "write) to confirm and reset the loop counter.\n\n"
                    "this nudge will not fire again for 10 minutes."
                )

        # 1. Unclaimed file edits
        if tracker.unclaimed_files and self._can_nudge(tracker, "lane_claim"):
            target = tracker.unclaimed_files[0]
            self._record_nudge(tracker, "lane_claim")
            return (
                f"[nudge] you edited {target} but didn't claim it. "
                f"claim it with lane_claim to prevent conflicts."
            )

        # 2. Task without checkpoints (higher priority than scratchpad)
        if (
            tracker.has_active_task
            and tracker.turns_since_checkpoint >= CHECKPOINT_IDLE_THRESHOLD
        ):
            if self._can_nudge(tracker, "checkpoint"):
                self._record_nudge(tracker, "checkpoint")
                return (
                    "[nudge] you have an active task but no recent checkpoint. "
                    'use <task_checkpoint id="TASK_ID">progress note</task_checkpoint> to save your progress.'
                )

        # 3. Scratchpad neglect
        if tracker.turns_since_scratchpad >= SCRATCHPAD_IDLE_THRESHOLD:
            if self._can_nudge(tracker, "scratchpad"):
                self._record_nudge(tracker, "scratchpad")
                return (
                    "[nudge] you've been working a while without saving notes. "
                    "use scratchpad_append to save key findings before you compact."
                )

        # 4. No file watches when peers are editing
        if peers_online > 0 and not tracker.has_file_watches and tracker.turns >= 3:
            if self._can_nudge(tracker, "file_watch"):
                self._record_nudge(tracker, "file_watch")
                return (
                    f"[nudge] {peers_online} other agent(s) are working. "
                    "use file_watch to get notified when files change."
                )

        return None

    def reset_loop_counter(self, identity: str) -> None:
        """Reset the loop counter for an agent.

        Called by the hub plugin when an agent emits <wait_for_user/>
        or wakes from waiting state.
        """
        tracker = self._get_tracker(identity)
        tracker.turns_hub_only = 0
