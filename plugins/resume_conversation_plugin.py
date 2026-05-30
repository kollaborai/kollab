"""Resume conversation plugin for session management."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from kollabor_events.data_models import (
    ConversationMetadata,
)
from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    Event,
    SlashCommand,
    UIConfig,
)
from kollabor_tui.visual_effects import AgnosterSegment

logger = logging.getLogger(__name__)


class ResumeConversationPlugin:
    """Plugin for resuming previous conversations."""

    def __init__(self, **kwargs) -> None:
        """Initialize the resume conversation plugin."""
        self.name = "resume_conversation"
        self.version = "1.0.0"
        self.description = "Resume previous conversation sessions"
        self.enabled = True
        self.logger = logger

        # Dependencies (will be injected during initialization)
        self.conversation_manager: Optional[Any] = None
        self.conversation_logger: Optional[Any] = None
        self.event_bus: Optional[Any] = None
        self.config: Optional[Any] = None
        self.llm_service: Optional[Any] = None
        self.renderer: Optional[Any] = None

    async def initialize(self, event_bus, config, **kwargs) -> None:
        """Initialize the plugin and register commands.

        Args:
            event_bus: Application event bus.
            config: Configuration manager.
            **kwargs: Additional initialization parameters.
        """
        try:
            self.event_bus = event_bus
            self.config = config

            if self.config and not self.config.get(
                "plugins.resume_conversation.enabled", True
            ):
                self.enabled = False
                self.logger.info("Resume conversation plugin disabled by config")
                return

            # Get dependencies from kwargs
            self.conversation_manager = kwargs.get("conversation_manager")
            self.conversation_logger = kwargs.get("conversation_logger")
            self.llm_service = kwargs.get("llm_service")
            self.renderer = kwargs.get("renderer")

            # Get command registry
            command_registry = kwargs.get("command_registry")
            if not command_registry:
                self.logger.warning(
                    "No command registry provided, resume commands not registered"
                )
                return

            # Register resume command
            self._register_resume_commands(command_registry)

            # Register status view
            await self._register_status_view()

            self.logger.info("Resume conversation plugin initialized successfully")

        except Exception as e:
            self.logger.error(f"Error initializing resume conversation plugin: {e}")
            raise

    async def _register_status_view(self) -> None:
        """Register resume conversation status view.

        DEPRECATED: Old status view API no longer exists.
        Use widget system instead.
        """
        pass

    def _get_status_content(self) -> List[str]:
        """Get resume conversation status (agnoster style)."""
        try:
            seg = AgnosterSegment()
            seg.add_lime("Resume", "dark")
            seg.add_cyan("/resume", "dark")
            seg.add_neutral("restore previous sessions", "mid")
            return [seg.render()]

        except Exception as e:
            self.logger.error(f"Error getting status content: {e}")
            seg = AgnosterSegment()
            seg.add_neutral("Resume: Error", "dark")
            return [seg.render()]

    def _register_resume_commands(self, command_registry) -> None:
        """Register resume-related commands.

        Args:
            command_registry: Command registry for registration.
        """
        try:
            # Main resume command
            resume_command = CommandDefinition(
                name="resume",
                description="Continue a previous conversation",
                handler=self.handle_resume,
                plugin_name=self.name,
                category=CommandCategory.CONVERSATION,
                mode=CommandMode.STATUS_TAKEOVER,
                aliases=["restore", "continue"],
                icon="[⏯]",
                ui_config=UIConfig(
                    type="modal", title="Resume Conversation", height=20, width=80
                ),
                cli_hidden=False,
            )
            command_registry.register_command(resume_command)

            # Fork command - browse and fork conversations
            fork_command = CommandDefinition(
                name="fork",
                description="Browse and fork previous conversations",
                handler=self.handle_sessions,
                plugin_name=self.name,
                category=CommandCategory.CONVERSATION,
                mode=CommandMode.STATUS_TAKEOVER,
                aliases=["history", "forks"],
                icon="[⑂]",
                ui_config=UIConfig(
                    type="modal", title="Conversation History", height=20, width=80
                ),
            )
            command_registry.register_command(fork_command)

            # Branch command
            branch_command = CommandDefinition(
                name="branch",
                description="Fork conversation from a specific message",
                handler=self.handle_branch,
                plugin_name=self.name,
                category=CommandCategory.CONVERSATION,
                mode=CommandMode.STATUS_TAKEOVER,
                aliases=[],
                icon="[⑂]",
                ui_config=UIConfig(
                    type="modal", title="Branch Conversation", height=20, width=80
                ),
                cli_hidden=False,
            )
            command_registry.register_command(branch_command)

        except Exception as e:
            self.logger.error(f"Error registering resume commands: {e}")

    async def handle_resume(self, command: SlashCommand) -> CommandResult:
        """Handle /resume command.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            args = command.args or []
            force = False
            if args:
                force = "--force" in args
                args = [arg for arg in args if arg != "--force"]

            if len(args) == 0:
                # Show session selection modal
                return await self._show_conversation_menu()
            elif len(args) == 1:
                # Resume specific session by ID
                session_id = args[0]
                return await self._load_conversation(session_id, force=force)
            elif len(args) >= 2 and args[0].lower() == "search":
                # Search sessions
                query = " ".join(args[1:])
                return await self._search_conversations(query)
            else:
                # Handle filters and other options
                return await self._handle_resume_options(args)

        except Exception as e:
            self.logger.error(f"Error in resume command: {e}")
            return CommandResult(
                success=False,
                message=f"Error resuming conversation: {str(e)}",
                display_type="error",
            )

    async def handle_sessions(self, command: SlashCommand) -> CommandResult:
        """Handle /sessions command.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            args = command.args or []

            if len(args) == 0:
                # Show all sessions
                return await self._show_conversation_menu()
            elif args[0].lower() == "search":
                if len(args) > 1:
                    query = " ".join(args[1:])
                    return await self._search_conversations(query)
                else:
                    return CommandResult(
                        success=False,
                        message="Usage: /sessions search <query>",
                        display_type="error",
                    )
            else:
                return CommandResult(
                    success=False,
                    message="Usage: /sessions [search <query>]",
                    display_type="error",
                )

        except Exception as e:
            self.logger.error(f"Error in sessions command: {e}")
            return CommandResult(
                success=False,
                message=f"Error browsing sessions: {str(e)}",
                display_type="error",
            )

    async def handle_branch(self, command: SlashCommand) -> CommandResult:
        """Handle /branch command for branching conversations.

        Creates a new branch from any conversation at any message point.
        Original conversation remains intact, new branch is created.

        Usage:
            /branch                    - Show sessions to branch from
            /branch <session_id>       - Show messages to select branch point
            /branch <session_id> <idx> - Create branch from message at index

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            args = command.args or []

            if not self._ensure_conversation_manager():
                return CommandResult(
                    success=False,
                    message="Conversation manager not available",
                    display_type="error",
                )

            if len(args) == 0:
                # Step 1: Show sessions to branch from (including current)
                return await self._show_branch_session_selector()

            elif len(args) == 1:
                # Step 2: Show messages from session to select branch point
                session_id = args[0]
                return await self._show_branch_point_selector(session_id)

            elif len(args) >= 2:
                # Step 3: Execute the branch - create new session from branch point
                session_id = args[0]
                self.logger.info(
                    f"[BRANCH] Step 3: Execute branch from session={session_id}"
                )
                try:
                    branch_index = int(args[1])
                except ValueError:
                    self.logger.error(f"[BRANCH] Invalid branch index: {args[1]}")
                    return CommandResult(
                        success=False,
                        message=f"Invalid branch index: {args[1]}. Must be a number.",
                        display_type="error",
                    )

                self.logger.info(f"[BRANCH] Executing branch at index {branch_index}")
                # Create branch and load it
                result = await self._execute_branch_from_session(
                    session_id, branch_index
                )
                self.logger.info(
                    f"[BRANCH] Result: success={result.success}, message={result.message[:50]}..."
                )
                return result

        except Exception as e:
            self.logger.error(f"Error in branch command: {e}")
            return CommandResult(
                success=False,
                message=f"Error branching conversation: {str(e)}",
                display_type="error",
            )

        return CommandResult(
            success=False,
            message="Unexpected branch command state",
            display_type="error",
        )

    async def _execute_branch_from_session(
        self, session_id: str, branch_index: int
    ) -> CommandResult:
        """Execute branch operation - create new session from branch point and load it.

        Args:
            session_id: Source session to branch from (or "current" for active conversation).
            branch_index: Index to branch from (inclusive).

        Returns:
            Command result.
        """
        try:
            # Handle current conversation
            if session_id == "current":
                if not self.llm_service or not hasattr(
                    self.llm_service, "conversation_history"
                ):
                    return CommandResult(
                        success=False,
                        message="No current conversation available",
                        display_type="error",
                    )

                current_messages = self.llm_service.conversation_history
                if branch_index < 0 or branch_index >= len(current_messages):
                    return CommandResult(
                        success=False,
                        message=f"Invalid index. Must be 0-{len(current_messages)-1}.",
                        display_type="error",
                    )

                # Create branch from current - keep messages up to branch_index.
                # Use the in-place helper so QueueProcessor / SessionManager /
                # hub plugin keep their cached list reference valid.
                branched_messages = current_messages[: branch_index + 1]
                self._replace_conversation_history(branched_messages)

                # Update session stats to reflect truncated messages
                if hasattr(self.llm_service, "session_stats"):
                    self.llm_service.session_stats["messages"] = len(branched_messages)

                msg_count = len(branched_messages)
                removed_count = len(current_messages) - msg_count

                return CommandResult(
                    success=True,
                    message=f"[ok] Branched at message {branch_index}\n"
                    f"    Kept {msg_count} messages, removed {removed_count}\n"
                    f"    Continue the conversation from this point.",
                    display_type="success",
                )

            # Branch from saved session
            assert self.conversation_manager is not None
            result = self.conversation_manager.branch_session(session_id, branch_index)

            if not result.get("success"):
                return CommandResult(
                    success=False,
                    message=f"Branch failed: {result.get('error', 'Unknown error')}",
                    display_type="error",
                )

            new_session_id = result.get("session_id")
            return await self._load_and_display_session(
                header=f"--- Branched from {session_id} at message {branch_index} ---",
                success_msg=(
                    f"[ok] Created branch: {new_session_id}\n"
                    f"    From: {session_id} at message {result['branch_point']}\n"
                    f"    Loaded {result['message_count']} messages. Continue below."
                ),
            )

        except Exception as e:
            self.logger.error(f"Error executing branch: {e}")
            return CommandResult(
                success=False,
                message=f"Error executing branch: {str(e)}",
                display_type="error",
            )

    async def _show_branch_session_selector(self) -> CommandResult:
        """Show modal to select a session to branch from."""
        session_items = []

        # Add current conversation as first option if it has messages
        current_msg_count = 0
        current_messages: List[Any] = []
        if self.llm_service and hasattr(self.llm_service, "conversation_history"):
            current_messages = self.llm_service.conversation_history or []
            current_msg_count = len(current_messages)

        if current_msg_count >= 2:
            # Get first user message for preview
            first_user_msg = ""
            for msg in current_messages:
                role = msg.role if hasattr(msg, "role") else msg.get("role", "")
                content = (
                    msg.content if hasattr(msg, "content") else msg.get("content", "")
                )
                if role == "user" and content:
                    first_user_msg = content[:45].split("\n")[0]
                    if len(content) > 45:
                        first_user_msg += "..."
                    break

            session_items.append(
                {
                    "id": "current",
                    "title": f"[*CURRENT*] {first_user_msg or 'Active conversation'}",
                    "subtitle": f"{current_msg_count} msgs | this session",
                    "metadata": {"session_id": "current"},
                    "action": "branch_select_session",
                }
            )

        # Add saved conversations
        conversations = await self.discover_conversations(limit=20)

        for conv in conversations:
            time_str = (
                conv.created_time.strftime("%m/%d %H:%M")
                if conv.created_time
                else "Unknown"
            )

            # Get user's actual request - skip system prompts and preambles
            user_request = ""
            skip_patterns = [
                "kollabor",
                "system prompt",
                "you are",
                "assistant",
                "as an ai",
            ]
            if conv.preview_messages:
                for msg in conv.preview_messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role == "system" or not content:
                        continue
                    content_lower = content.lower()[:50]
                    if any(p in content_lower for p in skip_patterns):
                        continue
                    if role == "user" and content:
                        user_request = content
                        break

            # Extract meaningful first line
            first_line = ""
            if user_request:
                for line in user_request.split("\n"):
                    line = line.strip()
                    if line and len(line) > 3:
                        first_line = line[:60] + ("..." if len(line) > 60 else "")
                        break
            first_line = first_line or "No preview"

            # Extract session name
            session_name = ""
            if conv.session_id and "-" in conv.session_id:
                parts = conv.session_id.split("-", 1)
                if len(parts) > 1:
                    session_name = parts[1]

            # Clean subtitle with session name
            subtitle_parts = []
            if session_name:
                subtitle_parts.append(session_name)
            subtitle_parts.append(f"{conv.message_count} msgs")
            if conv.git_branch and conv.git_branch not in ["-", "unknown"]:
                subtitle_parts.append(conv.git_branch)

            session_items.append(
                {
                    "id": conv.session_id,
                    "title": f"[{time_str}] {first_line}",
                    "subtitle": " | ".join(subtitle_parts),
                    "metadata": {"session_id": conv.session_id},
                    "action": "branch_select_session",
                }
            )

        if not session_items:
            return CommandResult(
                success=False,
                message="No conversations found to branch from. Start a conversation first.",
                display_type="info",
            )

        modal_definition = {
            "title": "Branch From Session",
            "footer": "Up/Down navigate | Enter select | Esc cancel",
            # Width is dynamic (terminal_width - 2)
            "height": 20,
            "sections": [
                {
                    "title": f"Select session to branch ({len(session_items)} available)",
                    "type": "session_list",
                    "sessions": session_items,
                }
            ],
        }

        return CommandResult(
            success=True,
            message="Select a session to branch from",
            display_type="modal",
            ui_config=UIConfig(
                type="modal",
                title=str(modal_definition["title"]),
                modal_config=modal_definition,
            ),
        )

    async def _show_branch_point_selector(self, session_id: str) -> CommandResult:
        """Show modal to select branch point message."""
        messages = []

        # Handle current conversation
        if session_id == "current":
            if self.llm_service and hasattr(self.llm_service, "conversation_history"):
                current_messages = self.llm_service.conversation_history
                for i, msg in enumerate(current_messages or []):
                    role = (
                        msg.role if hasattr(msg, "role") else msg.get("role", "unknown")
                    )
                    content = (
                        msg.content
                        if hasattr(msg, "content")
                        else msg.get("content", "")
                    )
                    preview = content[:50].replace("\n", " ")
                    if len(content) > 50:
                        preview += "..."
                    messages.append(
                        {
                            "index": i,
                            "role": role,
                            "preview": preview,
                            "timestamp": None,
                        }
                    )
            title_text = "Current Session"
        else:
            # Get messages from saved session
            assert self.conversation_manager is not None
            messages = self.conversation_manager.get_session_messages(session_id)
            # Format title - extract readable part from session_id (e.g., "2512301235-shadow-rise" -> "shadow-rise")
            parts = session_id.split("-", 1)
            title_text = parts[1] if len(parts) > 1 else session_id[:12]

        if not messages:
            return CommandResult(
                success=False,
                message=f"No messages found in session: {session_id}",
                display_type="error",
            )

        # Deduplicate consecutive messages with same role and content
        deduped_messages = []
        prev_role = None
        prev_content = None
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("preview", "")[:50]  # Compare first 50 chars
            if role == prev_role and content == prev_content:
                continue  # Skip duplicate
            deduped_messages.append(msg)
            prev_role = role
            prev_content = content

        # Build message selector - skip system messages from display
        message_items = []
        for msg in deduped_messages:
            role = msg["role"]
            preview = msg["preview"]

            # Skip system prompt messages from selection (can't meaningfully branch from them)
            if role == "system":
                continue

            # Role indicators and labels
            if role == "user":
                role_indicator = "YOU:"
                role_label = "user"
            else:
                role_indicator = "AI:"
                role_label = "assistant"

            # Clean up preview
            clean_preview = preview.strip()[:55]
            if len(preview) > 55:
                clean_preview += "..."

            # Format timestamp if available
            ts = msg.get("timestamp")
            time_str = ts[11:16] if ts and len(ts) > 16 else ""  # Just HH:MM

            message_items.append(
                {
                    "id": str(msg["index"]),
                    "title": f"[{msg['index']}] {role_indicator} {clean_preview}",
                    "subtitle": f"{role_label}{' | ' + time_str if time_str else ''}",
                    "metadata": {
                        "session_id": session_id,
                        "message_index": msg["index"],
                    },
                    "action": "branch_execute",
                    "exit_mode": "minimal",  # Plugin will display content after modal exit
                }
            )

        modal_definition = {
            "title": f"Branch Point: {title_text}",
            "footer": "Up/Down navigate | Enter branch here | Esc cancel",
            # Width is dynamic (terminal_width - 2)
            "height": 20,
            "sections": [
                {
                    "title": f"Select message to branch from ({len(message_items)} messages)",
                    "type": "session_list",
                    "sessions": message_items,
                }
            ],
        }

        return CommandResult(
            success=True,
            message="Select branch point",
            display_type="modal",
            ui_config=UIConfig(
                type="modal",
                title=str(modal_definition["title"]),
                modal_config=modal_definition,
            ),
        )

    async def discover_conversations(
        self, limit: Optional[int] = None
    ) -> List[ConversationMetadata]:
        """Discover available conversations.

        Args:
            limit: Maximum number of conversations to return

        Returns:
            List of conversation metadata
        """
        if limit is None:
            limit = (
                self.config.get("plugins.resume_conversation.max_conversations", 50)
                if self.config
                else 50
            )
        conversations: List[ConversationMetadata] = []

        try:
            if not self.conversation_logger:
                self.logger.warning("Conversation logger not available")
                return conversations

            # Get sessions from conversation logger
            sessions = self.conversation_logger.list_sessions()

            for session_data in sessions:
                # Stop if we have enough conversations
                if len(conversations) >= limit:
                    break

                try:
                    # Filter out sessions with 2 or fewer messages
                    message_count = session_data.get("message_count", 0)
                    if message_count <= 2:
                        continue

                    # Strip 'session_' prefix if present for compatibility with conversation_manager
                    raw_session_id = session_data.get("session_id", "")
                    session_id = (
                        raw_session_id.replace("session_", "")
                        if raw_session_id.startswith("session_")
                        else raw_session_id
                    )

                    metadata = ConversationMetadata(
                        file_path=session_data.get("file_path", ""),
                        session_id=session_id,
                        title=self._generate_session_title(session_data),
                        message_count=message_count,
                        created_time=self._parse_datetime(
                            session_data.get("start_time")
                        ),
                        modified_time=self._parse_datetime(
                            session_data.get("end_time")
                        ),
                        last_message_preview=session_data.get("preview_messages", [{}])[
                            0
                        ].get("content", ""),
                        topics=session_data.get("topics", []),
                        file_id=self._generate_file_id(
                            session_data.get("session_id", "")
                        ),
                        working_directory=session_data.get(
                            "working_directory", "unknown"
                        ),
                        git_branch=session_data.get("git_branch", "unknown"),
                        duration=session_data.get("duration"),
                        size_bytes=session_data.get("size_bytes", 0),
                        preview_messages=session_data.get("preview_messages", []),
                    )
                    conversations.append(metadata)

                except Exception as e:
                    self.logger.warning(f"Failed to process session: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Failed to discover conversations: {e}")

        return conversations

    async def _show_conversation_menu(self) -> CommandResult:
        """Show interactive conversation selection menu.

        Returns:
            Command result with modal UI.
        """
        try:
            conversations = await self.discover_conversations()

            if not conversations:
                msg = (
                    "No saved conversations found.\n\n"
                    "Tip: Use /save to save current conversations for future resumption."
                )
                return CommandResult(success=False, message=msg, display_type="info")

            # Build modal definition
            modal_definition = self._build_conversation_modal(conversations)

            return CommandResult(
                success=True,
                message="Select a conversation to resume",
                ui_config=UIConfig(
                    type="modal",
                    title=modal_definition["title"],
                    width=modal_definition.get("width"),
                    height=(
                        int(modal_definition["height"])
                        if modal_definition.get("height")
                        else 0
                    ),
                    modal_config=modal_definition,
                ),
                display_type="modal",
            )

        except Exception as e:
            self.logger.error(f"Error showing conversation menu: {e}")
            return CommandResult(
                success=False,
                message=f"Error loading conversations: {str(e)}",
                display_type="error",
            )

    async def _search_conversations(self, query: str) -> CommandResult:
        """Search conversations by content.

        Args:
            query: Search query

        Returns:
            Command result with search modal.
        """
        try:
            if not self.conversation_logger:
                return CommandResult(
                    success=False,
                    message="Conversation search not available",
                    display_type="error",
                )

            sessions = self.conversation_logger.search_sessions(query)

            if not sessions:
                return CommandResult(
                    success=False,
                    message=f"No conversations found matching: {query}",
                    display_type="info",
                )

            # Convert to conversation metadata
            conversations = []
            for session_data in sessions[:20]:  # Limit search results
                # Strip 'session_' prefix if present for compatibility with conversation_manager
                raw_session_id = session_data.get("session_id", "")
                session_id = (
                    raw_session_id.replace("session_", "")
                    if raw_session_id.startswith("session_")
                    else raw_session_id
                )

                metadata = ConversationMetadata(
                    file_path=session_data.get("file_path", ""),
                    session_id=session_id,
                    title=self._generate_session_title(session_data),
                    message_count=session_data.get("message_count", 0),
                    created_time=self._parse_datetime(session_data.get("start_time")),
                    modified_time=self._parse_datetime(session_data.get("end_time")),
                    last_message_preview=session_data.get("preview_messages", [{}])[
                        0
                    ].get("content", ""),
                    topics=session_data.get("topics", []),
                    file_id=self._generate_file_id(session_data.get("session_id", "")),
                    working_directory=session_data.get("working_directory", "unknown"),
                    git_branch=session_data.get("git_branch", "unknown"),
                    duration=session_data.get("duration"),
                    size_bytes=session_data.get("size_bytes", 0),
                    preview_messages=session_data.get("preview_messages", []),
                    search_relevance=session_data.get("search_relevance"),
                )
                conversations.append(metadata)

            # Build search modal
            modal_definition = self._build_search_modal(conversations, query)

            return CommandResult(
                success=True,
                message=f"Found {len(conversations)} conversations matching: {query}",
                ui_config=UIConfig(
                    type="modal",
                    title=modal_definition["title"],
                    width=modal_definition.get("width"),
                    height=(
                        int(modal_definition["height"])
                        if modal_definition.get("height")
                        else 0
                    ),
                    modal_config=modal_definition,
                ),
                display_type="modal",
            )

        except Exception as e:
            self.logger.error(f"Error searching conversations: {e}")
            return CommandResult(
                success=False,
                message=f"Error searching conversations: {str(e)}",
                display_type="error",
            )

    def _get_conversation_manager(self):
        """Get or create conversation manager instance.

        Returns:
            Conversation manager instance or None.
        """
        # Return existing if available
        if self.conversation_manager:
            return self.conversation_manager

        # Try to create one
        try:
            from kollabor_ai import ConversationManager
            from kollabor_config.config_utils import get_conversations_dir

            # Use a basic config if no manager available
            class BasicConfig:
                def get(self, key, default=None):
                    return default

            config = BasicConfig()
            conversations_dir = get_conversations_dir()
            conversations_dir.mkdir(parents=True, exist_ok=True)

            self.conversation_manager = ConversationManager(config)
            return self.conversation_manager
        except Exception as e:
            self.logger.warning(f"Could not create conversation manager: {e}")
            return None

    def _ensure_conversation_manager(self) -> bool:
        """Ensure conversation manager is available. Returns True if available."""
        if not self.conversation_manager:
            self.conversation_manager = self._get_conversation_manager()
        return self.conversation_manager is not None

    def _replace_conversation_history(self, new_messages: list) -> None:
        """Replace the LLM service's conversation history in place.

        Preserves the list object's identity by using list.clear() +
        list.extend() instead of assigning a new list. This matters
        because QueueProcessor, SessionManager, and the hub plugin
        all hold references to llm_service.conversation_history from
        their __init__. A new list assignment would leave them
        pointing at a stale empty list and break mid-session injection.

        Bug caught by the phase 4.5 inventory: /resume, /branch, and
        /load-and-display all did `conversation_history = loaded_messages`
        which silently desynced the 176+ call sites that cached a
        reference. This helper is the canonical primitive; every path
        that resets history should go through it.

        If llm_service is missing or its conversation_history isn't a
        mutable list (e.g. a tuple in a test fixture), this falls
        back to direct assignment so tests don't crash -- but log a
        warning because that path can't preserve identity.
        """
        if not self.llm_service:
            return
        history = getattr(self.llm_service, "conversation_history", None)
        if (
            history is None
            or not hasattr(history, "clear")
            or not hasattr(history, "extend")
        ):
            self.logger.warning(
                "conversation_history is not a mutable list; "
                "falling back to direct assignment (list identity lost)"
            )
            self.llm_service.conversation_history = new_messages
            return
        history.clear()
        history.extend(new_messages)

    def _prepare_session_display(self, header: str, success_msg: str) -> list:
        """Prepare session messages for display. Loads into llm_service but returns messages instead of displaying.

        Args:
            header: Header message
            success_msg: Success message

        Returns:
            List of display message tuples, or empty list on failure
        """
        if not self.llm_service:
            self.logger.warning("llm_service not available")
            return []

        assert self.conversation_manager is not None
        raw_messages = self.conversation_manager.messages
        self.logger.info(f"[SESSION] raw_messages count: {len(raw_messages)}")

        from kollabor_events.data_models import ConversationMessage

        loaded_messages = []
        display_messages: List[tuple] = [("system", header, {})]

        for msg in raw_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            loaded_messages.append(ConversationMessage(role=role, content=content))

            if role in ("user", "assistant"):
                display_messages.append((role, content, {}))

        # In-place replace preserves list identity; see
        # _replace_conversation_history docstring for the bug history.
        self._replace_conversation_history(loaded_messages)

        if hasattr(self.llm_service, "session_stats"):
            self.llm_service.session_stats["messages"] = len(loaded_messages)

        display_messages.append(("system", success_msg, {}))
        return display_messages

    async def _load_and_display_session(
        self, header: str, success_msg: str
    ) -> CommandResult:
        """Load session into llm_service and display in UI.

        Reads messages from self.conversation_manager.messages (must be populated first).
        Used by both resume and branch after they load/create the session.

        Args:
            header: Header message for the display
            success_msg: Success message to show at end

        Returns:
            CommandResult (always success with empty message, or error)
        """
        if not self.llm_service:
            return CommandResult(
                success=False, message="LLM service not available", display_type="error"
            )

        assert self.conversation_manager is not None
        raw_messages = self.conversation_manager.messages
        self.logger.info(f"[SESSION] raw_messages count: {len(raw_messages)}")

        from kollabor_events.data_models import ConversationMessage

        loaded_messages = []
        messages_for_display = [{"role": "system", "content": header}]

        for msg in raw_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            loaded_messages.append(ConversationMessage(role=role, content=content))

            if role in ("user", "assistant"):
                messages_for_display.append({"role": role, "content": content})

        # In-place replace preserves list identity; see
        # _replace_conversation_history docstring for the bug history.
        self._replace_conversation_history(loaded_messages)

        if hasattr(self.llm_service, "session_stats"):
            self.llm_service.session_stats["messages"] = len(loaded_messages)

        # Add success message
        messages_for_display.append({"role": "system", "content": success_msg})

        # Use ADD_MESSAGE event for unified display with loading
        from kollabor_events.models import EventType

        assert self.event_bus is not None
        await self.event_bus.emit_with_hooks(
            EventType.ADD_MESSAGE,
            {
                "messages": messages_for_display,
                "options": {
                    "show_loading": True,
                    "loading_message": "Loading conversation...",
                    "log_messages": False,  # Already logged
                    "add_to_history": False,  # Already loaded above
                    "display_messages": True,
                },
            },
            "resume_plugin",
        )

        return CommandResult(success=True, message="", display_type="success")

    async def _load_conversation(
        self, session_id: str, force: bool = False
    ) -> CommandResult:
        """Load specific conversation by session ID into a new session.

        Phase 4.5 step 7: routes entirely through state_service. The
        daemon owns conversation_manager and llm_service.conversation_history;
        this handler is thin -- it calls state.resume_conversation and
        renders the returned metadata. Works identically in local and
        attach mode because state_service has the same interface on
        both sides.
        """
        # Route through the shared daemon-resume path (also used by the
        # conversations browsers) so there is one source of truth for
        # "resume this session + render it".
        from kollabor.llm.session_resume import resume_session_id

        outcome = await resume_session_id(self.event_bus, self.renderer, session_id)
        if not outcome.success:
            return CommandResult(
                success=False,
                message=outcome.error or "Failed to resume session",
                display_type="error",
            )
        return CommandResult(success=True, message="", display_type="success")

    async def _handle_resume_options(self, args: List[str]) -> CommandResult:
        """Handle additional resume options and filters.

        Args:
            args: Command arguments

        Returns:
            Command result.
        """
        # Parse filters like --today, --week, --limit N
        filters: Dict[str, Any] = {}
        limit = 20

        i = 0
        while i < len(args):
            arg = args[i]

            if arg == "--today":
                from datetime import date

                filters["date"] = date.today().isoformat()
            elif arg == "--week":
                from datetime import date, timedelta

                filters["date_range"] = (
                    (date.today() - timedelta(days=7)).isoformat(),
                    date.today().isoformat(),
                )
            elif arg == "--limit" and i + 1 < len(args):
                try:
                    limit = int(args[i + 1])
                    i += 1
                except ValueError:
                    pass
            elif arg.startswith("--"):
                return CommandResult(
                    success=False,
                    message=f"Unknown option: {arg}",
                    display_type="error",
                )

            i += 1

        # Apply filters
        conversations = await self.discover_conversations(limit)

        # Filter conversations based on criteria
        filtered_conversations = []
        for conv in conversations:
            include = True

            if "date" in filters:
                if (
                    conv.created_time
                    and conv.created_time.date().isoformat() != filters["date"]
                ):
                    include = False

            if "date_range" in filters:
                start_date, end_date = filters["date_range"]
                if conv.created_time:
                    conv_date = conv.created_time.date().isoformat()
                    if not (start_date <= conv_date <= end_date):
                        include = False

            if include:
                filtered_conversations.append(conv)

        if not filtered_conversations:
            return CommandResult(
                success=False,
                message="No conversations found matching the specified criteria",
                display_type="info",
            )

        # Build filtered modal
        modal_definition = self._build_filtered_modal(filtered_conversations, filters)

        return CommandResult(
            success=True,
            message=f"Showing {len(filtered_conversations)} filtered conversations",
            ui_config=UIConfig(
                type="modal",
                title=modal_definition.get("title", "Conversations"),
                width=modal_definition.get("width"),
                height=(
                    int(modal_definition["height"])
                    if modal_definition.get("height")
                    else None
                ),
                modal_config=modal_definition,
            ),
            display_type="modal",
        )

    def _build_conversation_modal(
        self, conversations: List[ConversationMetadata]
    ) -> Dict[str, Any]:
        """Build conversation selection modal definition.

        Args:
            conversations: List of conversations

        Returns:
            Modal definition dictionary.
        """
        preview_length = (
            self.config.get("plugins.resume_conversation.preview_length", 80)
            if self.config
            else 80
        )
        session_items = []
        for conv in conversations:
            # Format time for display
            time_str = "Unknown"
            if conv.created_time:
                time_str = conv.created_time.strftime("%m/%d %H:%M")

            # Create preview
            preview = conv.last_message_preview[:preview_length]
            if len(conv.last_message_preview) > preview_length:
                preview += "..."

            # Get user's actual request (skip system prompts and preambles)
            user_request = ""
            skip_patterns = [
                "kollabor system prompt",
                "system prompt",
                "you are",
                "assistant",
                "as an ai",
            ]
            if conv.preview_messages:
                for msg in conv.preview_messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    # Skip system messages
                    if role == "system":
                        continue
                    if role == "user" and content:
                        # Skip if content looks like a system prompt preamble
                        content_lower = content.lower()[:50]
                        if any(pattern in content_lower for pattern in skip_patterns):
                            continue
                        user_request = content
                        break
                    if role == "assistant" and content and not user_request:
                        # Use assistant response if no user message found
                        user_request = f"[AI] {content}"
                        break

            # Extract meaningful first line
            first_line = ""
            if user_request:
                # Get first non-empty line
                for line in user_request.split("\n"):
                    line = line.strip()
                    if line and len(line) > 3:
                        first_line = line[:60]
                        if len(line) > 60:
                            first_line += "..."
                        break
            first_line = first_line or "No preview"

            # Extract session name from session_id (e.g., "2601241708-wolf-blade" -> "wolf-blade")
            session_name = ""
            if conv.session_id and "-" in conv.session_id:
                parts = conv.session_id.split("-", 1)
                if len(parts) > 1:
                    session_name = parts[1]

            # Cleaner subtitle format with session name
            subtitle_parts = []
            if session_name:
                subtitle_parts.append(session_name)
            subtitle_parts.append(f"{conv.message_count} msgs")
            if conv.duration and conv.duration != "?":
                subtitle_parts.append(conv.duration)
            if conv.git_branch and conv.git_branch not in ["-", "unknown"]:
                subtitle_parts.append(conv.git_branch)

            session_items.append(
                {
                    "id": conv.session_id,
                    "title": f"[{time_str}] {first_line}",
                    "subtitle": " | ".join(subtitle_parts),
                    "preview": preview,
                    "action": "resume_session",
                    "exit_mode": "minimal",  # Plugin will display content after modal exit
                    "metadata": {
                        "session_id": conv.session_id,
                        "file_id": conv.file_id,
                        "working_directory": conv.working_directory,
                        "git_branch": conv.git_branch,
                        "topics": conv.topics,
                    },
                }
            )

        return {
            "title": "Resume Conversation",
            "footer": "↑↓ navigate • Enter select • Tab search • F filter • Esc exit",
            # Width is dynamic (terminal_width - 2)
            "height": 20,
            "sections": [
                {
                    "title": f"Recent Conversations ({len(conversations)} available)",
                    "type": "session_list",
                    "sessions": session_items,
                }
            ],
            "actions": [
                {"key": "Enter", "label": "Resume", "action": "select"},
                {"key": "Tab", "label": "Search", "action": "search"},
                {"key": "F", "label": "Filter", "action": "filter"},
                {"key": "Escape", "label": "Cancel", "action": "cancel"},
            ],
        }

    def _build_search_modal(
        self, conversations: List[ConversationMetadata], query: str
    ) -> Dict[str, Any]:
        """Build search results modal definition.

        Args:
            conversations: Search results
            query: Search query

        Returns:
            Modal definition dictionary.
        """
        session_items = []
        for conv in conversations:
            # Format time for display
            time_str = "Unknown"
            if conv.created_time:
                time_str = conv.created_time.strftime("%m/%d %H:%M")

            # Get user's actual request - skip system prompts and preambles
            user_request = ""
            skip_patterns = [
                "kollabor",
                "system prompt",
                "you are",
                "assistant",
                "as an ai",
            ]
            if conv.preview_messages:
                for msg in conv.preview_messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role == "system" or not content:
                        continue
                    content_lower = content.lower()[:50]
                    if any(p in content_lower for p in skip_patterns):
                        continue
                    if role == "user" and content:
                        user_request = content
                        break

            # Extract meaningful first line
            first_line = ""
            if user_request:
                for line in user_request.split("\n"):
                    line = line.strip()
                    if line and len(line) > 3:
                        first_line = line[:50] + ("..." if len(line) > 50 else "")
                        break
            first_line = first_line or "No preview"

            # Extract session name
            session_name = ""
            if conv.session_id and "-" in conv.session_id:
                parts = conv.session_id.split("-", 1)
                if len(parts) > 1:
                    session_name = parts[1]

            # Relevance score if available
            relevance_text = (
                f" [{conv.search_relevance:.0%}]" if conv.search_relevance else ""
            )

            # Clean subtitle with session name
            subtitle_parts = []
            if session_name:
                subtitle_parts.append(session_name)
            subtitle_parts.append(f"{conv.message_count} msgs")
            if conv.duration and conv.duration != "?":
                subtitle_parts.append(conv.duration)
            if conv.git_branch and conv.git_branch not in ["-", "unknown"]:
                subtitle_parts.append(conv.git_branch)

            session_items.append(
                {
                    "id": conv.session_id,
                    "title": f"[{time_str}] {first_line}{relevance_text}",
                    "subtitle": " | ".join(subtitle_parts),
                    "preview": conv.last_message_preview[:80],
                    "metadata": {
                        "session_id": conv.session_id,
                        "file_id": conv.file_id,
                        "search_relevance": conv.search_relevance,
                    },
                }
            )

        return {
            "title": f"Search Results: {query}",
            "footer": "↑↓ navigate • Enter select • Esc back",
            # Width is dynamic (terminal_width - 2)
            "height": 20,
            "sections": [
                {
                    "title": f"Found {len(conversations)} conversations",
                    "type": "session_list",
                    "sessions": session_items,
                }
            ],
            "actions": [
                {"key": "Enter", "label": "Resume", "action": "select"},
                {"key": "Escape", "label": "Back", "action": "cancel"},
            ],
        }

    def _build_filtered_modal(
        self, conversations: List[ConversationMetadata], filters: Dict
    ) -> Dict[str, Any]:
        """Build filtered results modal definition.

        Args:
            conversations: Filtered conversations
            filters: Applied filters

        Returns:
            Modal definition dictionary.
        """
        filter_desc = []
        if "date" in filters:
            filter_desc.append(f"Date: {filters['date']}")
        if "date_range" in filters:
            start, end = filters["date_range"]
            filter_desc.append(f"Date: {start} to {end}")

        filter_text = " • ".join(filter_desc) if filter_desc else "Filtered"

        session_items = []
        for conv in conversations:
            # Format time for display
            time_str = "Unknown"
            if conv.created_time:
                time_str = conv.created_time.strftime("%m/%d %H:%M")

            # Get user's actual request - skip system prompts and preambles
            user_request = ""
            skip_patterns = [
                "kollabor",
                "system prompt",
                "you are",
                "assistant",
                "as an ai",
            ]
            if conv.preview_messages:
                for msg in conv.preview_messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role == "system" or not content:
                        continue
                    content_lower = content.lower()[:50]
                    if any(p in content_lower for p in skip_patterns):
                        continue
                    if role == "user" and content:
                        user_request = content
                        break

            # Extract meaningful first line
            first_line = ""
            if user_request:
                for line in user_request.split("\n"):
                    line = line.strip()
                    if line and len(line) > 3:
                        first_line = line[:60] + ("..." if len(line) > 60 else "")
                        break
            first_line = first_line or "No preview"

            # Extract session name
            session_name = ""
            if conv.session_id and "-" in conv.session_id:
                parts = conv.session_id.split("-", 1)
                if len(parts) > 1:
                    session_name = parts[1]

            # Clean subtitle with session name
            subtitle_parts = []
            if session_name:
                subtitle_parts.append(session_name)
            subtitle_parts.append(f"{conv.message_count} msgs")
            if conv.duration and conv.duration != "?":
                subtitle_parts.append(conv.duration)
            if conv.git_branch and conv.git_branch not in ["-", "unknown"]:
                subtitle_parts.append(conv.git_branch)

            session_items.append(
                {
                    "id": conv.session_id,
                    "title": f"[{time_str}] {first_line}",
                    "subtitle": " | ".join(subtitle_parts),
                    "preview": conv.last_message_preview[:80],
                    "metadata": {
                        "session_id": conv.session_id,
                        "file_id": conv.file_id,
                    },
                }
            )

        return {
            "title": f"Filtered Conversations ({filter_text})",
            "footer": "↑↓ navigate • Enter select • Esc back",
            # Width is dynamic (terminal_width - 2)
            "height": 20,
            "sections": [
                {
                    "title": f"Showing {len(conversations)} conversations",
                    "type": "session_list",
                    "sessions": session_items,
                }
            ],
            "actions": [
                {"key": "Enter", "label": "Resume", "action": "select"},
                {"key": "Escape", "label": "Back", "action": "cancel"},
            ],
        }

    def _generate_session_title(self, session_data: Dict) -> str:
        """Generate a descriptive title for a session.

        Args:
            session_data: Session data

        Returns:
            Generated title
        """
        topics = session_data.get("topics", [])
        working_dir = session_data.get("working_directory", "unknown")

        # Extract project name from working directory
        project_name = (
            working_dir.split("/")[-1]
            if working_dir != "unknown"
            else "Unknown Project"
        )

        # Use topic if available, otherwise use generic title
        if topics:
            main_topic = topics[0].replace("_", " ").title()
            return f"{main_topic} - {project_name}"
        else:
            return f"Conversation - {project_name}"

    def _generate_file_id(self, session_id: str) -> str:
        """Generate short file ID for display.

        Args:
            session_id: Full session ID

        Returns:
            Short file ID
        """
        # Simple hash to generate a consistent short ID
        hash_val = hash(session_id) % 100000
        return f"#{hash_val:05d}"

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string.

        Args:
            dt_str: Datetime string

        Returns:
            Parsed datetime or None
        """
        if not dt_str:
            return None

        try:
            from datetime import datetime

            # Handle ISO format with Z
            if dt_str.endswith("Z"):
                dt_str = dt_str.replace("Z", "+00:00")
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None

    async def shutdown(self) -> None:
        """Shutdown the plugin and cleanup resources."""
        try:
            self.logger.info("Resume conversation plugin shutdown completed")
        except Exception as e:
            self.logger.error(f"Error shutting down resume conversation plugin: {e}")

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Get default configuration for resume conversation plugin.

        Returns:
            Default configuration dictionary.
        """
        return {
            "plugins": {
                "resume_conversation": {
                    "enabled": True,
                    "max_conversations": 50,
                    "preview_length": 80,
                    "auto_save_current": True,
                    "confirm_load": True,
                    "session_retention_days": 30,
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        """Get configuration widgets for this plugin.

        Returns:
            Widget section definition for the config modal.
        """
        return {
            "title": "Resume Conversation",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.resume_conversation.enabled",
                },
                {
                    "type": "slider",
                    "label": "Maximum conversations to show",
                    "config_path": "plugins.resume_conversation.max_conversations",
                    "min_value": 10,
                    "max_value": 200,
                    "step": 10,
                },
                {
                    "type": "slider",
                    "label": "Preview text length in characters",
                    "config_path": "plugins.resume_conversation.preview_length",
                    "min_value": 20,
                    "max_value": 200,
                    "step": 10,
                },
                {
                    "type": "checkbox",
                    "label": "Auto-save Current",
                    "config_path": "plugins.resume_conversation.auto_save_current",
                    "help": "Automatically save current conversation before loading another",
                },
                {
                    "type": "checkbox",
                    "label": "Confirm Load",
                    "config_path": "plugins.resume_conversation.confirm_load",
                    "help": "Ask for confirmation before loading a conversation",
                },
                {
                    "type": "slider",
                    "label": "Days to retain conversation sessions",
                    "config_path": "plugins.resume_conversation.session_retention_days",
                    "min_value": 1,
                    "max_value": 365,
                    "step": 1,
                },
            ],
        }

    async def register_hooks(self) -> None:
        """Register event hooks for the plugin."""
        from kollabor_events.models import EventType, Hook

        hook = Hook(
            name="resume_modal_command",
            plugin_name="resume_conversation",
            event_type=EventType.MODAL_COMMAND_SELECTED,
            priority=10,
            callback=self._handle_modal_command,
        )
        assert self.event_bus is not None
        await self.event_bus.register_hook(hook)

    async def _handle_modal_command(
        self, data: Dict[str, Any], event: Event
    ) -> Dict[str, Any]:
        """Handle modal command selection events.

        Args:
            data: Event data containing command info.
            event: Event object.

        Returns:
            Modified data dict with display_messages or show_modal keys.
        """
        command = data.get("command", {})
        action = command.get("action")

        if action == "resume_session":
            session_id = command.get("session_id") or command.get("metadata", {}).get(
                "session_id"
            )
            if session_id:
                self.logger.info(f"[RESUME] Modal selected session: {session_id}")

                if (
                    self._ensure_conversation_manager()
                    and self.conversation_manager is not None
                    and self.conversation_manager.load_session(session_id)
                ):
                    # Load messages into llm_service history
                    display_messages = self._prepare_session_display(
                        header=f"--- Resumed session: {session_id} ---",
                        success_msg=f"[ok] Resumed: {session_id}. Continue below.",
                    )

                    if display_messages:
                        # Return messages for modal_controller to display AFTER modal exits
                        # This avoids the race condition where ADD_MESSAGE displays to
                        # the alternate buffer before the modal closes
                        data["display_messages"] = [
                            (role, content, meta)
                            for role, content, meta in display_messages
                        ]

        elif action == "branch_select_session":
            session_id = command.get("session_id") or command.get("metadata", {}).get(
                "session_id"
            )
            if session_id:
                self.logger.info(
                    f"[BRANCH] Modal selected session for branch: {session_id}"
                )
                result = await self._show_branch_point_selector(session_id)
                if result and result.ui_config and result.display_type == "modal":
                    data["show_modal"] = result.ui_config.modal_config

        elif action == "search":
            # Show search options modal
            self.logger.info("[RESUME] Search action triggered")
            data["show_modal"] = {
                "title": "Search Conversations",
                "footer": "Enter select • Esc back",
                # Width is dynamic (terminal_width - 2)
                "height": 12,
                "sections": [
                    {
                        "title": "Search by command",
                        "commands": [
                            {
                                "name": "/resume search <query>",
                                "description": "Search conversation content",
                            },
                            {
                                "name": "/resume search git",
                                "description": "Example: find git-related chats",
                            },
                            {
                                "name": "/resume search modal",
                                "description": "Example: find modal discussions",
                            },
                        ],
                    }
                ],
            }

        elif action == "filter":
            # Show filter options modal
            self.logger.info("[RESUME] Filter action triggered")
            data["show_modal"] = {
                "title": "Filter Conversations",
                "footer": "Enter select • Esc back",
                # Width is dynamic (terminal_width - 2)
                "height": 14,
                "sections": [
                    {
                        "title": "Filter options",
                        "commands": [
                            {
                                "name": "Today's conversations",
                                "description": "Show only today",
                                "action": "filter_today",
                            },
                            {
                                "name": "This week",
                                "description": "Show this week's conversations",
                                "action": "filter_week",
                            },
                            {
                                "name": "Show more",
                                "description": "Show up to 50 conversations",
                                "action": "filter_limit",
                            },
                        ],
                    }
                ],
            }

        elif action == "filter_today":
            self.logger.info("[RESUME] Filter today triggered")
            result = await self._handle_resume_options(["--today"])
            if result and result.ui_config and result.display_type == "modal":
                data["show_modal"] = result.ui_config.modal_config

        elif action == "filter_week":
            self.logger.info("[RESUME] Filter week triggered")
            result = await self._handle_resume_options(["--week"])
            if result and result.ui_config and result.display_type == "modal":
                data["show_modal"] = result.ui_config.modal_config

        elif action == "filter_limit":
            self.logger.info("[RESUME] Filter limit triggered")
            result = await self._handle_resume_options(["--limit", "50"])
            if result and result.ui_config and result.display_type == "modal":
                data["show_modal"] = result.ui_config.modal_config

        elif action == "branch_execute":
            metadata = command.get("metadata", {})
            session_id = metadata.get("session_id")
            message_index = metadata.get("message_index")
            if session_id is not None and message_index is not None:
                self.logger.info(
                    f"[BRANCH] Executing branch: {session_id} at {message_index}"
                )
                if (
                    self._ensure_conversation_manager()
                    and self.conversation_manager is not None
                ):
                    result = self.conversation_manager.branch_session(
                        session_id, message_index
                    )
                    if result.get("success"):
                        new_session_id = result.get("session_id")
                        display_messages = self._prepare_session_display(
                            header=f"--- Branched from {session_id} at message {message_index} ---",
                            success_msg=(
                                f"[ok] Created branch: {new_session_id}\n"
                                f"    From: {session_id} at message {result['branch_point']}\n"
                                f"    Loaded {result['message_count']} messages. Continue below."
                            ),
                        )
                        if display_messages:
                            # Return messages for modal_controller to display AFTER modal exits
                            # This avoids the race condition where ADD_MESSAGE displays to
                            # the alternate buffer before the modal closes
                            data["display_messages"] = [
                                (role, content, meta)
                                for role, content, meta in display_messages
                            ]

        return data
