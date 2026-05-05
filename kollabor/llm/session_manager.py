"""Session management for Kollab LLM service.

Handles conversation initialization, session restart, and
conversation context setup. Extracted from LLMService as part of
the llm_service.py decomposition (Phase C).
"""

import logging
from typing import Callable, List, Optional

from kollabor_events.data_models import ConversationMessage

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation session lifecycle.

    Responsibilities:
    - Initialize conversation with system prompt
    - Restart sessions (save current, start fresh)
    - Set dynamic conversation context (version, plugins, provider)
    """

    def __init__(
        self,
        conversation_logger,
        conversation_manager,
        config,
        event_bus,
        api_service,
        prompt_builder,
    ):
        """Initialize the session manager.

        Args:
            conversation_logger: KollaborConversationLogger for persistence
            conversation_manager: ConversationManager for message tracking
            config: ConfigService for reading settings
            event_bus: EventBus for hook registry access
            api_service: APICommunicationService for session ID management
            prompt_builder: SystemPromptBuilder for building system prompts
        """
        self.conversation_logger = conversation_logger
        self.conversation_manager = conversation_manager
        self.config = config
        self.event_bus = event_bus
        self.api_service = api_service
        self.prompt_builder = prompt_builder

    async def initialize_conversation(
        self,
        conversation_history: List[ConversationMessage],
        add_message_fn: Callable,
    ) -> Optional[str]:
        """Initialize conversation with project context.

        Args:
            conversation_history: The conversation history list (modified in place)
            add_message_fn: Callback to add messages to conversation

        Returns:
            The parent UUID from logging the initial message, or None on error
        """
        try:
            # Clear any existing history
            conversation_history.clear()

            # Build system prompt synchronously. Running in asyncio.to_thread
            # causes SIGTTIN (suspended tty input) regardless of subprocess
            # isolation (stdin=DEVNULL, start_new_session). The event loop
            # blocks for ~2s during subprocess execution, but the input handler
            # thread still captures keystrokes which render after completion.
            initial_message = self.prompt_builder.build()

            add_message_fn(ConversationMessage(role="system", content=initial_message))

            # Log initial context message
            parent_uuid = await self.conversation_logger.log_user_message(
                initial_message,
                user_context={
                    "type": "system_initialization",
                    "project_context_loaded": True,
                },
            )

            logger.info("Conversation initialized with project context")
            return parent_uuid  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(f"Failed to initialize conversation: {e}")
            return None

    async def restart_session(
        self,
        conversation_history: List[ConversationMessage],
        add_message_fn: Callable,
    ) -> dict:
        """Restart the conversation session - save current, start fresh.

        This properly resets all session state:
        - Logs conversation end
        - Saves current conversation
        - Generates new session_id shared across all components
        - Clears conversation history
        - Rebuilds system prompt
        - Logs conversation start

        Args:
            conversation_history: The conversation history list (modified in place)
            add_message_fn: Callback to add messages to conversation

        Returns:
            Dict with old_session_id, new_session_id, messages_cleared
        """
        from kollabor_ai import generate_session_name

        old_session_id = self.conversation_logger.session_id
        old_message_count = max(0, len(conversation_history) - 1)  # Exclude system msg

        try:
            # 1. Log end of current conversation
            await self.conversation_logger.log_conversation_end()

            # 2. Save current conversation via conversation_manager (if saving enabled)
            if (
                self.conversation_manager.messages
                and self.conversation_manager.save_conversations
            ):
                self.conversation_manager.save_conversation()

            # 3. Generate new session_id
            new_session_id = generate_session_name()

            # 4. Reset conversation_logger for new session
            self.conversation_logger.reset_session(new_session_id)

            # 5. Reset conversation_manager for new session
            self.conversation_manager.reset_session(new_session_id)

            # 6. Update api_service session
            self.api_service.set_session_id(new_session_id)

            # 7. Clear and reinitialize conversation history
            conversation_history.clear()
            initial_message = self.prompt_builder.build()
            add_message_fn(ConversationMessage(role="system", content=initial_message))

            # 8. Log start of new conversation
            await self.conversation_logger.log_conversation_start()

            logger.info(f"Session restarted: {old_session_id} -> {new_session_id}")

            return {
                "old_session_id": old_session_id,
                "new_session_id": new_session_id,
                "messages_cleared": old_message_count,
            }

        except Exception as e:
            logger.error(f"Failed to restart session: {e}")
            raise

    def set_conversation_context(self):
        """Set dynamic context on conversation logger before logging start."""
        # Get version
        try:
            from importlib.metadata import version

            app_version = version("kollab")
        except Exception:
            try:
                from pathlib import Path

                pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
                if pyproject.exists():
                    for line in pyproject.read_text().split("\n"):
                        if line.startswith("version ="):
                            app_version = line.split('"')[1]
                            break
                    else:
                        app_version = "unknown"
                else:
                    app_version = "unknown"
            except Exception:
                app_version = "unknown"

        # Get active plugins from event bus
        active_plugins = []
        if self.event_bus and hasattr(self.event_bus, "registry"):
            try:
                hooks = self.event_bus.registry.get_all_hooks()
                plugin_names = set()
                for hook_list in hooks.values():
                    for hook in hook_list:
                        if hasattr(hook, "__self__"):
                            plugin_names.add(type(hook.__self__).__name__)
                active_plugins = sorted(plugin_names)
            except Exception as e:
                logger.debug(f"Failed to extract plugin names: {e}")

        self.conversation_logger.set_context(app_version, active_plugins)
        self.conversation_logger.set_provider(self.api_service._profile.provider)
