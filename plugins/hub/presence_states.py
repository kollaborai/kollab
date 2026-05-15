"""Agent presence state enumeration.

Used by presence.py, coordinator.py, and the hub plugin's
message delivery pipeline to decide whether an agent can be
re-invoked.
"""

from enum import Enum


class PresenceState(str, Enum):
    """Agent presence state."""

    ACTIVE = "active"
    """Agent is working and can be re-invoked by any mechanism."""

    WAITING = "waiting"
    """Agent is externally parked. Only coordinator or force="true" messages
    can wake it during cooldown. After cooldown expires, any peer message can
    wake it."""

    IDLE = "idle"
    """Agent hasn't been re-invoked recently but is NOT explicitly
    waiting. Normal re-invocation rules apply. This is the default
    state for agents that are between turns."""
