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

from kollabor_ai.conversation_logger import (
    _acp_display_metadata,
    _session_file_rewrite_lock_path,
    _session_file_write_lock_path,
)
from kollabor_ai.session_naming import generate_branch_name, generate_session_name
from kollabor_config.file_lock import locked_file
from kollabor_events.chat_stream import event_problems

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


def _acp_input_metadata(value: Any) -> Dict[str, Any]:
    """Restore the ACP identity needed to project a user row after JSONL load."""
    if not isinstance(value, dict) or value.get("provider") != "acp":
        return {}
    result: Dict[str, Any] = {"provider": "acp"}
    for key in (
        "request_id",
        "source",
        "thread_id",
        "acp_turn_key",
        "dispatch_epoch",
    ):
        item = value.get(key)
        if isinstance(item, str) and item:
            result[key] = item
    turn_id = value.get("turn_id")
    if isinstance(turn_id, int) and not isinstance(turn_id, bool) and turn_id > 0:
        result["turn_id"] = turn_id
    dispatch_order = value.get("dispatch_order")
    if (
        isinstance(dispatch_order, int)
        and not isinstance(dispatch_order, bool)
        and dispatch_order > 0
    ):
        result["dispatch_order"] = dispatch_order
    outcome = value.get("chat_stream_outcome")
    if isinstance(outcome, str) and outcome in {"completed", "failed", "cancelled"}:
        result["chat_stream_outcome"] = outcome
    if value.get("hub_message") is True:
        result["hub_message"] = True
    hub_message_id = value.get("hub_message_id")
    if isinstance(hub_message_id, str) and hub_message_id:
        result["hub_message_id"] = hub_message_id
    hub_admission = value.get("hub_admission")
    if isinstance(hub_admission, str) and hub_admission in {"input", "note"}:
        result["hub_admission"] = hub_admission
    hub_note = value.get("hub_note")
    if (
        isinstance(hub_note, dict)
        and hub_note.get("type") == "hub-note"
        and isinstance(hub_note.get("from"), str)
        and hub_note["from"]
        and isinstance(hub_note.get("to"), str)
        and isinstance(hub_note.get("text"), str)
        and isinstance(hub_note.get("observed"), bool)
    ):
        note = {
            key: hub_note[key]
            for key in ("type", "from", "to", "text", "observed")
        }
        for key, valid in (
            ("kind", lambda item: isinstance(item, str)),
            ("thread_id", lambda item: isinstance(item, str)),
            ("reply_to", lambda item: isinstance(item, str)),
            ("is_direct", lambda item: isinstance(item, bool)),
            (
                "presence",
                lambda item: isinstance(item, str) and item in {"online", "offline"},
            ),
        ):
            item = hub_note.get(key)
            if item is not None and valid(item):
                note[key] = item
        result["hub_note"] = note
    return result


