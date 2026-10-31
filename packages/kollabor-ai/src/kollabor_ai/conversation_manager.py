"""Conversation management for LLM core service.

Manages conversation state, history, context windows,
and message threading for LLM interactions.
"""

import json
import logging
import os
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from kollabor_ai.session_naming import generate_branch_name, generate_session_name
from kollabor_ai.conversation_logger import _acp_display_metadata

logger = logging.getLogger(__name__)


def _parse_record_stamp(value: Any) -> Optional[datetime]:
    """Parse a persisted record timestamp into an aware UTC datetime.

    Aware stamps (``Z`` or a numeric offset) are taken as written; naive
    stamps are read as LOCAL wall-clock time, matching the convention
    documented on kollabor_ai.conversation_logger.utc_stamp for sessions
    recorded before stamps became true UTC. Returns None for missing or
    unparseable values so callers fail open: a record is never dropped
    just because its stamp cannot be understood.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return stamp.astimezone(timezone.utc)


SESSION_STAT_KEYS = (
    "messages",
    "input_tokens",
    "output_tokens",
    "total_input_tokens",
    "total_output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "total_cache_read_tokens",
    "total_cache_creation_tokens",
)


def is_system_initialization_record(record: Any) -> bool:
    if not isinstance(record, dict) or record.get("type") != "user":
        return False
    intelligence = record.get("kollabor_intelligence")
    context = intelligence.get("user_context") if isinstance(intelligence, dict) else None
    return isinstance(context, dict) and context.get("type") == "system_initialization"


def matches_system_initialization_provenance(
    displayed_message: Any, saved_message: Any, logged_record: Any,
) -> bool:
    """Match one aligned history row to its explicitly typed original record.

    A saved base retains UUIDs that the public history DTO omits. The caller
    supplies the corresponding saved row, never a content-only search result.
    This predicate changes no messages, roles, or persistent state.
    """
    if not all(isinstance(item, dict) for item in (displayed_message, saved_message, logged_record)):
        return False
    identifier = saved_message.get("uuid")
    if not isinstance(identifier, str) or not identifier or logged_record.get("uuid") != identifier:
        return False
    if displayed_message.get("role") != "user" or saved_message.get("role") != "user":
        return False
    if not is_system_initialization_record(logged_record):
        return False
    original = logged_record.get("message")
    content = saved_message.get("content")
    return (
        isinstance(original, dict) and original.get("role") == "user"
        and isinstance(content, str) and bool(content)
        and displayed_message.get("content") == content == original.get("content")
    )


def _tool_use_line(block: Dict[str, Any]) -> str:
    """One display line for a logged tool_use block: ``[tool: name] {input}``."""
    args = block.get("input")
    if args and not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    line = f"[tool: {block.get('name', 'unknown')}] {args or ''}".rstrip()
    return line if len(line) <= 120 else line[:117] + "..."


class ConversationManager:
    """Manage conversation state and history.

    Handles message storage, context windows, conversation threading,
    and session management for LLM interactions.
    """

    def __init__(self, config, conversation_logger=None):
        """Initialize conversation manager.

        Args:
            config: Configuration manager
            conversation_logger: Optional conversation logger instance
        """
        self.config = config
        self.conversation_logger = conversation_logger

        # Conversation state - use memorable session names
        # Use logger's session ID if available for consistency across logging systems
        if conversation_logger:
            self.current_session_id = conversation_logger.session_id
        else:
            self.current_session_id = generate_session_name()
        self.messages: List[Dict[str, Any]] = []
        self.message_index: Dict[str, Dict[str, Any]] = {}  # uuid -> message lookup
        self.context_window: List[Dict[str, Any]] = []

        # Configuration
        self.max_context_tokens = config.get("kollabor.llm.max_context_tokens", 4000)
        self.save_conversations = config.get("kollabor.llm.save_conversations", True)

        # Conversation storage - must be provided externally for clean package separation
        # This allows kollabor-ai to remain independent of kollabor's filesystem layout
        if not hasattr(config, "_conversations_dir"):
            raise ValueError(
                "Config must provide '_conversations_dir' attribute. "
                "Use kollabor.config.loader.get_conversations_dir() and set it on config."
            )
        self.conversations_dir: Path = config._conversations_dir
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

        # Snapshots directory for JSON exports (separate from JSONL logs)
        self.snapshots_dir: Path = self.conversations_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        # Current conversation metadata
        self.current_parent_uuid = None  # Track parent UUID for message threading

        self.conversation_metadata = {
            "started_at": datetime.now().isoformat(),
            "message_count": 0,
            "turn_count": 0,
            "topics": [],
            "model_used": None,
        }

        # Bound by LLMService after its live stats dictionary is initialized.
        # Keeping the same mutable mapping lets loads restore counters in place.
        self._session_stats: Optional[Dict[str, Any]] = None

        logger.info(
            f"Conversation manager initialized with session: {self.current_session_id}"
        )

    def bind_session_stats(self, session_stats: Dict[str, Any]) -> None:
        """Bind the live LLM statistics mapping used for persistence and restore."""
        self._session_stats = session_stats

    def _ensure_conversation_uid(self) -> str:
        """Durable conversation identity (goal-command spec 6.5).

        `current_session_id` rotates on resume by design; this UUID is the
        stable key goals attach to. Minted once per conversation, persisted
        in conversation metadata, and carried through `/resume`.
        """
        uid = self.conversation_metadata.get("conversation_uid")
        if not uid:
            uid = uuid4().hex
            self.conversation_metadata["conversation_uid"] = uid
        return uid

    @property
    def conversation_uid(self) -> str:
        return self._ensure_conversation_uid()

    def _session_stats_snapshot(self) -> Dict[str, Any]:
        """Return the stable persisted subset of the live session statistics."""
        source = self._session_stats or {}
        return {key: source.get(key, 0) for key in SESSION_STAT_KEYS}

    def _restore_session_stats(self, saved_stats: Optional[Dict[str, Any]]) -> None:
        """Restore valid known counters, defaulting unsafe values to zero."""
        if self._session_stats is None:
            return
        source = saved_stats if isinstance(saved_stats, dict) else {}
        for key in SESSION_STAT_KEYS:
            value = source.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                logger.warning("Ignoring invalid persisted session counter: %s", key)
                value = 0
            self._session_stats[key] = value

    def add_message(
        self,
        role: str,
        content: str,
        parent_uuid: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a message to the conversation.

        Args:
            role: Message role (user, assistant, system)
            content: Message content
            parent_uuid: UUID of parent message for threading
            metadata: Optional message metadata

        Returns:
            UUID of the added message
        """
        message_uuid = str(uuid4())
        timestamp = datetime.now().isoformat()

        # Update current_parent_uuid for next message
        if parent_uuid:
            self.current_parent_uuid = parent_uuid

        message = {
            "uuid": message_uuid,
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "parent_uuid": parent_uuid or self.current_parent_uuid,
            "metadata": metadata or {},
            "session_id": self.current_session_id,
        }

        # Add to messages list
        self.messages.append(message)
        self.message_index[message_uuid] = message

        # Update context window
        self._update_context_window()

        # Update metadata
        self.conversation_metadata["message_count"] += 1
        if role == "user":
            self.conversation_metadata["turn_count"] += 1

        # NOTE: Logging is handled by llm_service.py, NOT here
        # llm_service.py calls conversation_logger.log_user_message() or log_assistant_message()
        # BEFORE calling _add_conversation_message() -> add_message()
        # Adding logging here would cause double-logging of every message

        # Auto-save if configured
        if self.save_conversations:
            self.save_conversation()

        logger.debug(f"Added {role} message: {message_uuid}")
        return message_uuid

    def get_context_messages(
        self, max_messages: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get messages for LLM context.

        Args:
            max_messages: Maximum number of messages to return

        Returns:
            List of messages for context
        """
        if max_messages:
            return self.messages[-max_messages:]
        return self.context_window

    def _update_context_window(self):
        """Update the context window: the whole conversation, never trimmed.

        Compaction is what shrinks a conversation, not a message-count cap.
        """
        self.context_window = list(self.messages)

    def _get_last_message_uuid(self) -> Optional[str]:
        """Get UUID of the last message."""
        if self.messages:
            return str(self.messages[-1]["uuid"])
        return None

    def get_message_thread(self, message_uuid: str) -> List[Dict[str, Any]]:
        """Get the thread of messages leading to a specific message.

        Args:
            message_uuid: UUID of the target message

        Returns:
            List of messages in the thread
        """
        thread: List[Dict[str, Any]] = []
        current_uuid: Optional[str] = message_uuid

        while current_uuid:
            if current_uuid in self.message_index:
                message = self.message_index[current_uuid]
                thread.insert(0, message)
                current_uuid = message.get("parent_uuid")  # type: ignore[assignment]
            else:
                break

        return thread

    def get_message_by_uuid(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Look up a message by its UUID.

        Used by ContextService to retrieve message content for
        diff generation and eviction.

        Args:
            uuid: The message UUID to look up.

        Returns:
            The message dict, or None if not found.
        """
        return self.message_index.get(uuid)

    def rewrite_message(self, uuid: str, new_content: str) -> bool:
        """Rewrite a message's content in place.

        Used by ContextService for eviction — replaces a heavy
        tool result with a short stub.

        Args:
            uuid: The message UUID to rewrite.
            new_content: The replacement content.

        Returns:
            True if the message was found and rewritten, False otherwise.
        """
        if uuid not in self.message_index:
            logger.warning(f"rewrite_message: UUID {uuid} not found")
            return False

        message = self.message_index[uuid]
        old_len = len(message.get("content", ""))
        message["content"] = new_content

        logger.info(
            f"Rewrote message {uuid}: "
            f"{old_len} -> {len(new_content)} bytes"
        )
        return True

    def search_messages(
        self, query: str, role: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search messages by content.

        Args:
            query: Search query
            role: Optional role filter

        Returns:
            List of matching messages
        """
        results = []
        query_lower = query.lower()

        for message in self.messages:
            if role and message["role"] != role:
                continue

            if query_lower in message["content"].lower():
                results.append(message)

        return results

    def get_conversation_summary(self) -> Dict[str, Any]:
        """Get summary of current conversation.

        Returns:
            Conversation summary statistics
        """
        user_messages = [m for m in self.messages if m["role"] == "user"]
        assistant_messages = [m for m in self.messages if m["role"] == "assistant"]

        # Extract topics from messages
        topics = self._extract_topics()

        summary = {
            "session_id": self.current_session_id,
            "total_messages": len(self.messages),
            "user_messages": len(user_messages),
            "assistant_messages": len(assistant_messages),
            "turn_count": self.conversation_metadata["turn_count"],
            "started_at": self.conversation_metadata["started_at"],
            "duration": self._calculate_duration(),
            "topics": topics,
            "average_message_length": self._calculate_avg_message_length(),
            "context_usage": f"{len(self.context_window)}",
        }

        return summary

    def _extract_topics(self) -> List[str]:
        """Extract main topics from conversation."""
        # Simple keyword extraction for now
        # TODO: Implement more sophisticated topic extraction
        topics = []

        # Common technical keywords to look for
        keywords = [
            "error",
            "bug",
            "feature",
            "implement",
            "fix",
            "create",
            "update",
            "delete",
            "configure",
            "install",
            "debug",
        ]

        all_content = " ".join([m["content"] for m in self.messages])
        all_content_lower = all_content.lower()

        for keyword in keywords:
            if keyword in all_content_lower:
                topics.append(keyword)

        return topics[:5]  # Return top 5 topics

    def _calculate_duration(self) -> str:
        """Calculate conversation duration."""
        if not self.messages:
            return "0m"

        def parse_timestamp(ts: str) -> datetime:
            """Parse timestamp, normalizing to naive UTC."""
            # Handle Z suffix
            ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            # Convert to naive UTC for consistent comparison
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt

        start = parse_timestamp(self.messages[0]["timestamp"])
        end = parse_timestamp(self.messages[-1]["timestamp"])
        duration = end - start

        minutes = duration.total_seconds() / 60
        if minutes < 60:
            return f"{int(minutes)}m"
        else:
            hours = minutes / 60
            return f"{hours:.1f}h"

    def _calculate_avg_message_length(self) -> int:
        """Calculate average message length."""
        if not self.messages:
            return 0

        total_length = sum(len(m["content"]) for m in self.messages)
        return total_length // len(self.messages)

    def save_conversation(self, filename: Optional[str] = None) -> Path:
        """Save current conversation to file.

        Args:
            filename: Optional custom filename

        Returns:
            Path to saved conversation file
        """
        if not filename:
            # Use session_id directly for filename
            filename = f"{self.current_session_id}_snapshot.json"

        # Save snapshots to dedicated snapshots subdirectory
        filepath = self.snapshots_dir / filename

        conversation_data = {
            "metadata": self.conversation_metadata,
            "summary": self.get_conversation_summary(),
            "messages": self.messages,
            "session_stats": self._session_stats_snapshot(),
        }

        with open(filepath, "w") as f:
            json.dump(conversation_data, f, indent=2)

        logger.debug(f"Saved conversation to: {filepath}")
        return filepath

    def load_conversation(self, filepath: Path) -> bool:
        """Load a conversation from file.

        Args:
            filepath: Path to conversation file

        Returns:
            True if loaded successfully
        """
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            self.messages = data.get("messages", [])

            # Restore metadata with defaults for required fields
            loaded_metadata = data.get("metadata", {})
            self.conversation_metadata = {
                "started_at": loaded_metadata.get(
                    "started_at", datetime.now().isoformat()
                ),
                "message_count": loaded_metadata.get(
                    "message_count", len(self.messages)
                ),
                "turn_count": loaded_metadata.get(
                    "turn_count",
                    sum(1 for m in self.messages if m.get("role") == "user"),
                ),
                "topics": loaded_metadata.get("topics", []),
                "model_used": loaded_metadata.get("model_used"),
                # legacy files lack it: mint now, persists on next save
                "conversation_uid": loaded_metadata.get("conversation_uid")
                or uuid4().hex,
            }

            # Rebuild message index
            self.message_index = {m["uuid"]: m for m in self.messages}

            # Update context window and restore persisted usage counters.
            self._update_context_window()
            self._restore_session_stats(data.get("session_stats"))

            logger.info(f"Loaded conversation from: {filepath}")
            return True

        except Exception as e:
            logger.error(f"Failed to load conversation: {e}")
            return False

    def save_session(self, session_id: str) -> bool:
        """Save current session state.

        Args:
            session_id: Session identifier

        Returns:
            True if saved successfully
        """
        try:
            # Use standard naming: {session_id}.jsonl
            filename = f"{session_id}.jsonl"
            filepath = self.conversations_dir / filename

            self._ensure_conversation_uid()
            session_data = {
                "session_id": session_id,
                "metadata": self.conversation_metadata,
                "summary": self.get_conversation_summary(),
                "messages": self.messages,
                "message_index": self.message_index,
                "context_window": self.context_window,
                "current_parent_uuid": self.current_parent_uuid,
                "session_stats": self._session_stats_snapshot(),
                "saved_at": datetime.now().isoformat(),
            }

            fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600)
                # Write as single-line JSON for JSONL compatibility
                f.write(json.dumps(session_data) + "\n")
                f.flush()
                os.fsync(f.fileno())

            logger.info(f"Saved session {session_id} to: {filepath}")
            return True

        except Exception as e:
            logger.error(f"Failed to save session: {e}")
            return False

    def _load_snapshot_session_stats(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Load persisted counters from the sidecar used by streaming sessions."""
        snapshot_file = self.snapshots_dir / f"{session_id}_snapshot.json"
        if not snapshot_file.exists():
            return None
        try:
            with open(snapshot_file, "r") as f:
                snapshot = json.load(f)
            saved_stats = snapshot.get("session_stats")
            return saved_stats if isinstance(saved_stats, dict) else None
        except Exception as e:
            logger.warning(f"Failed to load session stats from {snapshot_file}: {e}")
            return None

    def _snapshot_conversation_uid(self, session_id: str) -> Optional[str]:
        """The conversation uid saved in this session's snapshot, if any."""
        snapshot_file = self.snapshots_dir / f"{session_id}_snapshot.json"
        if not snapshot_file.exists():
            return None
        try:
            with open(snapshot_file, "r") as f:
                metadata = json.load(f).get("metadata")
        except Exception as e:
            logger.warning(f"Cannot read conversation uid from {snapshot_file}: {e}")
            return None
        uid = metadata.get("conversation_uid") if isinstance(metadata, dict) else None
        return uid if isinstance(uid, str) and uid else None

    def load_session(self, session_id: str) -> bool:
        """Load session from storage.

        Args:
            session_id: Session identifier (with or without 'session_' prefix)

        Returns:
            True if loaded successfully
        """
        try:
            # Standard format: {session_id}.jsonl
            session_file = self.conversations_dir / f"{session_id}.jsonl"

            if not session_file.exists():
                logger.error(f"Session file not found: {session_file}")
                return False

            data = self._load_from_jsonl(session_file)
            if not data:
                logger.error(f"Session data empty for: {session_id}")
                return False
            self._restore_snapshot_acp_display(session_id, data.get("messages", []))

            # Restore session state
            self.current_session_id = session_id
            self.messages = data.get("messages", [])
            self.message_index = data.get("message_index", {})
            self.context_window = data.get("context_window", [])
            self.current_parent_uuid = data.get("current_parent_uuid")

            # Restore metadata with defaults for required fields
            loaded_metadata = data.get("metadata", {})
            self.conversation_metadata = {
                "started_at": loaded_metadata.get(
                    "started_at", datetime.now().isoformat()
                ),
                "message_count": loaded_metadata.get(
                    "message_count", len(self.messages)
                ),
                "turn_count": loaded_metadata.get(
                    "turn_count",
                    sum(1 for m in self.messages if m.get("role") == "user"),
                ),
                "topics": loaded_metadata.get("topics", []),
                "model_used": loaded_metadata.get("model_used"),
                # streaming logs never carry it, but the snapshot saved with
                # every message does; mint one only when neither has it (#400)
                "conversation_uid": loaded_metadata.get("conversation_uid")
                or self._snapshot_conversation_uid(session_id)
                or uuid4().hex,
            }

            # Rebuild message index if missing
            if not self.message_index:
                self.message_index = {m["uuid"]: m for m in self.messages}

            # Update context window and restore persisted usage counters. Streaming
            # JSONL sessions store their counters in the matching saved snapshot.
            self._update_context_window()
            saved_stats = data.get("session_stats")
            if saved_stats is None or data.get("_has_streaming_tail"):
                latest_stats = self._load_snapshot_session_stats(session_id)
                if latest_stats is not None:
                    saved_stats = latest_stats
            self._restore_session_stats(saved_stats)

            logger.info(f"Loaded session: {session_id} from: {session_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            return False

    def _restore_snapshot_acp_display(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Recover missing ACP cards from this session's existing rich snapshot.

        Manager and logger UUIDs used to differ. Their assistant parent UUID
        came from the same request, so it can match an unambiguous pair without
        treating text/timestamps or a stale snapshot's whole history as truth.
        """
        snapshot_file = self.snapshots_dir / f"{session_id}_snapshot.json"
        if not snapshot_file.exists():
            return
        try:
            snapshot = json.loads(snapshot_file.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("Cannot recover ACP display metadata (%s)", type(exc).__name__)
            return
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("messages"), list):
            return
        by_uuid, by_parent = defaultdict(list), defaultdict(list)
        for stored in snapshot["messages"]:
            if not isinstance(stored, dict) or stored.get("role") != "assistant":
                continue
            for key, index in (("uuid", by_uuid), ("parent_uuid", by_parent)):
                value = stored.get(key)
                if isinstance(value, str) and value:
                    index[value].append(stored)
        parent_counts = Counter(
            message.get("parent_uuid") for message in messages
            if message.get("role") == "assistant" and isinstance(message.get("parent_uuid"), str)
            and message["parent_uuid"]
        )
        for message in messages:
            if message.get("role") != "assistant":
                continue
            metadata = message.get("metadata") or {}
            if not isinstance(metadata, dict) or any(
                key in metadata for key in ("acp_updates", "tool_calls", "xml_tool_calls", "provider_reasoning")
            ) or metadata.get("provider") not in (None, "acp"):
                continue
            candidates = by_uuid.get(message.get("uuid"), [])
            if not candidates:
                parent = message.get("parent_uuid")
                if not isinstance(parent, str) or not parent or parent_counts.get(parent, 0) != 1:
                    continue
                candidates = by_parent.get(parent, [])
            if len(candidates) != 1:
                continue
            recovered = _acp_display_metadata(candidates[0].get("metadata"))
            if any(key in recovered for key in ("acp_updates", "tool_calls", "stop_reason")):
                message["metadata"] = {**recovered, **metadata}

    def get_available_sessions(self) -> List[Dict[str, Any]]:
        """Get list of available sessions.

        Returns:
            List of session metadata
        """
        sessions: List[Dict[str, Any]] = []

        try:
            # Find both old format (session_*) and new format (YYMMDDHHMM-*) files
            all_files = (
                list(self.conversations_dir.glob("session_*.json"))
                + list(self.conversations_dir.glob("session_*.jsonl"))
                + list(
                    self.conversations_dir.glob(
                        "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-*.jsonl"
                    )
                )  # New format
            )

            for file_path in all_files:
                try:
                    if file_path.suffix == ".jsonl":
                        session_info = self._parse_jsonl_metadata(file_path)
                        if not session_info:
                            continue
                    else:
                        with open(file_path, "r") as f:
                            data = json.load(f)
                        metadata = data.get("metadata", {})
                        summary = data.get("summary", {})
                        session_info = {
                            "session_id": data.get(
                                "session_id", file_path.stem.replace("session_", "")
                            ),
                            "file_path": str(file_path),
                            "start_time": metadata.get("started_at"),
                            "message_count": len(data.get("messages", [])),
                            "turn_count": metadata.get("turn_count", 0),
                            "topics": metadata.get("topics", []),
                            "working_directory": metadata.get(
                                "working_directory", "unknown"
                            ),
                            "git_branch": metadata.get("git_branch", "unknown"),
                            "last_activity": data.get("saved_at"),
                            "size_bytes": file_path.stat().st_size,
                            "duration": summary.get("duration", "0m"),
                            "preview_messages": data.get("messages", [])[:3],
                        }
                    if session_info is not None:
                        sessions.append(session_info)

                except Exception as e:
                    logger.warning(f"Failed to parse session file {file_path}: {e}")
                    continue

            # Sort by last activity (newest first)
            sessions.sort(key=lambda x: x.get("last_activity", ""), reverse=True)

        except Exception as e:
            logger.error(f"Failed to scan sessions directory: {e}")

        return sessions

    def validate_session(self, session_id: str) -> Dict[str, Any]:
        """Validate session for resume compatibility.

        Args:
            session_id: Session identifier (with or without 'session_' prefix)

        Returns:
            Validation result with issues
        """
        validation_result: Dict[str, Any] = {
            "valid": True,
            "issues": [],
            "warnings": [],
            "compatibility_score": 1.0,
        }

        try:
            # Standard format: {session_id}.jsonl
            session_file = self.conversations_dir / f"{session_id}.jsonl"

            if not session_file.exists():
                validation_result["valid"] = False
                validation_result["issues"].append("Session file not found")
                return validation_result

            parsed = self._parse_jsonl_metadata(session_file)
            if not parsed:
                validation_result["valid"] = False
                validation_result["issues"].append("Could not parse session log")
                return validation_result

            metadata = {
                "working_directory": parsed.get("working_directory"),
                "git_branch": parsed.get("git_branch"),
                "topics": parsed.get("topics", []),
                "turn_count": parsed.get("turn_count", 0),
                "started_at": parsed.get("start_time"),
            }

            # Check working directory
            old_directory = metadata.get("working_directory")
            if old_directory and not Path(old_directory).exists():
                validation_result["warnings"].append(
                    f"Original working directory no longer exists: {old_directory}"
                )
                validation_result["compatibility_score"] -= 0.2

            # Check for missing files (simplified - just check metadata)
            missing_files: List[str] = []
            if missing_files:
                validation_result["warnings"].append(
                    f"Some referenced files are missing: {missing_files[:3]}"
                )
                validation_result["compatibility_score"] -= 0.1

            # Ensure compatibility score is within bounds
            validation_result["compatibility_score"] = max(
                0.0, validation_result["compatibility_score"]
            )

        except Exception as e:
            validation_result["valid"] = False
            validation_result["issues"].append(f"Validation error: {str(e)}")

        return validation_result

    def _parse_jsonl_metadata(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Parse minimal metadata from a JSONL session file.

        Delegates to shared session_parser for actual parsing.
        Strips 'session_' prefix from session_id for backward compatibility.
        """
        from kollabor_ai.session_parser import parse_session_jsonl

        session_info = parse_session_jsonl(file_path)
        if session_info is None:
            return None

        # Strip legacy prefix from session_id
        session_info["session_id"] = session_info["session_id"].replace("session_", "")
        return session_info

    def _load_from_jsonl(self, session_file: Path) -> Dict[str, Any]:
        """Load session data from JSONL session file."""
        messages: List[Dict[str, Any]] = []
        tool_calls_by_message: Dict[str, set[str]] = {}
        result_batches: Dict[str, Dict[str, Any]] = {}
        pending_compaction: Dict[str, Any] = {}  # last-round-wins
        last_compaction_at: Optional[datetime] = None
        compaction_uuids: set[str] = set()
        current_parent_uuid: Optional[str] = None
        session_stats: Optional[Dict[str, Any]] = None
        has_saved_base = False
        has_streaming_tail = False
        metadata: Dict[str, Any] = {
            "started_at": None,
            "working_directory": "unknown",
            "git_branch": "unknown",
            "turn_count": 0,
            "topics": [],
        }
        recognized_record = False

        try:
            with open(session_file, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue

                    # Handle save_session format (complete session object)
                    if "messages" in data and isinstance(data["messages"], list):
                        saved_messages = data["messages"]
                        saved_metadata = data.get("metadata", metadata)
                        saved_index = data.get("message_index", {})
                        saved_context = data.get("context_window", [])
                        valid_messages = all(
                            isinstance(message, dict)
                            and isinstance(message.get("uuid"), str)
                            and bool(message["uuid"])
                            for message in saved_messages
                        )
                        valid_index = isinstance(saved_index, dict) and all(
                            isinstance(message_uuid, str)
                            and isinstance(message, dict)
                            for message_uuid, message in saved_index.items()
                        )
                        valid_context = isinstance(saved_context, list) and all(
                            isinstance(message, dict) for message in saved_context
                        )
                        if not (
                            valid_messages
                            and isinstance(saved_metadata, dict)
                            and valid_index
                            and valid_context
                        ):
                            logger.warning(
                                "save_session format validation failed for %s: "
                                "valid_messages=%s valid_index=%s valid_context=%s "
                                "metadata_is_dict=%s",
                                session_file,
                                valid_messages,
                                valid_index,
                                valid_context,
                                isinstance(saved_metadata, dict),
                            )
                            continue
                        # A resumed session starts with this durable base,
                        # then the logger appends ordinary streaming turns.
                        # Keep reading instead of discarding that later tail.
                        messages = saved_messages
                        metadata = saved_metadata
                        current_parent_uuid = data.get("current_parent_uuid")
                        session_stats = data.get("session_stats")
                        has_saved_base = True
                        has_streaming_tail = False
                        pending_compaction = {}
                        last_compaction_at = None
                        tool_calls_by_message.clear()
                        result_batches.clear()
                        recognized_record = True
                        continue

                    # Handle conversation logger streaming format
                    msg_type = data.get("type")
                    if msg_type == "conversation_metadata":
                        recognized_record = True
                        metadata["started_at"] = data.get("startTime")
                        metadata["working_directory"] = data.get("cwd", "unknown")
                        metadata["git_branch"] = data.get("gitBranch", "unknown")
                    elif msg_type == "conversation_end":
                        recognized_record = True
                        summary = data.get("summary", {})
                        metadata["topics"] = summary.get("themes", [])
                    elif msg_type == "context_compaction":
                        # Compaction summary written by context_compaction
                        # plugin (rebirth recovery, aquamarine's gap [B]).
                        # Replay as a user-role message; LAST-ROUND-WINS is
                        # enforced by keeping only the final round — records
                        # before the last one are superseded. Tail-after-
                        # timestamp: only the compaction record is replayed;
                        # user/assistant records AFTER its timestamp are
                        # appended on top by the loop below (they are newer).
                        recognized_record = True
                        has_streaming_tail = has_saved_base
                        tool_calls_by_message.clear()
                        compaction_uuid = data.get("uuid")
                        if isinstance(compaction_uuid, str) and compaction_uuid:
                            compaction_uuids.add(compaction_uuid)
                        content = data.get("content", "")
                        # Parsed once for ordering; an unparseable stamp
                        # leaves last_compaction_at None, which suppresses
                        # nothing (fail open) rather than comparing garbage.
                        parsed_stamp = _parse_record_stamp(
                            data.get("timestamp")
                        )
                        if parsed_stamp is not None:
                            # A later record with a missing/unparseable
                            # stamp keeps the prior boundary: resetting to
                            # None would un-suppress pre-boundary history.
                            last_compaction_at = parsed_stamp
                        if content:
                            pending_compaction = {
                                "uuid": data.get("uuid") or str(uuid4()),
                                "role": "user",
                                "content": content,
                                "timestamp": data.get("timestamp"),
                                "parent_uuid": None,
                                "metadata": {
                                    "context_compaction": True,
                                    "compaction_round": data.get(
                                        "compaction_round"
                                    ),
                                },
                                "session_id": session_file.stem.replace(
                                    "session_", ""
                                ),
                            }
                    elif msg_type == "system" and data.get("subtype") == "tool_result":
                        parent_uuid, tool_id = data.get("parentUuid"), data.get("toolUseID")
                        if (
                            not isinstance(parent_uuid, str) or not isinstance(tool_id, str)
                            or tool_id not in tool_calls_by_message.get(parent_uuid, set())
                        ):
                            continue
                        record_timestamp = data.get("timestamp")
                        record_at = _parse_record_stamp(record_timestamp)
                        if (
                            last_compaction_at is not None and record_at is not None
                            and record_at <= last_compaction_at
                        ):
                            continue
                        structured = data.get("tool_result")
                        entry = deepcopy(structured) if isinstance(structured, dict) else {"content": data.get("content", "")}
                        entry["id"] = tool_id
                        entry["content"] = str(entry.get("content") or "")
                        batch = result_batches.setdefault(parent_uuid, {
                            "uuid": data.get("uuid") or str(uuid4()), "role": "user", "content": "",
                            "timestamp": record_timestamp, "parent_uuid": parent_uuid,
                            "metadata": {"tool_output_batch": True, "xml_tool_results": []},
                            "session_id": session_file.stem.replace("session_", ""),
                        })
                        entries = batch["metadata"]["xml_tool_results"]
                        existing = next((i for i, result in enumerate(entries) if result["id"] == tool_id), None)
                        if existing is None:
                            entries.append(entry)
                        else:
                            entries[existing] = entry
                    elif msg_type in ("user", "assistant"):
                        recognized_record = True
                        record_timestamp = data.get("timestamp")
                        record_at = _parse_record_stamp(record_timestamp)
                        if (
                            last_compaction_at is not None
                            and record_at is not None
                            and record_at <= last_compaction_at
                        ):
                            # The compaction summary supersedes all records at
                            # or before its timestamp; replay only the live tail.
                            continue
                        content = data.get("message", {}).get("content", "")
                        has_streaming_tail = has_saved_base
                        tool_uses: List[Dict[str, Any]] = []
                        if isinstance(content, list):
                            blocks = [b for b in content if isinstance(b, dict)]
                            tool_uses = [
                                b for b in blocks if b.get("type") == "tool_use"
                            ]
                            content = "\n".join(
                                b["text"]
                                for b in blocks
                                if b.get("type", "text") == "text" and b.get("text")
                            )
                        msg_uuid = data.get("uuid") or str(uuid4())
                        message: Dict[str, Any] = {
                            "uuid": msg_uuid,
                            "role": data.get("message", {}).get("role", msg_type),
                            "content": content,
                            "timestamp": data.get("timestamp"),
                            "parent_uuid": data.get("parentUuid"),
                            # Native calls are not reconstructed from streaming
                            # logs. ACP display cards stay separate; only a text
                            # turn can restore its provider-native reasoning.
                            "metadata": _acp_display_metadata(data.get("display_metadata")) or (
                                {"provider_reasoning": data["provider_reasoning"]}
                                if data.get("provider_reasoning") and not tool_uses
                                else {}
                            ),
                            "session_id": session_file.stem.replace("session_", ""),
                        }
                        # The logger stores initial system context as a user
                        # record. Preserve that wire role and recover only its
                        # explicit provenance, never classify by prompt text.
                        if is_system_initialization_record(data):
                            message["metadata"]["system_initialization"] = True
                        if tool_uses:
                            # Browsers read these directly; resume promotes them
                            # to xml_tool_calls, which providers never receive.
                            message["tool_uses"] = tool_uses
                            tool_calls_by_message[msg_uuid] = {
                                block["id"] for block in tool_uses
                                if isinstance(block.get("id"), str) and block["id"]
                            }
                        messages.append(message)
                        if msg_type == "user":
                            metadata["turn_count"] = metadata.get("turn_count", 0) + 1

            # Durable-append recovery: compaction records that failed their
            # primary write live in the ".compaction-recovery.jsonl" sidecar
            # (see _append_jsonl_durable). Merge them before finalization so a
            # session that crashed mid-spill resumes with its final compaction
            # boundary applied. Primary records win on equal stamps (already
            # replayed above); a spilled record supersedes only when its stamp
            # is strictly newer than the current boundary.
            sidecar_path = Path(str(session_file) + ".compaction-recovery.jsonl")
            if sidecar_path.exists():
                try:
                    with open(sidecar_path, "r") as sidecar:
                        for line in sidecar:
                            try:
                                data = json.loads(line.strip())
                            except json.JSONDecodeError:
                                continue
                            if (
                                not isinstance(data, dict)
                                or data.get("type") != "context_compaction"
                            ):
                                continue
                            sidecar_uuid = data.get("uuid")
                            if (
                                isinstance(sidecar_uuid, str)
                                and sidecar_uuid
                                and sidecar_uuid in compaction_uuids
                            ):
                                continue
                            content = data.get("content", "")
                            if not content:
                                continue
                            recognized_record = True
                            parsed_stamp = _parse_record_stamp(
                                data.get("timestamp")
                            )
                            if (
                                parsed_stamp is not None
                                and last_compaction_at is not None
                                and parsed_stamp <= last_compaction_at
                            ):
                                # Older spilled round: the primary (or a newer
                                # spill) already owns the boundary.
                                continue
                            if isinstance(sidecar_uuid, str) and sidecar_uuid:
                                compaction_uuids.add(sidecar_uuid)
                            if parsed_stamp is not None:
                                last_compaction_at = parsed_stamp
                            has_streaming_tail = has_saved_base
                            tool_calls_by_message.clear()
                            pending_compaction = {
                                "uuid": sidecar_uuid or str(uuid4()),
                                "role": "user",
                                "content": content,
                                "timestamp": data.get("timestamp"),
                                "parent_uuid": None,
                                "metadata": {
                                    "context_compaction": True,
                                    "compaction_round": data.get(
                                        "compaction_round"
                                    ),
                                },
                                "session_id": session_file.stem.replace(
                                    "session_", ""
                                ),
                            }
                except OSError as exc:
                    logger.warning(
                        "Could not read compaction recovery sidecar %s: %s",
                        sidecar_path,
                        exc,
                    )

            if not recognized_record:
                return {}

            # Compaction summary replay: LAST-ROUND-WINS (only the final
            # compaction record is replayed — earlier rounds are fully
            # superseded by later ones), and it PREPENDS the messages that
            # arrived after compaction ran (they are newer, the loop kept
            # them in order).
            if pending_compaction:
                if last_compaction_at is not None:
                    messages = [
                        message
                        for message in messages
                        if (
                            (stamp := _parse_record_stamp(message.get("timestamp")))
                            is None
                            or stamp > last_compaction_at
                        )
                    ]
                messages = [pending_compaction] + messages

            # Place each result batch after its owning assistant, even if log
            # records arrived out of order or another turn reused a tool ID.
            # Resume remains text-only for providers; the display projector
            # alone converts these entries into native-looking tool rows.
            with_results: List[Dict[str, Any]] = []
            for message in messages:
                with_results.append(message)
                batch = result_batches.get(message["uuid"])
                if batch is not None:
                    batch["content"] = "\n".join(
                        f"Tool result: {entry['content']}"
                        for entry in batch["metadata"]["xml_tool_results"]
                    )
                    with_results.append(batch)
            messages = with_results
            metadata["message_count"] = len(messages)

            # Build summary-like shape to keep interface stable
            return {
                "messages": messages,
                "metadata": metadata,
                "message_index": {m["uuid"]: m for m in messages},
                "context_window": messages,
                "current_parent_uuid": current_parent_uuid,
                "session_stats": session_stats,
                "_has_streaming_tail": has_streaming_tail,
            }
        except Exception as e:
            logger.error(f"Failed to load JSONL session {session_file}: {e}")
            return {}

    def reset_session(self, new_session_id: str) -> None:
        """Reset manager state for a new session.

        Encapsulates all internal state changes needed when starting
        a fresh session. Used by SessionManager during session restart.

        Args:
            new_session_id: The new session identifier
        """
        self.current_session_id = new_session_id
        self.messages = []
        self.message_index = {}
        self.context_window = []
        self.current_parent_uuid = None
        self.conversation_metadata = {
            "started_at": datetime.now().isoformat(),
            "message_count": 0,
            "turn_count": 0,
            "topics": [],
            "model_used": None,
        }
        logger.info(f"Manager reset for new session: {new_session_id}")

    def clear_conversation(self):
        """Clear current conversation and start fresh."""
        # Save current conversation if it has messages
        if self.messages and self.save_conversations:
            self.save_conversation()

        # Delegate to reset_session with a new generated ID
        self.reset_session(generate_session_name())

        logger.info(f"Cleared conversation, new session: {self.current_session_id}")

    def branch_session(
        self, source_session_id: str, branch_point_index: int
    ) -> Dict[str, Any]:
        """Create a new session branching from a specific message in an existing session.

        Args:
            source_session_id: Session ID to branch from
            branch_point_index: Index of the message to branch from (0-based, inclusive)

        Returns:
            Dict with success status, new session_id, and message count
        """
        try:
            # Load the source session
            if not self.load_session(source_session_id):
                return {
                    "success": False,
                    "error": f"Failed to load source session: {source_session_id}",
                }

            # Validate branch point
            if branch_point_index < 0 or branch_point_index >= len(self.messages):
                return {
                    "success": False,
                    "error": f"Invalid branch point: {branch_point_index}. Session has {len(self.messages)} messages.",
                }

            # Get messages up to and including the branch point
            branched_messages = self.messages[: branch_point_index + 1]

            # Create new session ID with memorable branch name
            new_session_id = generate_branch_name()

            # Update message UUIDs and session IDs for the branch
            new_messages = []
            uuid_map = {}  # Map old UUIDs to new UUIDs

            for msg in branched_messages:
                new_uuid = str(uuid4())
                uuid_map[msg["uuid"]] = new_uuid

                new_msg = {
                    "uuid": new_uuid,
                    "role": msg["role"],
                    "content": msg["content"],
                    "timestamp": msg["timestamp"],
                    "parent_uuid": uuid_map.get(msg.get("parent_uuid")),
                    "metadata": {
                        **msg.get("metadata", {}),
                        "branched_from": source_session_id,
                        "original_uuid": msg["uuid"],
                    },
                    "session_id": new_session_id,
                }
                new_messages.append(new_msg)

            # Set up the new session state
            self.current_session_id = new_session_id
            self.messages = new_messages
            self.message_index = {m["uuid"]: m for m in new_messages}
            self._update_context_window()

            # Update parent UUID for next message
            if new_messages:
                self.current_parent_uuid = new_messages[-1]["uuid"]

            # Update metadata
            self.conversation_metadata = {
                "started_at": datetime.now().isoformat(),
                "branched_from": source_session_id,
                "branch_point": branch_point_index,
                "message_count": len(new_messages),
                "turn_count": sum(1 for m in new_messages if m["role"] == "user"),
                "topics": [],
                "model_used": None,
            }

            # Save the branched session
            self.save_session(new_session_id)

            logger.info(
                f"Created branch session {new_session_id} from {source_session_id} at message {branch_point_index}"
            )

            return {
                "success": True,
                "session_id": new_session_id,
                "message_count": len(new_messages),
                "branch_point": branch_point_index,
                "source_session": source_session_id,
            }

        except Exception as e:
            logger.error(f"Failed to branch session: {e}")
            return {"success": False, "error": str(e)}

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all messages from a session for display/selection.

        Args:
            session_id: Session ID to get messages from

        Returns:
            List of messages with index and preview
        """
        try:
            # Load the session temporarily
            original_state = {
                "session_id": self.current_session_id,
                "messages": self.messages.copy(),
                "message_index": self.message_index.copy(),
                "context_window": self.context_window.copy(),
                "current_parent_uuid": self.current_parent_uuid,
                "metadata": self.conversation_metadata.copy(),
            }

            if not self.load_session(session_id):
                return []

            # Extract message previews
            message_list = []
            for i, msg in enumerate(self.messages):
                content = msg.get("content", "")
                tool_lines = [_tool_use_line(b) for b in msg.get("tool_uses", [])]
                # Get first line, truncate if needed
                first = "\n".join(filter(None, [content, *tool_lines])).split("\n")[0]
                first_line = first[:60] + ("..." if len(first) > 60 else "")

                message_list.append(
                    {
                        "index": i,
                        "uuid": msg["uuid"],
                        "role": msg["role"],
                        "preview": first_line,
                        "timestamp": msg.get("timestamp", ""),
                        # First 200 chars of text for hover/detail, then tool calls
                        "full_content": "\n".join(
                            filter(None, [content[:200], *tool_lines])
                        ),
                    }
                )

            # Restore original state
            self.current_session_id = original_state["session_id"]
            self.messages = original_state["messages"]
            self.message_index = original_state["message_index"]
            self.context_window = original_state["context_window"]
            self.current_parent_uuid = original_state["current_parent_uuid"]
            self.conversation_metadata = original_state["metadata"]

            return message_list

        except Exception as e:
            logger.error(f"Failed to get session messages: {e}")
            return []

    def export_for_training(self) -> List[Dict[str, str]]:
        """Export conversation in format suitable for model training.

        Returns:
            List of message pairs for training
        """
        training_data = []

        for i in range(0, len(self.messages) - 1, 2):
            if (
                self.messages[i]["role"] == "user"
                and i + 1 < len(self.messages)
                and self.messages[i + 1]["role"] == "assistant"
            ):

                training_data.append(
                    {
                        "instruction": self.messages[i]["content"],
                        "response": self.messages[i + 1]["content"],
                    }
                )

        return training_data

    def get_conversation_stats(self) -> Dict[str, Any]:
        """Get detailed conversation statistics.

        Returns:
            Detailed statistics about the conversation
        """
        stats = {
            "session": {
                "id": self.current_session_id,
                "started": self.conversation_metadata["started_at"],
                "duration": self._calculate_duration(),
            },
            "messages": {
                "total": len(self.messages),
                "by_role": {},
                "average_length": self._calculate_avg_message_length(),
                "shortest": min((len(m["content"]) for m in self.messages), default=0),
                "longest": max((len(m["content"]) for m in self.messages), default=0),
            },
            "context": {
                "window_size": len(self.context_window),
            },
            "threading": {
                "unique_threads": len(set(m.get("parent_uuid") for m in self.messages)),
                "max_thread_depth": self._calculate_max_thread_depth(),
            },
        }

        # Count messages by role
        for message in self.messages:
            role = message["role"]
            stats["messages"]["by_role"][role] = (
                stats["messages"]["by_role"].get(role, 0) + 1
            )

        return stats

    def _calculate_max_thread_depth(self) -> int:
        """Calculate maximum thread depth in conversation."""
        max_depth = 0

        for message in self.messages:
            depth = len(self.get_message_thread(message["uuid"]))
            max_depth = max(max_depth, depth)

        return max_depth
