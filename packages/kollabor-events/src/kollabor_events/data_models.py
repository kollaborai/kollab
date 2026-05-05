"""Shared data models for Kollab."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ConversationMessage:
    """A single message in the conversation.

    Attributes:
        role: Role of the message sender (user, assistant, system, tool).
        content: The message content.
        timestamp: When the message was created.
        metadata: Additional metadata.
        thinking: Optional thinking process information.
    """

    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    thinking: Optional[str] = None


@dataclass
class SessionMetadata:
    """Metadata for a conversation session."""

    session_id: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    message_count: int
    turn_count: int
    working_directory: str
    git_branch: str
    themes: List[str]
    files_mentioned: List[str]
    last_activity: Optional[datetime]
    size_bytes: int
    is_valid: bool
    validation_issues: List[str]


@dataclass
class SessionSummary:
    """Summary of a conversation session."""

    metadata: SessionMetadata
    preview_messages: List[Dict]
    key_topics: List[str]
    user_patterns: List[str]
    project_context: Dict[str, Any]
    compatibility_score: float


@dataclass
class ConversationMetadata:
    """Metadata for conversation discovery."""

    file_path: str
    session_id: str
    title: str
    message_count: int
    created_time: Optional[datetime]
    modified_time: Optional[datetime]
    last_message_preview: str
    topics: List[str]
    file_id: str  # Short ID for display (#12345)
    working_directory: str
    git_branch: str
    duration: Optional[str]
    size_bytes: int
    preview_messages: List[Dict]
    search_relevance: Optional[float] = None
