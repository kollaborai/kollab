"""Context service data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Literal, Optional, Tuple


@dataclass
class LedgerEntry:
    """A single heavy item tracked by ContextService.

    Heavy items are tool results and file reads over a configurable
    size threshold (default 8KB). Each entry has a stable ctx_id
    that the agent can reference in <curate>, <evict>, and
    <context/> tags.
    """

    ctx_id: str
    """Stable identifier, e.g. 'ctx-1', 'ctx-2'. Sequential
    per session. Assigned when the entry is created."""

    kind: Literal["file_read", "tool_result", "attachment"]
    """Category of the heavy item."""

    tool: str
    """The tool that produced this entry. For file_read entries,
    this is 'read' or 'diff'. For tool results, it's the tool
    name (e.g. 'terminal', 'grep', 'mcp:github')."""

    label: str
    """Human-readable label for the entry. For files, the file path.
    For tool results, a short description."""

    # Content identity
    content_hash: str
    """Hash of the raw content bytes. Used for dedup."""

    size_bytes: int
    """Size of the raw content in bytes."""

    # History placement
    message_uuid: str
    """The UUID of the ConversationMessage containing this heavy
    item. Stable across history edits and compaction."""

    # Lifecycle
    added_at: datetime
    """When the entry was created."""

    last_accessed_at: datetime
    """Updated when a stale-read hit references this entry."""

    read_count: int = 1
    """Number of times the agent has referenced this entry.
    Starts at 1 for the original read."""

    ttl_seconds: Optional[int] = None
    """Optional time-to-live in seconds from last_accessed_at.
    None = no expiry."""

    # Curation
    decision: Literal["pending", "keep", "summary", "evicted"] = "pending"
    """Agent's decision about this entry's compaction fate."""

    decision_body: str = ""
    """For keep: the reason. For summary: the agent-written summary
    that replaces the full tool result at compact time."""

    decided_at: Optional[datetime] = None
    """When the decision was recorded."""

    # File-specific fields
    file_path: Optional[str] = None
    """For file_read entries, the path on disk."""

    file_lines: Optional[Tuple[int, int]] = None
    """For partial file reads, (start, end) as 1-indexed inclusive."""

    file_version: Optional[int] = None
    """Monotonic version number per file path."""

    prior_ctx_id: Optional[str] = None
    """For diff entries, the ctx_id of the previous version."""

    # Hub sharing
    hub_shared: bool = False
    """If True, this entry has been broadcast to hub peers."""

    hub_holders: List[str] = field(default_factory=list)
    """List of peer identities that hold a version of this item."""


@dataclass
class FileVersion:
    """Tracks all versions of a file ever read this session.

    Used by FileTracker to maintain per-file version history.
    """

    path: str
    """The file path."""

    versions: List[LedgerEntry] = field(default_factory=list)
    """Ordered by read time. Last entry is the most recent."""

    @property
    def latest(self) -> Optional[LedgerEntry]:
        """Get the most recent LedgerEntry for this file."""
        return self.versions[-1] if self.versions else None

    @property
    def latest_hash(self) -> Optional[str]:
        """Get the most recent hash for this file."""
        return self.latest.content_hash if self.latest else None
