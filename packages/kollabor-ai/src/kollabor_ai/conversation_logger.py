"""Conversation logging system with intelligence features.

This module provides comprehensive JSONL logging for all conversations,
including message threading, session management, and intelligence features
that learn from user patterns and project context.
"""

import json
import logging
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from kollabor_ai.session_naming import generate_session_name

logger = logging.getLogger(__name__)


def _get_app_version() -> str:
    """Get application version dynamically.

    Tries package metadata first (installed version),
    then falls back to pyproject.toml (development mode).

    Returns:
        Version string or "unknown" if cannot be determined.
    """
    try:
        # Try package metadata (installed version)
        return str(get_version("kollabor"))
    except Exception:
        # Fallback to pyproject.toml (development)
        try:
            pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
            with open(pyproject_path, "rb") as f:
                pyproject = tomllib.load(f)
                return str(pyproject["project"]["version"])
        except Exception:
            return "unknown"


class KollaborConversationLogger:
    """Conversation logger with intelligence features.

    Logs every terminal interaction as structured JSON objects with
    conversation threading, user context analysis, and learning capabilities.
    """

    def __init__(self, conversations_dir: Path):
        """Initialize the conversation logger.

        Args:
            conversations_dir: Directory to store conversation JSONL files
        """
        self.conversations_dir = conversations_dir
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

        # Session management - use memorable session names
        self.session_id = generate_session_name()
        self.session_file = self.conversations_dir / f"{self.session_id}.jsonl"

        # Conversation state
        self.conversation_start_time = datetime.now()
        self.message_count = 0
        self.current_thread_uuid = None

        # Intelligence features
        self.user_patterns: List[str] = []
        self.project_context: Dict[str, Any] = {}
        self.conversation_themes: List[str] = []
        self.solution_history: List[Dict[str, Any]] = []

        # Dynamic context (set by llm_service before log_conversation_start)
        self.app_version = "unknown"
        self.active_plugins: List[str] = []
        self.file_interactions: Dict[str, Any] = {}
        self.llm_provider = "unknown"  # Provider type (openai, anthropic, azure_openai)

    def record_file_interaction(self, file_path: str, operation: str) -> None:
        """Record a file interaction for session tracking.

        Called by the queue processor after successful file operations.
        Populates file_interactions so conversation_end includes
        files_modified.

        Args:
            file_path: Normalized path of the file.
            operation: Tool type (file_edit, file_create, file_delete, etc.).
        """
        self.file_interactions[file_path] = {
            "operation": operation,
            "timestamp": datetime.now().isoformat(),
        }

        # Memory management (inside conversations/)
        self.memory_dir = self.conversations_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._load_conversation_memory()

        logger.info(f"Conversation logger initialized: {self.session_id}")

    async def initialize(self) -> bool:
        """Initialize async resources for conversation logger."""
        # Any async initialization can happen here
        logger.debug("Conversation logger async initialization complete")
        return True

    def reset_session(self, new_session_id: str) -> None:
        """Reset logger state for a new session.

        Encapsulates all internal state changes needed when starting
        a fresh session. Used by SessionManager during session restart.

        Args:
            new_session_id: The new session identifier
        """
        self.session_id = new_session_id
        self.session_file = self.conversations_dir / f"{new_session_id}.jsonl"
        self.message_count = 0
        self.conversation_start_time = datetime.now()
        self.current_thread_uuid = None
        logger.info(f"Logger reset for new session: {new_session_id}")

    def set_context(self, app_version: str, active_plugins: list) -> None:
        """Set dynamic context for session logging.

        Called before log_conversation_start() to provide runtime
        context that isn't available at logger construction time.

        Args:
            app_version: Application version string
            active_plugins: List of active plugin names
        """
        self.app_version = app_version
        self.active_plugins = active_plugins

    def set_provider(self, provider: str):
        """Update the active LLM provider.

        Args:
            provider: The provider type (e.g., "openai", "anthropic", "azure_openai", "custom")
        """
        self.llm_provider = provider
        logger.debug(f"Updated LLM provider to: {provider}")

    async def shutdown(self):
        """Shutdown conversation logger and save state."""
        # Save any pending data
        self._save_conversation_memory()
        logger.info("Conversation logger shutdown complete")

    def _load_conversation_memory(self):
        """Load conversation memory from previous sessions."""
        try:
            # Load user patterns
            patterns_file = self.memory_dir / "user_patterns.json"
            if patterns_file.exists():
                with open(patterns_file, "r") as f:
                    self.user_patterns = json.load(f)

            # Load project context
            context_file = self.memory_dir / "project_context.json"
            if context_file.exists():
                with open(context_file, "r") as f:
                    self.project_context = json.load(f)

            # Load solution history
            solutions_file = self.memory_dir / "solution_history.json"
            if solutions_file.exists():
                with open(solutions_file, "r") as f:
                    self.solution_history = json.load(f)
            else:
                self.solution_history = []

            logger.info("Loaded conversation memory from previous sessions")

        except Exception as e:
            logger.warning(f"Failed to load conversation memory: {e}")
            self.solution_history = []

    def _save_conversation_memory(self):
        """Save conversation memory for future sessions."""
        try:
            # Save user patterns
            patterns_file = self.memory_dir / "user_patterns.json"
            with open(patterns_file, "w") as f:
                json.dump(self.user_patterns, f, indent=2)

            # Save project context
            context_file = self.memory_dir / "project_context.json"
            with open(context_file, "w") as f:
                json.dump(self.project_context, f, indent=2)

            # Save solution history
            solutions_file = self.memory_dir / "solution_history.json"
            with open(solutions_file, "w") as f:
                json.dump(self.solution_history, f, indent=2)

            logger.debug("Saved conversation memory for future sessions")

        except Exception as e:
            logger.error(f"Failed to save conversation memory: {e}")

    def _get_git_branch(self) -> str:
        """Get current git branch."""
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def _get_working_directory(self) -> str:
        """Get current working directory."""
        return str(Path.cwd())

    async def _append_to_jsonl(self, message: Dict[str, Any]):
        """Append message to JSONL file."""
        try:
            with open(self.session_file, "a") as f:
                f.write(json.dumps(message) + "\n")
            self.message_count += 1
        except Exception as e:
            logger.error(f"Failed to write to JSONL: {e}")

    def _analyze_user_context(self, content: str) -> Dict[str, Any]:
        """Analyze user context from message content."""
        context: Dict[str, Any] = {
            "message_length": len(content),
            "has_code": "```" in content,
            "has_question": "?" in content,
            "has_command": any(
                cmd in content.lower()
                for cmd in ["fix", "create", "update", "delete", "implement"]
            ),
            "detected_intent": self._detect_intent(content),
        }

        # Learn from patterns (deduplicated)
        new_patterns = []
        if context["has_command"]:
            new_patterns.append("prefers_direct_commands")
        if context["message_length"] > 200:
            new_patterns.append("provides_detailed_context")
        if context["has_code"]:
            new_patterns.append("shares_code_frequently")
        if context["has_question"]:
            new_patterns.append("asks_clarifying_questions")

        # Add new patterns (deduplicated)
        for pattern in new_patterns:
            if pattern not in self.user_patterns:
                self.user_patterns.append(pattern)
                logger.debug(f"Learned user pattern: {pattern}")

        # Update project context based on content
        self._update_project_context(content)

        return context

    def _update_project_context(self, content: str):
        """Update project context based on message content."""
        # Track file mentions
        import re

        file_mentions = re.findall(
            r"(?:core/|plugins/|tests/|\.py|\.json|\.md)\S*", content
        )
        for file_path in file_mentions:
            if file_path not in self.project_context:
                self.project_context[file_path] = {
                    "mentions": 0,
                    "first_mentioned": datetime.now().isoformat(),
                    "context": "user_discussion",
                }
            self.project_context[file_path]["mentions"] += 1
            self.project_context[file_path][
                "last_mentioned"
            ] = datetime.now().isoformat()

        # Track technologies mentioned
        technologies = [
            "python",
            "async",
            "json",
            "mcp",
            "terminal",
            "llm",
            "hook",
            "plugin",
        ]
        mentioned_tech = [tech for tech in technologies if tech in content.lower()]
        if mentioned_tech:
            if "technologies" not in self.project_context:
                self.project_context["technologies"] = {}
            for tech in mentioned_tech:
                if tech not in self.project_context["technologies"]:
                    self.project_context["technologies"][tech] = 0
                self.project_context["technologies"][tech] += 1

    def _analyze_assistant_response(self, content: str):
        """Analyze assistant response to learn solution patterns."""
        # Track successful solution patterns
        solution_patterns = []

        if "<terminal>" in content:
            solution_patterns.append("uses_terminal_commands")
        if "<tool" in content:
            solution_patterns.append("uses_mcp_tools")
        if "```" in content:
            solution_patterns.append("provides_code_examples")
        if len(content) > 500:
            solution_patterns.append("provides_detailed_explanations")
        if any(
            word in content.lower()
            for word in ["because", "therefore", "however", "first", "next", "then"]
        ):
            solution_patterns.append("explains_reasoning")

        # Add to solution history
        if solution_patterns:
            solution_entry = {
                "timestamp": datetime.now().isoformat(),
                "patterns": solution_patterns,
                "content_length": len(content),
                "session_id": self.session_id,
            }
            self.solution_history.append(solution_entry)

            # Keep only last 100 solutions
            if len(self.solution_history) > 100:
                self.solution_history = self.solution_history[-100:]

    def _detect_intent(self, content: str) -> str:
        """Detect user intent from message."""
        content_lower = content.lower()

        if any(word in content_lower for word in ["fix", "bug", "error", "broken"]):
            return "debugging"
        elif any(
            word in content_lower for word in ["create", "new", "add", "implement"]
        ):
            return "feature_development"
        elif any(
            word in content_lower
            for word in ["refactor", "clean", "improve", "optimize"]
        ):
            return "refactoring"
        elif any(word in content_lower for word in ["help", "how", "what", "explain"]):
            return "seeking_help"
        elif any(word in content_lower for word in ["test", "check", "verify"]):
            return "testing"
        else:
            return "general_conversation"

    def _get_session_context(self) -> Dict[str, Any]:
        """Get current session context."""
        return {
            "conversation_phase": self._determine_conversation_phase(),
            "message_count": self.message_count,
            "session_duration": (
                datetime.now() - self.conversation_start_time
            ).total_seconds(),
            "recurring_themes": (
                list(set(self.conversation_themes[-10:]))
                if self.conversation_themes
                else []
            ),
            "active_files": (
                list(self.file_interactions.keys())[-5:]
                if self.file_interactions
                else []
            ),
        }

    def _determine_conversation_phase(self) -> str:
        """Determine current phase of conversation."""
        if self.message_count < 2:
            return "initiation"
        elif self.message_count < 10:
            return "exploration"
        elif self.message_count < 30:
            return "development"
        else:
            return "deep_work"

    def _get_project_awareness(self) -> Dict[str, Any]:
        """Get project awareness context."""
        return {
            "project_type": self.project_context.get("type", "python_terminal_app"),
            "architecture": self.project_context.get("architecture", "plugin_based"),
            "recent_changes": self.project_context.get("recent_changes", []),
            "known_issues": self.project_context.get("known_issues", []),
            "coding_standards": self.project_context.get("coding_standards", {}),
        }

    def _get_related_sessions(self) -> List[str]:
        """Find related previous sessions."""
        related = []
        try:
            # Look for sessions with similar themes
            for session_file in self.conversations_dir.glob("session_*.jsonl"):
                if session_file.name != self.session_file.name:
                    # Simple heuristic: sessions from same day
                    if session_file.name[:10] == self.session_file.name[:10]:
                        related.append(session_file.stem)
                    if len(related) >= 3:
                        break
        except Exception as e:
            logger.warning(f"Failed to find related sessions: {e}")
        return related

    async def log_conversation_start(self):
        """Log conversation root structure with metadata."""
        root_message = {
            "type": "conversation_metadata",
            "sessionId": self.session_id,
            "startTime": self.conversation_start_time.isoformat() + "Z",
            "endTime": None,
            "uuid": str(uuid4()),
            "timestamp": datetime.now().isoformat() + "Z",
            "cwd": self._get_working_directory(),
            "gitBranch": self._get_git_branch(),
            "version": (
                self.app_version
                if self.app_version != "unknown"
                else _get_app_version()
            ),
            "provider": self.llm_provider,
            "conversation_context": {
                "active_plugins": self.active_plugins,
                "session_goals": [],
                "conversation_summary": "",
            },
            "kollabor_intelligence": {
                "conversation_memory": {
                    "related_sessions": self._get_related_sessions(),
                    "recurring_themes": [],
                    "user_patterns": (
                        self.user_patterns[:10] if self.user_patterns else []
                    ),
                }
            },
        }

        await self._append_to_jsonl(root_message)
        logger.info(
            f"Logged conversation start: {self.session_id} (provider={self.llm_provider})"
        )

    async def log_user_message(
        self,
        content: str,
        parent_uuid: Optional[str] = None,
        user_context: Optional[Dict] = None,
    ) -> str:
        """Log user message with intelligence features."""
        message_uuid = str(uuid4())

        message: Dict[str, Any] = {
            "parentUuid": parent_uuid,
            "isSidechain": False,
            "userType": "external",
            "cwd": self._get_working_directory(),
            "sessionId": self.session_id,
            "version": (
                self.app_version
                if self.app_version != "unknown"
                else _get_app_version()
            ),
            "gitBranch": self._get_git_branch(),
            "type": "user",
            "message": {"role": "user", "content": content},
            "uuid": message_uuid,
            "timestamp": datetime.now().isoformat() + "Z",
            "kollabor_intelligence": {
                "user_context": user_context or self._analyze_user_context(content),
                "session_context": self._get_session_context(),
                "project_awareness": self._get_project_awareness(),
            },
        }

        await self._append_to_jsonl(message)

        # Update conversation themes
        intent = message["kollabor_intelligence"]["user_context"].get("detected_intent")
        if intent:
            self.conversation_themes.append(intent)

        # Save updated conversation memory
        self._save_conversation_memory()

        return message_uuid

    async def log_assistant_message(
        self,
        content: str,
        parent_uuid: str,
        usage_stats: Optional[Dict] = None,
        model: Optional[str] = None,
        thinking_content: Optional[List[str]] = None,
        tool_calls: Optional[List[Dict]] = None,
    ) -> str:
        """Log assistant response with usage statistics.

        Args:
            content: Assistant response content
            parent_uuid: Parent message UUID for threading
            usage_stats: Optional token usage statistics
            model: Optional model name from API response (defaults to configured model)
            thinking_content: Optional list of thinking content blocks
            tool_calls: Optional list of tool call dicts with id, name, input keys
        """
        message_uuid = str(uuid4())

        # Use provided model or fallback to provider or "unknown"
        actual_model = model or self.llm_provider or "unknown"

        # Build content array: text + optional tool_use items
        content_items = []
        if content:
            content_items.append({"type": "text", "text": content})
        if tool_calls:
            for tc in tool_calls:
                content_items.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": tc.get("name", "unknown"),
                        "input": tc.get("input", tc.get("arguments", {})),
                    }
                )
        # Fallback: always have at least one text item
        if not content_items:
            content_items.append({"type": "text", "text": ""})

        message: Dict[str, Any] = {
            "parentUuid": parent_uuid,
            "isSidechain": False,
            "userType": "external",
            "cwd": self._get_working_directory(),
            "sessionId": self.session_id,
            "version": (
                self.app_version
                if self.app_version != "unknown"
                else _get_app_version()
            ),
            "gitBranch": self._get_git_branch(),
            "provider": self.llm_provider,
            "message": {
                "id": f"msg_kollabor_{int(time.time())}",
                "type": "message",
                "role": "assistant",
                "model": actual_model,
                "content": content_items,
                "stop_reason": "tool_use" if tool_calls else None,
                "stop_sequence": None,
                "usage": usage_stats or {},
            },
            "requestId": f"req_kollabor_{int(time.time())}",
            "type": "assistant",
            "uuid": message_uuid,
            "timestamp": datetime.now().isoformat() + "Z",
        }

        # Add kollabor_intelligence only when thinking content or duration exists
        if thinking_content or (usage_stats and usage_stats.get("thinking_duration")):
            message["kollabor_intelligence"] = {
                "thinking": thinking_content or [],
                "has_thinking": bool(thinking_content),
                "thinking_duration": (
                    usage_stats.get("thinking_duration") if usage_stats else None
                ),
            }

        await self._append_to_jsonl(message)

        # Analyze assistant response for learning
        self._analyze_assistant_response(content)

        # Save updated conversation memory
        self._save_conversation_memory()

        return message_uuid

    async def log_system_message(
        self,
        content: str,
        parent_uuid: str,
        subtype: str = "informational",
        tool_use_id: Optional[str] = None,
    ) -> str:
        """Log system messages including hook outputs and tool calls."""
        message_uuid = str(uuid4())

        message: Dict[str, Any] = {
            "parentUuid": parent_uuid,
            "isSidechain": False,
            "userType": "external",
            "cwd": self._get_working_directory(),
            "sessionId": self.session_id,
            "version": (
                self.app_version
                if self.app_version != "unknown"
                else _get_app_version()
            ),
            "gitBranch": self._get_git_branch(),
            "type": "system",
            "subtype": subtype,
            "content": content,
            "isMeta": False,
            "timestamp": datetime.now().isoformat() + "Z",
            "uuid": message_uuid,
            "level": "info",
        }

        if tool_use_id:
            message["toolUseID"] = tool_use_id

        await self._append_to_jsonl(message)
        return message_uuid

    async def log_conversation_end(self):
        """Log conversation end and save memory."""
        # Update the root message with end time
        # Note: In production, we'd update the first line of JSONL
        # For now, append an end marker
        end_message = {
            "type": "conversation_end",
            "sessionId": self.session_id,
            "endTime": datetime.now().isoformat() + "Z",
            "uuid": str(uuid4()),
            "timestamp": datetime.now().isoformat() + "Z",
            "summary": {
                "total_messages": self.message_count,
                "duration": (
                    datetime.now() - self.conversation_start_time
                ).total_seconds(),
                "themes": (
                    list(set(self.conversation_themes))
                    if self.conversation_themes
                    else []
                ),
                "files_modified": (
                    [
                        path
                        for path, info in self.file_interactions.items()
                        if info.get("operation")
                        not in ("file_read", "file_grep", "file_mkdir", "file_rmdir")
                    ]
                    if self.file_interactions
                    else []
                ),
            },
        }

        await self._append_to_jsonl(end_message)

        # Save conversation memory
        self._save_conversation_memory()

        logger.info(f"Logged conversation end: {self.session_id}")

    def list_sessions(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """List available sessions with metadata.

        Args:
            filters: Optional filters for sessions

        Returns:
            List of session metadata sorted by newest first
        """
        sessions = []

        try:
            # Find both old format (session_*) and new format (YYMMDDHHMM-*) files
            all_files = list(self.conversations_dir.glob("session_*.jsonl")) + list(
                self.conversations_dir.glob(
                    "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-*.jsonl"
                )
            )

            for session_file in all_files:
                try:
                    session_info = self._parse_session_file(session_file, filters)
                    if session_info:
                        sessions.append(session_info)
                except Exception as e:
                    logger.warning(f"Failed to parse session file {session_file}: {e}")
                    continue

            # Sort by newest first - robust sorting with multiple fallbacks
            def _sort_key(session: Dict) -> datetime:
                """Extract sortable datetime from session with fallbacks."""
                # 1. Try parsed start_time from metadata
                start_time = session.get("start_time")
                if start_time:
                    try:
                        return datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass

                # 2. Try filename parsing for new format (YYMMDDHHMM-*)
                session_id = session.get("session_id", "")
                if "-" in session_id:
                    try:
                        # New format: YYMMDDHHMM-random_string
                        timestamp_str = session_id[:10]
                        return datetime.strptime(timestamp_str, "%y%m%d%H%M").replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        pass

                # 3. Try filename parsing for old format (session_YYYYMMDD_HHMMSS*)
                if session_id.startswith("session_"):
                    try:
                        timestamp_str = session_id[8:19]  # Extract YYYYMMDD_HHMMSS
                        return datetime.strptime(
                            timestamp_str, "%Y%m%d_%H%M%S"
                        ).replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                # 4. Fallback to file modification time
                try:
                    file_path = Path(session.get("file_path", ""))
                    return datetime.fromtimestamp(
                        file_path.stat().st_mtime, tz=timezone.utc
                    )
                except (OSError, AttributeError):
                    pass

                # 5. Last resort: epoch
                return datetime.min.replace(tzinfo=timezone.utc)

            sessions.sort(key=_sort_key, reverse=True)

        except Exception as e:
            logger.error(f"Failed to scan sessions directory: {e}")

        return sessions

    def _parse_session_file(
        self, session_file: Path, filters: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Parse a session file and extract metadata.

        Args:
            session_file: Path to session JSONL file
            filters: Optional filters

        Returns:
            Session metadata or None if should be filtered out
        """
        from kollabor_ai.session_parser import parse_session_jsonl

        session_info = parse_session_jsonl(session_file)
        if session_info is None:
            return None

        # Apply filters (logger-specific)
        if filters:
            if "working_directory" in filters:
                if session_info["working_directory"] != filters["working_directory"]:
                    return None
            if "git_branch" in filters:
                if session_info["git_branch"] != filters["git_branch"]:
                    return None

        return session_info

    def get_session_summary(self, session_id: str) -> Dict:
        """Get session summary for preview.

        Args:
            session_id: Session identifier

        Returns:
            Session summary
        """
        try:
            # Find session file
            session_file = None
            for file_path in self.conversations_dir.glob(f"{session_id}*.jsonl"):
                session_file = file_path
                break

            if not session_file:
                return {}

            session_info = self._parse_session_file(session_file)
            if not session_info:
                return {}

            # Extract additional summary information
            summary = {
                "metadata": session_info,
                "key_topics": session_info.get("topics", []),
                "user_patterns": [],  # TODO: Extract from memory
                "project_context": {
                    "working_directory": session_info.get("working_directory"),
                    "git_branch": session_info.get("git_branch"),
                    "files_mentioned": [],  # TODO: Extract from messages
                },
                "compatibility_score": 1.0,  # TODO: Calculate based on environment
            }

            return summary

        except Exception as e:
            logger.error(f"Failed to get session summary for {session_id}: {e}")
            return {}

    def search_sessions(self, query: str) -> List[Dict]:
        """Search sessions by content.

        Args:
            query: Search query

        Returns:
            List of matching sessions
        """
        results = []
        query_lower = query.lower()

        try:
            # Search both old format (session_*) and new format (YYMMDDHHMM-*) files
            all_files = list(self.conversations_dir.glob("session_*.jsonl")) + list(
                self.conversations_dir.glob(
                    "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-*.jsonl"
                )
            )

            for session_file in all_files:
                try:
                    # Quick filename check first
                    if query_lower in session_file.name.lower():
                        session_info = self._parse_session_file(session_file)
                        if session_info:
                            results.append(session_info)
                        continue

                    # Content search
                    with open(session_file, "r") as f:
                        content = f.read().lower()

                    if query_lower in content:
                        session_info = self._parse_session_file(session_file)
                        if session_info:
                            # Add search relevance info
                            session_info["search_relevance"] = (
                                self._calculate_search_relevance(content, query_lower)
                            )
                            results.append(session_info)

                except Exception as e:
                    logger.warning(f"Failed to search session file {session_file}: {e}")
                    continue

            # Sort by relevance (most relevant first)
            results.sort(key=lambda x: x.get("search_relevance", 0), reverse=True)

        except Exception as e:
            logger.error(f"Failed to search sessions: {e}")

        return results

    def _calculate_search_relevance(self, content: str, query: str) -> float:
        """Calculate search relevance score.

        Args:
            content: Session content
            query: Search query

        Returns:
            Relevance score (0.0 to 1.0)
        """
        # Simple relevance calculation
        query_words = query.split()
        content_words = content.split()

        if not query_words:
            return 0.0
        if not content_words:
            return 0.0

        # Count exact matches
        exact_matches = content.count(query)

        # Count word matches
        word_matches = sum(1 for word in query_words if word in content)

        # Calculate relevance based on frequency and coverage
        relevance = (exact_matches * 0.5 + word_matches * 0.1) / len(content_words)

        return min(relevance * 100, 1.0)  # Normalize to 0-1
