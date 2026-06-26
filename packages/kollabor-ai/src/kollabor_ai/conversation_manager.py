"""Conversation management for LLM core service.

Manages conversation state, history, context windows,
and message threading for LLM interactions.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from kollabor_ai.session_naming import generate_branch_name, generate_session_name

logger = logging.getLogger(__name__)


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
        self.max_history = config.get("kollabor.llm.max_history", 999)
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

        logger.info(
            f"Conversation manager initialized with session: {self.current_session_id}"
        )

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
        """Update the context window with recent messages."""
        # Simple sliding window for now
        # TODO: Implement token counting for precise context management
        self.context_window = self.messages[-self.max_history :]

        # Ensure we have system message if it exists
        system_messages = [m for m in self.messages if m["role"] == "system"]
        if system_messages and system_messages[0] not in self.context_window:
            # Prepend system message
            self.context_window = [system_messages[0]] + self.context_window

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
            "context_usage": f"{len(self.context_window)}/{self.max_history}",
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
            }

            # Rebuild message index
            self.message_index = {m["uuid"]: m for m in self.messages}

            # Update context window
            self._update_context_window()

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

            session_data = {
                "session_id": session_id,
                "metadata": self.conversation_metadata,
                "summary": self.get_conversation_summary(),
                "messages": self.messages,
                "message_index": self.message_index,
                "context_window": self.context_window,
                "current_parent_uuid": self.current_parent_uuid,
                "saved_at": datetime.now().isoformat(),
            }

            with open(filepath, "w") as f:
                # Write as single-line JSON for JSONL compatibility
                f.write(json.dumps(session_data) + "\n")

            logger.info(f"Saved session {session_id} to: {filepath}")
            return True

        except Exception as e:
            logger.error(f"Failed to save session: {e}")
            return False

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
            }

            # Rebuild message index if missing
            if not self.message_index:
                self.message_index = {m["uuid"]: m for m in self.messages}

            # Update context window
            self._update_context_window()

            logger.info(f"Loaded session: {session_id} from: {session_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            return False

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
        metadata: Dict[str, Any] = {
            "started_at": None,
            "working_directory": "unknown",
            "git_branch": "unknown",
            "turn_count": 0,
            "topics": [],
        }

        try:
            with open(session_file, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue

                    # Handle save_session format (complete session object)
                    if "messages" in data and isinstance(data["messages"], list):
                        return {
                            "messages": data["messages"],
                            "metadata": data.get("metadata", metadata),
                            "message_index": data.get("message_index", {}),
                            "context_window": data.get("context_window", []),
                            "current_parent_uuid": data.get("current_parent_uuid"),
                        }

                    # Handle conversation logger streaming format
                    msg_type = data.get("type")
                    if msg_type == "conversation_metadata":
                        metadata["started_at"] = data.get("startTime")
                        metadata["working_directory"] = data.get("cwd", "unknown")
                        metadata["git_branch"] = data.get("gitBranch", "unknown")
                    elif msg_type == "conversation_end":
                        summary = data.get("summary", {})
                        metadata["topics"] = summary.get("themes", [])
                    elif msg_type in ("user", "assistant"):
                        content = data.get("message", {}).get("content", "")
                        if isinstance(content, list) and content:
                            content = content[0].get("text", "")
                        msg_uuid = data.get("uuid") or str(uuid4())
                        messages.append(
                            {
                                "uuid": msg_uuid,
                                "role": data.get("message", {}).get("role", msg_type),
                                "content": content,
                                "timestamp": data.get("timestamp"),
                                "parent_uuid": None,
                                "metadata": {},
                                "session_id": session_file.stem.replace("session_", ""),
                            }
                        )
                        if msg_type == "user":
                            metadata["turn_count"] = metadata.get("turn_count", 0) + 1

            # Build summary-like shape to keep interface stable
            return {
                "messages": messages,
                "metadata": metadata,
                "message_index": {m["uuid"]: m for m in messages},
                "context_window": messages[-self.max_history :],
                "current_parent_uuid": None,
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
                # Get first line, truncate if needed
                first_line = content.split("\n")[0][:60]
                if len(content.split("\n")[0]) > 60:
                    first_line += "..."

                message_list.append(
                    {
                        "index": i,
                        "uuid": msg["uuid"],
                        "role": msg["role"],
                        "preview": first_line,
                        "timestamp": msg.get("timestamp", ""),
                        "full_content": content[
                            :200
                        ],  # First 200 chars for hover/detail
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
                "max_size": self.max_history,
                "utilization": f"{(len(self.context_window) / self.max_history * 100):.1f}%",
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