def _native_hub_chat_stream_row(value: Any) -> Dict[str, Any] | None:
    """Validate and copy one display-only native Hub row from persistence."""
    if not isinstance(value, dict) or value.get("kind") not in {"input", "note"}:
        return None
    parts = value.get("parts")
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict):
        return None
    source_note = parts[0]
    if source_note.get("type") != "hub-note":
        return None
    note_keys = (
        "type",
        "from",
        "to",
        "text",
        "observed",
        "kind",
        "thread_id",
        "reply_to",
        "is_direct",
        "presence",
    )
    row_keys = (
        "id",
        "seq",
        "kind",
        "role",
        "turn_id",
        "status",
        "parts",
        "created_at",
    )
    row = {key: deepcopy(value[key]) for key in row_keys if key in value}
    history_index = value.get("_history_index")
    if (
        isinstance(history_index, int)
        and not isinstance(history_index, bool)
        and history_index >= 0
    ):
        # This is persistence metadata for branch filtering, not part of the
        # public chat-stream row shape. ChatStreamService strips it on output.
        row["_history_index"] = history_index
    history_compaction_round = value.get("_history_compaction_round")
    if (
        isinstance(history_compaction_round, int)
        and not isinstance(history_compaction_round, bool)
        and history_compaction_round >= 0
    ):
        row["_history_compaction_round"] = history_compaction_round
    history_compaction_id = value.get("_history_compaction_id")
    if isinstance(history_compaction_id, str) and history_compaction_id:
        row["_history_compaction_id"] = history_compaction_id
    history_row_revision = value.get("_history_row_revision")
    if (
        isinstance(history_row_revision, int)
        and not isinstance(history_row_revision, bool)
        and history_row_revision >= 0
    ):
        row["_history_row_revision"] = history_row_revision
    row.setdefault("seq", 0)
    row["parts"] = [
        {key: deepcopy(source_note[key]) for key in note_keys if key in source_note}
    ]
    event = {
        "seq": 0,
        "conversation_uid": "persisted-native-hub-row",
        # Newer chat-stream contracts require a daemon generation even for
        # validation-only events. This value is deliberately synthetic: it is
        # never persisted or exposed to clients.
        "generation": "persisted-native-hub-row",
        "type": "row.upsert",
        "data": row,
    }
    if event_problems(event):
        return None
    expected_role = "user" if row["kind"] == "input" else "system"
    if row["role"] != expected_role:
        return None
    return row


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
        # Display-only native Hub rows are persisted beside model history, but
        # stay outside `messages` and `context_window` sent to providers.
        self.chat_stream_rows: List[Dict[str, Any]] = []
        self.chat_stream_row_revision = 0
        self.chat_stream_history_compaction_id: Optional[str] = None
        self.chat_stream_history_length: Optional[int] = None

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

    @staticmethod
    def _history_compaction_id(history: List[Any]) -> Optional[str]:
        for message in reversed(history):
            metadata = (
                message.get("metadata")
                if isinstance(message, dict)
                else getattr(message, "metadata", None)
            )
            if not isinstance(metadata, dict) or not (
                metadata.get("context_compaction")
                or metadata.get("compaction_summary")
            ):
                continue
            compaction_id = metadata.get("compaction_id")
            return (
                compaction_id
                if isinstance(compaction_id, str) and compaction_id
                else None
            )
        return None

    @staticmethod
    def _history_has_compaction_marker(history: List[Any]) -> bool:
        for message in reversed(history):
            metadata = (
                message.get("metadata")
                if isinstance(message, dict)
                else getattr(message, "metadata", None)
            )
            if isinstance(metadata, dict) and (
                metadata.get("context_compaction")
                or metadata.get("compaction_summary")
            ):
                return True
        return False

    def set_chat_stream_history_coordinate(
        self, compaction_id: Optional[str], history_length: int
    ) -> None:
        """Remember the current coordinate system for display-only Hub rows."""
        if not isinstance(history_length, int) or isinstance(history_length, bool):
            return
        self.chat_stream_history_length = max(0, history_length)
        self.chat_stream_history_compaction_id = (
            compaction_id
            if isinstance(compaction_id, str) and compaction_id
            else None
        )

    def record_hub_chat_stream_row(
        self, row: Dict[str, Any], *, history: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """Upsert one structured, display-only native Hub row."""
        normalized = _native_hub_chat_stream_row(row)
        if normalized is None:
            raise ValueError("invalid native Hub chat stream row")
        coordinate_history = history if isinstance(history, list) else None
        if coordinate_history is not None:
            history_length = len(coordinate_history)
            compaction_id = self._history_compaction_id(coordinate_history)
            self.set_chat_stream_history_coordinate(compaction_id, history_length)
        elif self.chat_stream_history_length is not None:
            history_length = self.chat_stream_history_length
            compaction_id = self.chat_stream_history_compaction_id
        else:
            history_length = len(self.messages)
            compaction_id = self._history_compaction_id(self.messages)
        if "_history_index" not in normalized:
            # Store the insertion boundary: a row recorded after N messages
            # belongs after message N-1 and has boundary N. Branch points are
            # inclusive message indexes, so this also excludes a row received
            # after the selected message.
            normalized["_history_index"] = history_length
        if "_history_compaction_round" not in normalized:
            round_history = (
                coordinate_history if coordinate_history is not None else self.messages
            )
            for message in reversed(round_history):
                metadata = (
                    message.get("metadata")
                    if isinstance(message, dict)
                    else getattr(message, "metadata", None)
                )
                if not isinstance(metadata, dict) or not metadata.get(
                    "context_compaction"
                ):
                    continue
                round_number = metadata.get("compaction_round")
                if (
                    isinstance(round_number, int)
                    and not isinstance(round_number, bool)
                    and round_number >= 0
                ):
                    normalized["_history_compaction_round"] = round_number
                break
        if "_history_compaction_id" not in normalized:
            if compaction_id:
                normalized["_history_compaction_id"] = compaction_id
        row_id = normalized["id"]
        for index, existing in enumerate(self.chat_stream_rows):
            if existing.get("id") == row_id:
                # Keep the first arrival position while replacing lifecycle
                # fields and status with the latest durable projection.
                normalized["created_at"] = existing.get(
                    "created_at", normalized["created_at"]
                )
                if isinstance(existing.get("_history_index"), int):
                    normalized["_history_index"] = existing["_history_index"]
                if isinstance(existing.get("_history_compaction_round"), int):
                    normalized["_history_compaction_round"] = existing[
                        "_history_compaction_round"
                    ]
                if isinstance(existing.get("_history_compaction_id"), str):
                    normalized["_history_compaction_id"] = existing[
                        "_history_compaction_id"
                    ]
                revision = existing.get("_history_row_revision")
                if isinstance(revision, int) and not isinstance(revision, bool):
                    normalized["_history_row_revision"] = revision
                    self.chat_stream_row_revision = max(
                        self.chat_stream_row_revision, revision
                    )
                else:
                    self.chat_stream_row_revision += 1
                    normalized["_history_row_revision"] = (
                        self.chat_stream_row_revision
                    )
                self.chat_stream_rows[index] = normalized
                break
        else:
            supplied_revision = normalized.get("_history_row_revision")
            if isinstance(supplied_revision, int) and not isinstance(
                supplied_revision, bool
            ):
                self.chat_stream_row_revision = max(
                    self.chat_stream_row_revision, supplied_revision
                )
            else:
                self.chat_stream_row_revision += 1
                normalized["_history_row_revision"] = self.chat_stream_row_revision
            self.chat_stream_rows.append(normalized)
        return deepcopy(normalized)

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

        def parse_timestamp(ts: Any) -> Optional[datetime]:
            """Parse timestamp to aware UTC; None when missing/unparseable.

            Session files can carry records with missing or corrupted
            stamps (crashed writers, imported transcripts). Duration must
            never make the whole save/resume-persist path fail over one
            bad stamp, so fail open per record instead of raising.
            """
            return _parse_record_stamp(ts)

        start = None
        end = None
        for message in self.messages:
            stamp = parse_timestamp(message.get("timestamp"))
            if stamp is None:
                continue
            if start is None:
                start = stamp
            end = stamp

        if start is None or end is None:
            return "0m"

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
            "chat_stream_rows": self.chat_stream_rows,
            "chat_stream_row_revision": self.chat_stream_row_revision,
            "chat_stream_history_compaction_id": self.chat_stream_history_compaction_id,
            "chat_stream_history_length": self.chat_stream_history_length,
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
            saved_compaction_id = data.get("chat_stream_history_compaction_id")
            saved_history_length = data.get("chat_stream_history_length")
            message_compaction_id = self._history_compaction_id(self.messages)
            has_message_compaction = self._history_has_compaction_marker(self.messages)
            self.set_chat_stream_history_coordinate(
                message_compaction_id
                if has_message_compaction
                else saved_compaction_id
                if isinstance(saved_compaction_id, str)
                else None,
                saved_history_length
                if isinstance(saved_history_length, int)
                and not isinstance(saved_history_length, bool)
                else len(self.messages),
            )
            self.chat_stream_rows = [
                row
                for value in data.get("chat_stream_rows", [])
                if (row := _native_hub_chat_stream_row(value)) is not None
            ]
            self.chat_stream_row_revision = max(
                data.get("chat_stream_row_revision", 0)
                if isinstance(data.get("chat_stream_row_revision", 0), int)
                else 0,
                max(
                    (
                        row.get("_history_row_revision", 0)
                        for row in self.chat_stream_rows
                    ),
                    default=0,
                ),
            )

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
                "chat_stream_rows": self.chat_stream_rows,
                "chat_stream_row_revision": self.chat_stream_row_revision,
                "chat_stream_history_compaction_id": self.chat_stream_history_compaction_id,
                "chat_stream_history_length": self.chat_stream_history_length,
                "message_index": self.message_index,
                "context_window": self.context_window,
                "current_parent_uuid": self.current_parent_uuid,
                "session_stats": self._session_stats_snapshot(),
                "saved_at": datetime.now().isoformat(),
            }

            # Match the logger's lock order: snapshot/rewrite, then JSONL write.
            # The stable sidecars survive atomic transcript replacement.
            with locked_file(
                _session_file_rewrite_lock_path(filepath), "a+", private=True
            ):
                with locked_file(
                    _session_file_write_lock_path(filepath), "a+", private=True
                ):
                    fd = os.open(
                        filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
                    )
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
            saved_compaction_id = data.get("_chat_stream_history_compaction_id")
            saved_history_length = data.get("_chat_stream_history_length")
            self.set_chat_stream_history_coordinate(
                saved_compaction_id
                if isinstance(saved_compaction_id, str)
                else None,
                saved_history_length
                if isinstance(saved_history_length, int)
                and not isinstance(saved_history_length, bool)
                else len(self.messages),
            )
            self.message_index = data.get("message_index", {})
            self.context_window = data.get("context_window", [])
            self.chat_stream_rows = data.get("chat_stream_rows", [])
            self.chat_stream_rows = [
                row
                for value in self.chat_stream_rows
                if (row := _native_hub_chat_stream_row(value)) is not None
            ]
            self.chat_stream_row_revision = max(
                data.get("chat_stream_row_revision", 0)
                if isinstance(data.get("chat_stream_row_revision", 0), int)
                else 0,
                max(
                    (
                        row.get("_history_row_revision", 0)
                        for row in self.chat_stream_rows
                    ),
                    default=0,
                ),
            )
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
        chat_stream_rows_by_id: Dict[str, Dict[str, Any]] = {}
        tool_calls_by_message: Dict[str, set[str]] = {}
        result_batches: Dict[str, Dict[str, Any]] = {}
        pending_compaction: Dict[str, Any] = {}  # last-round-wins
        last_compaction_at: Optional[datetime] = None
        last_compaction_snapshot_count: Optional[int] = None
        last_compaction_retained_positions: Optional[List[int]] = None
        last_compaction_round: Optional[int] = None
        last_compaction_id: Optional[str] = None
        last_compaction_source_id: Optional[str] = None
        last_compaction_snapshot_row_revision: Optional[int] = None
        last_compaction_retained_position_map: Optional[Dict[str, int]] = None
        last_compaction_output_count: Optional[int] = None
        has_applied_native_hub_compaction = False
        chat_stream_row_revision = 0
        saved_history_compaction_id: Optional[str] = None
        native_hub_compactions: List[Dict[str, Any]] = []
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

        def restore_native_hub_row(value: Any) -> bool:
            nonlocal chat_stream_row_revision
            row = _native_hub_chat_stream_row(value)
            if row is None:
                return False
            row_id = row["id"]
            existing = chat_stream_rows_by_id.get(row_id)
            if existing is not None:
                row["created_at"] = existing["created_at"]
                for key in (
                    "_history_index",
                    "_history_compaction_id",
                    "_history_row_revision",
                ):
                    if key in existing:
                        row[key] = existing[key]
            revision = row.get("_history_row_revision")
            if isinstance(revision, int) and not isinstance(revision, bool):
                chat_stream_row_revision = max(chat_stream_row_revision, revision)
            else:
                chat_stream_row_revision += 1
                row["_history_row_revision"] = chat_stream_row_revision
            # A native wake cannot keep running across a daemon restart. Close
            # any durable in-flight input so snapshots do not resurrect a
            # turn that no longer exists. Terminal rows remain untouched.
            if row["kind"] == "input" and row["status"] in {
                "queued",
                "started",
                "joined",
            }:
                row["status"] = "failed"
            chat_stream_rows_by_id[row_id] = row
            # A staged compaction is logged before its in-memory history swap.
            # Rows appended during that window appear after the compaction
            # record in JSONL, so replay every already-seen coordinate transform
            # over a newly restored row before processing later records.
            for compaction in native_hub_compactions:
                if row_id not in chat_stream_rows_by_id:
                    break
                apply_native_hub_compaction(compaction, row_id=row_id)
            return True

        def restore_compaction_positions(data: Dict[str, Any]) -> None:
            """Keep the latest real compaction's source-position boundary.

            Later bookkeeping-only compaction records may omit these fields;
            they must not erase metadata from the actual summary record.
            """
            nonlocal last_compaction_snapshot_count
            nonlocal last_compaction_retained_positions
            nonlocal last_compaction_round
            nonlocal last_compaction_id
            nonlocal last_compaction_source_id
            nonlocal last_compaction_snapshot_row_revision
            nonlocal last_compaction_retained_position_map
            nonlocal last_compaction_output_count
            round_number = data.get("compaction_round")
            if isinstance(round_number, int) and not isinstance(round_number, bool):
                last_compaction_round = round_number
            value = data.get("compaction_id")
            if isinstance(value, str) and value:
                last_compaction_id = value
            elif data.get("content"):
                last_compaction_id = None
            value = data.get("compaction_source_id")
            if isinstance(value, str) and value:
                last_compaction_source_id = value
            elif data.get("content"):
                last_compaction_source_id = None
            value = data.get("compaction_snapshot_row_revision")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                last_compaction_snapshot_row_revision = value
            elif data.get("content"):
                last_compaction_snapshot_row_revision = None
            value = data.get("compaction_output_count")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                last_compaction_output_count = value
            elif data.get("content"):
                last_compaction_output_count = None
            count = data.get("compaction_snapshot_count")
            positions = data.get("compaction_retained_positions")
            if (
                isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
                and isinstance(positions, list)
                and all(
                    isinstance(position, int)
                    and not isinstance(position, bool)
                    and position >= 0
                    for position in positions
                )
            ):
                last_compaction_snapshot_count = count
                last_compaction_retained_positions = sorted(set(positions))
                raw_map = data.get("compaction_retained_position_map")
                if isinstance(raw_map, dict):
                    last_compaction_retained_position_map = {
                        str(source): target
                        for source, target in raw_map.items()
                        if str(source).isdigit()
                        and isinstance(target, int)
                        and not isinstance(target, bool)
                        and target >= 0
                    }
                else:
                    last_compaction_retained_position_map = None
            elif data.get("content"):
                # A newer legacy summary has no reliable positional boundary.
                last_compaction_snapshot_count = None
                last_compaction_retained_positions = None
                last_compaction_retained_position_map = None

        def apply_native_hub_compaction(
            data: Dict[str, Any], *, row_id: Optional[str] = None
        ) -> bool:
            """Filter and translate Hub row coordinates at this compaction."""
            nonlocal has_applied_native_hub_compaction
            snapshot_count = data.get("compaction_snapshot_count")
            retained_positions = data.get("compaction_retained_positions")
            output_count = data.get("compaction_output_count")
            snapshot_revision = data.get("compaction_snapshot_row_revision")
            compaction_id = data.get("compaction_id")
            raw_source_id = data.get("compaction_source_id")
            source_id = (
                raw_source_id
                if isinstance(raw_source_id, str) and raw_source_id
                else None
            )
            raw_map = data.get("compaction_retained_position_map")
            if not (
                isinstance(snapshot_count, int)
                and not isinstance(snapshot_count, bool)
                and snapshot_count >= 0
                and isinstance(retained_positions, list)
                and all(
                    isinstance(position, int)
                    and not isinstance(position, bool)
                    and position >= 0
                    for position in retained_positions
                )
                and isinstance(output_count, int)
                and not isinstance(output_count, bool)
                and output_count >= 0
                and isinstance(compaction_id, str)
                and compaction_id
            ):
                if data.get("content"):
                    has_applied_native_hub_compaction = False
                return False
            position_map = (
                {
                    int(source): target
                    for source, target in raw_map.items()
                    if isinstance(source, str)
                    and source.isdigit()
                    and isinstance(target, int)
                    and not isinstance(target, bool)
                    and target >= 0
                }
                if isinstance(raw_map, dict)
                else {}
            )
            retained = set(retained_positions)
            updated: Dict[str, Dict[str, Any]] = {}
            for current_row_id, row in list(chat_stream_rows_by_id.items()):
                if row_id is not None and current_row_id != row_id:
                    continue
                coordinate = row.get("_history_compaction_id")
                if coordinate == compaction_id:
                    updated[current_row_id] = row
                    continue
                if coordinate is not None and coordinate != source_id:
                    # A record may already carry a more recent coordinate, for
                    # example a row update appended after a saved transcript.
                    updated[current_row_id] = row
                    continue
                position = row.get("_history_index")
                if isinstance(position, int) and not isinstance(position, bool) and position >= 0:
                    revision = row.get("_history_row_revision")
                    concurrent = (
                        isinstance(revision, int)
                        and not isinstance(revision, bool)
                        and isinstance(snapshot_revision, int)
                        and not isinstance(snapshot_revision, bool)
                        and revision > snapshot_revision
                    )
                    if not isinstance(revision, int) and position >= snapshot_count:
                        concurrent = True
                    if concurrent:
                        row["_history_index"] = (
                            output_count + position - snapshot_count
                        )
                        row["_history_compaction_id"] = compaction_id
                        updated[current_row_id] = row
                        continue
                    if position_map:
                        left = position_map.get(position - 1)
                        right = position_map.get(position)
                        if left is not None:
                            row["_history_index"] = left + 1
                        elif right is not None:
                            row["_history_index"] = right
                        else:
                            continue
                    else:
                        if not (
                            position >= snapshot_count
                            or position - 1 in retained
                            or position in retained
                        ):
                            continue
                    row["_history_compaction_id"] = compaction_id
                    updated[current_row_id] = row
                    continue
                # New-format rows always carry a position. Keep legacy rows
                # using their timestamp only when positional metadata is absent.
                stamp = _parse_record_stamp(row.get("created_at"))
                boundary = _parse_record_stamp(data.get("timestamp"))
                if stamp is None or boundary is None or stamp > boundary:
                    updated[current_row_id] = row
            if row_id is None:
                chat_stream_rows_by_id.clear()
                chat_stream_rows_by_id.update(updated)
                native_hub_compactions.append(deepcopy(data))
                has_applied_native_hub_compaction = True
            elif row_id in updated:
                chat_stream_rows_by_id[row_id] = updated[row_id]
            else:
                chat_stream_rows_by_id.pop(row_id, None)
            return True

        def compaction_summary_metadata(round_value: Any) -> Dict[str, Any]:
            summary: Dict[str, Any] = {
                "context_compaction": True,
                "compaction_round": round_value,
            }
            if last_compaction_snapshot_count is not None:
                summary["compaction_snapshot_count"] = last_compaction_snapshot_count
            if last_compaction_retained_positions is not None:
                summary["compaction_retained_positions"] = list(
                    last_compaction_retained_positions
                )
            if last_compaction_id is not None:
                summary["compaction_id"] = last_compaction_id
            if last_compaction_source_id is not None:
                summary["compaction_source_id"] = last_compaction_source_id
            if last_compaction_snapshot_row_revision is not None:
                summary["compaction_snapshot_row_revision"] = (
                    last_compaction_snapshot_row_revision
                )
            if last_compaction_retained_position_map is not None:
                summary["compaction_retained_position_map"] = dict(
                    last_compaction_retained_position_map
                )
            if last_compaction_output_count is not None:
                summary["compaction_output_count"] = last_compaction_output_count
            return summary

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
                        # The saved base replaces all earlier streaming state.
                        pending_compaction = {}
                        last_compaction_at = None
                        last_compaction_snapshot_count = None
                        last_compaction_retained_positions = None
                        last_compaction_round = None
                        last_compaction_id = None
                        last_compaction_source_id = None
                        last_compaction_snapshot_row_revision = None
                        last_compaction_retained_position_map = None
                        last_compaction_output_count = None
                        has_applied_native_hub_compaction = False
                        native_hub_compactions.clear()
                        saved_history_compaction_id = (
                            data.get("chat_stream_history_compaction_id")
                            if isinstance(
                                data.get("chat_stream_history_compaction_id"), str
                            )
                            else None
                        )
                        tool_calls_by_message.clear()
                        result_batches.clear()
                        # A resumed session starts with this durable base,
                        # then the logger appends ordinary streaming turns.
                        # Keep reading instead of discarding that later tail.
                        messages = saved_messages
                        metadata = saved_metadata
                        chat_stream_rows_by_id.clear()
                        chat_stream_row_revision = 0
                        saved_chat_rows = data.get("chat_stream_rows", [])
                        if isinstance(saved_chat_rows, list):
                            for value in saved_chat_rows:
                                restore_native_hub_row(value)
                        saved_row_revision = data.get("chat_stream_row_revision")
                        if isinstance(saved_row_revision, int) and not isinstance(
                            saved_row_revision, bool
                        ):
                            chat_stream_row_revision = max(
                                chat_stream_row_revision, saved_row_revision
                            )
                        # A full snapshot may be the only durable copy of a
                        # compacted summary (for example, after a session save
                        # but before another streaming append). Restore its
                        # source-position boundary alongside the rows.
                        for saved_message in saved_messages:
                            saved_message_metadata = (
                                saved_message.get("metadata")
                                if isinstance(saved_message, dict)
                                else None
                            )
                            if not isinstance(saved_message_metadata, dict) or not (
                                saved_message_metadata.get("context_compaction")
                                or saved_message_metadata.get("compaction_summary")
                            ):
                                continue
                            restore_compaction_positions(
                                {**saved_message_metadata, "content": "saved summary"}
                            )
                            saved_stamp = _parse_record_stamp(
                                saved_message.get("timestamp")
                            )
                            if saved_stamp is not None:
                                last_compaction_at = saved_stamp
                            apply_native_hub_compaction(
                                {
                                    **saved_message_metadata,
                                    "timestamp": saved_message.get("timestamp"),
                                }
                            )
                        current_parent_uuid = data.get("current_parent_uuid")
                        session_stats = data.get("session_stats")
                        has_saved_base = True
                        has_streaming_tail = False
                        recognized_record = True
                        continue

                    # Native Hub rows are display-only structured records.
                    # Restore them beside model messages, never as messages.
                    if restore_native_hub_row(data.get("chat_stream_row")):
                        recognized_record = True

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
                        restore_compaction_positions(data)
                        apply_native_hub_compaction(data)
                        if content:
                            summary_metadata = compaction_summary_metadata(
                                data.get("compaction_round")
                            )
                            pending_compaction = {
                                "uuid": data.get("uuid") or str(uuid4()),
                                "role": "user",
                                "content": content,
                                "timestamp": data.get("timestamp"),
                                "parent_uuid": None,
                                "metadata": summary_metadata,
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
                        entry = (
                            deepcopy(structured)
                            if isinstance(structured, dict)
                            else {"content": data.get("content", "")}
                        )
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
                        # Native input IDs are stored with the original user
                        # message, not in the assistant's display metadata.
                        # Keep the identifier when rebuilding history so resume
                        # retains the row's request correlation.
                        logged_metadata = data.get("message", {}).get("metadata")
                        request_id = (
                            logged_metadata.get("request_id")
                            if isinstance(logged_metadata, dict)
                            else None
                        )
                        if (
                            msg_type == "user"
                            and isinstance(request_id, str)
                            and request_id
                        ):
                            message["metadata"]["request_id"] = request_id
                        if msg_type == "user":
                            message["metadata"].update(
                                _acp_input_metadata(logged_metadata)
                            )
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
                            restore_compaction_positions(data)
                            apply_native_hub_compaction(data)
                            has_streaming_tail = has_saved_base
                            tool_calls_by_message.clear()
                            summary_metadata = compaction_summary_metadata(
                                data.get("compaction_round")
                            )
                            pending_compaction = {
                                "uuid": sidecar_uuid or str(uuid4()),
                                "role": "user",
                                "content": content,
                                "timestamp": data.get("timestamp"),
                                "parent_uuid": None,
                                "metadata": summary_metadata,
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

            # Native Hub rows live beside the model transcript. Apply the
            # same last-compaction boundary so a restart cannot resurrect
            # display rows that were removed from the compacted history.
            if (
                not has_applied_native_hub_compaction
                and (
                    last_compaction_at is not None
                    or (
                        last_compaction_snapshot_count is not None
                        and last_compaction_retained_positions is not None
                    )
                )
            ):
                retained_hub_rows: Dict[str, Dict[str, Any]] = {}
                for row_id, row in chat_stream_rows_by_id.items():
                    row_stamp = _parse_record_stamp(row.get("created_at"))
                    row_position = row.get("_history_index")
                    row_compaction_round = row.get("_history_compaction_round")
                    has_position_metadata = (
                        last_compaction_snapshot_count is not None
                        and last_compaction_retained_positions is not None
                    )
                    if (
                        row_compaction_round == last_compaction_round
                        and last_compaction_round is not None
                    ):
                        retained_hub_rows[row_id] = row
                        continue
                    if has_position_metadata and isinstance(row_position, int) and not isinstance(row_position, bool):
                        adjacent_to_retained = (
                            row_position - 1 in last_compaction_retained_positions
                            or row_position in last_compaction_retained_positions
                        )
                        if not (
                            row_position >= last_compaction_snapshot_count
                            or adjacent_to_retained
                        ):
                            continue
                    elif (
                        row_stamp is not None
                        and last_compaction_at is not None
                        and row_stamp <= last_compaction_at
                    ):
                        continue
                    if (
                        row_stamp is not None
                        and not has_position_metadata
                        and last_compaction_at is not None
                    ):
                        preceding = [
                            index
                            for index, message in enumerate(messages)
                            if (message_stamp := _parse_record_stamp(
                                message.get("timestamp")
                            )) is not None
                            and message_stamp <= row_stamp
                        ]
                        # Position is a boundary count, not the last message
                        # index. The replayed summary is already in `messages`.
                        row["_history_index"] = len(preceding)
                    retained_hub_rows[row_id] = row
                chat_stream_rows_by_id = retained_hub_rows

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

            history_compaction_id = saved_history_compaction_id
            for message in reversed(messages):
                message_metadata = (
                    message.get("metadata")
                    if isinstance(message, dict)
                    else getattr(message, "metadata", None)
                )
                if not isinstance(message_metadata, dict) or not (
                    message_metadata.get("context_compaction")
                    or message_metadata.get("compaction_summary")
                ):
                    continue
                value = message_metadata.get("compaction_id")
                history_compaction_id = (
                    value if isinstance(value, str) and value else None
                )
                break

            # Build summary-like shape to keep interface stable
            return {
                "messages": messages,
                "metadata": metadata,
                "message_index": {m["uuid"]: m for m in messages},
                "context_window": messages,
                "chat_stream_rows": list(chat_stream_rows_by_id.values()),
                "chat_stream_row_revision": chat_stream_row_revision,
                "_chat_stream_history_compaction_id": history_compaction_id,
                "_chat_stream_history_length": len(messages),
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
        self.chat_stream_rows = []
        self.chat_stream_row_revision = 0
        self.chat_stream_history_compaction_id = None
        self.chat_stream_history_length = None
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

            # Native Hub rows are display-only but share the source
            # transcript's timeline. Do not carry rows recorded after the
            # selected message into a branch. Older rows without an explicit
            # position can be located by their timestamps when possible.
            compaction_summary_index = None
            compaction_snapshot_count = None
            compaction_retained_positions: set[int] = set()
            compaction_round = None
            compaction_id = None
            for index, message in enumerate(self.messages):
                message_metadata = message.get("metadata")
                if not isinstance(message_metadata, dict) or not (
                    message_metadata.get("context_compaction")
                    or message_metadata.get("compaction_summary")
                ):
                    continue
                compaction_summary_index = index
                snapshot_count = message_metadata.get("compaction_snapshot_count")
                retained_positions = message_metadata.get(
                    "compaction_retained_positions"
                )
                if (
                    isinstance(snapshot_count, int)
                    and not isinstance(snapshot_count, bool)
                    and snapshot_count >= 0
                    and isinstance(retained_positions, list)
                    and all(
                        isinstance(position, int)
                        and not isinstance(position, bool)
                        and position >= 0
                        for position in retained_positions
                    )
                ):
                    compaction_snapshot_count = snapshot_count
                    compaction_retained_positions = set(retained_positions)
                round_number = message_metadata.get("compaction_round")
                if isinstance(round_number, int) and not isinstance(round_number, bool):
                    compaction_round = round_number
                value = message_metadata.get("compaction_id")
                compaction_id = value if isinstance(value, str) and value else None

            def message_field(message: Any, name: str, default: Any = None) -> Any:
                if isinstance(message, dict):
                    return message.get(name, default)
                return getattr(message, name, default)

            branched_hub_rows = []
            for row in self.chat_stream_rows:
                history_index = row.get("_history_index")
                if (
                    isinstance(history_index, bool)
                    or not isinstance(history_index, int)
                ):
                    row_stamp = _parse_record_stamp(row.get("created_at"))
                    message_positions = [
                        index
                        for index, message in enumerate(self.messages)
                        if (message_stamp := _parse_record_stamp(
                            message.get("timestamp")
                        )) is not None
                        and row_stamp is not None
                        and message_stamp <= row_stamp
                    ]
                    if row_stamp is None:
                        continue
                    history_index = len(message_positions)
                row_compaction_round = row.get("_history_compaction_round")
                row_compaction_id = row.get("_history_compaction_id")
                if (
                    compaction_snapshot_count is not None
                    and compaction_summary_index is not None
                    and not (compaction_id and row_compaction_id == compaction_id)
                    and row_compaction_round != compaction_round
                ):
                    adjacent_to_retained = (
                        history_index - 1 in compaction_retained_positions
                        or history_index in compaction_retained_positions
                    )
                    if not (
                        history_index >= compaction_snapshot_count
                        or adjacent_to_retained
                    ):
                        continue
                    row_stamp = _parse_record_stamp(row.get("created_at"))
                    if row_stamp is None:
                        history_index = compaction_summary_index + int(
                            history_index >= compaction_snapshot_count
                        )
                    else:
                        preceding_tail = sum(
                            1
                            for index, message in enumerate(self.messages)
                            if index > compaction_summary_index
                            and (message_stamp := _parse_record_stamp(
                                message_field(message, "timestamp")
                            )) is not None
                            and message_stamp <= row_stamp
                        )
                        history_index = (
                            compaction_summary_index
                            + preceding_tail
                            + int(
                                preceding_tail > 0
                                or history_index >= compaction_snapshot_count
                            )
                        )
                if history_index <= branch_point_index:
                    branched_row = deepcopy(row)
                    branched_row["_history_index"] = history_index
                    branched_hub_rows.append(branched_row)

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
            self.set_chat_stream_history_coordinate(
                self._history_compaction_id(new_messages), len(new_messages)
            )
            self.message_index = {m["uuid"]: m for m in new_messages}
            self.chat_stream_rows = branched_hub_rows
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
