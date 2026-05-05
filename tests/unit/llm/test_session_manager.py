"""Tests for SessionManager."""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor.llm.session_manager import SessionManager
from kollabor_events.data_models import ConversationMessage


class TestSessionManager(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.conversation_logger = AsyncMock()
        self.conversation_logger.session_id = "test-session"
        self.conversation_logger.conversations_dir = MagicMock()
        self.conversation_logger.conversations_dir.__truediv__ = lambda s, o: Path(
            "/conversations"
        )
        self.conversation_logger.message_count = 5
        self.conversation_logger.conversation_start_time = MagicMock()
        self.conversation_logger.current_thread_uuid = "thread-uuid"

        self.conversation_manager = MagicMock()
        self.conversation_manager.messages = ["msg1", "msg2"]
        self.conversation_manager.save_conversations = True
        self.conversation_manager.save_conversation = MagicMock()
        self.conversation_manager.current_session_id = "test-session"
        self.conversation_manager.message_index = {}
        self.conversation_manager.context_window = []
        self.conversation_manager.current_parent_uuid = "parent-uuid"
        self.conversation_manager.conversation_metadata = {}

        self.config = MagicMock()
        self.config.get = MagicMock(return_value=False)

        self.event_bus = MagicMock()
        self.event_bus.registry = MagicMock()
        self.event_bus.registry.get_all_hooks = MagicMock(return_value={})

        self.api_service = MagicMock()
        self.api_service.set_session_id = MagicMock()
        self.api_service._profile = MagicMock()
        self.api_service._profile.provider = "test-provider"

        self.prompt_builder = MagicMock()
        self.prompt_builder.build = MagicMock(return_value="system prompt content")

        self.manager = SessionManager(
            conversation_logger=self.conversation_logger,
            conversation_manager=self.conversation_manager,
            config=self.config,
            event_bus=self.event_bus,
            api_service=self.api_service,
            prompt_builder=self.prompt_builder,
        )

    def tearDown(self):
        """Clean up."""
        self.loop.close()

    def test_init(self):
        """Test SessionManager initialization."""
        self.assertIsNotNone(self.manager)
        self.assertEqual(self.manager.conversation_logger, self.conversation_logger)
        self.assertEqual(self.manager.prompt_builder, self.prompt_builder)

    def test_initialize_conversation(self):
        """Test initializing conversation."""
        conversation_history = []
        add_message_fn = MagicMock()

        result = self.loop.run_until_complete(
            self.manager.initialize_conversation(conversation_history, add_message_fn)
        )

        self.assertIsNotNone(result)
        # add_message_fn is called with system message
        add_message_fn.assert_called_once()
        msg = add_message_fn.call_args[0][0]
        self.assertEqual(msg.role, "system")
        self.assertEqual(msg.content, "system prompt content")
        self.conversation_logger.log_user_message.assert_called_once()

    def test_initialize_conversation_clears_history(self):
        """Test initialization clears existing history."""
        conversation_history = [ConversationMessage(role="user", content="old message")]
        add_message_fn = MagicMock()

        self.loop.run_until_complete(
            self.manager.initialize_conversation(conversation_history, add_message_fn)
        )

        # History cleared, then add_message_fn called with system msg
        self.assertEqual(len(conversation_history), 0)  # clear() was called
        add_message_fn.assert_called_once()

    def test_initialize_conversation_error_handling(self):
        """Test initialization handles errors gracefully."""
        self.prompt_builder.build = MagicMock(side_effect=Exception("build error"))

        conversation_history = []
        add_message_fn = MagicMock()

        result = self.loop.run_until_complete(
            self.manager.initialize_conversation(conversation_history, add_message_fn)
        )

        self.assertIsNone(result)

    def test_restart_session(self):
        """Test restarting session."""
        conversation_history = [
            ConversationMessage(role="system", content="old prompt")
        ]
        add_message_fn = MagicMock()

        with patch("kollabor_ai.generate_session_name", return_value="new-session-id"):
            result = self.loop.run_until_complete(
                self.manager.restart_session(conversation_history, add_message_fn)
            )

        self.assertEqual(result["old_session_id"], "test-session")
        self.assertEqual(result["new_session_id"], "new-session-id")
        self.assertEqual(result["messages_cleared"], 0)  # system msg excluded

        self.conversation_logger.log_conversation_end.assert_called_once()
        self.conversation_manager.save_conversation.assert_called_once()
        self.api_service.set_session_id.assert_called_once_with("new-session-id")
        self.conversation_logger.log_conversation_start.assert_called_once()

    def test_restart_session_resets_conversation_manager(self):
        """Test restart calls reset_session on conversation manager."""
        conversation_history = [ConversationMessage(role="system", content="old")]
        add_message_fn = MagicMock()

        with patch("kollabor_ai.generate_session_name", return_value="new"):
            self.loop.run_until_complete(
                self.manager.restart_session(conversation_history, add_message_fn)
            )

        self.conversation_manager.reset_session.assert_called_once_with("new")

    def test_restart_session_resets_logger(self):
        """Test restart calls reset_session on conversation logger."""
        conversation_history = [ConversationMessage(role="system", content="old")]
        add_message_fn = MagicMock()

        new_session_id = "new-session-123"
        with patch("kollabor_ai.generate_session_name", return_value=new_session_id):
            self.loop.run_until_complete(
                self.manager.restart_session(conversation_history, add_message_fn)
            )

        self.conversation_logger.reset_session.assert_called_once_with(new_session_id)

    def test_restart_session_error(self):
        """Test restart session raises error on failure."""
        self.conversation_logger.log_conversation_end = AsyncMock(
            side_effect=Exception("log error")
        )

        conversation_history = []
        add_message_fn = MagicMock()

        with self.assertRaises(Exception):
            self.loop.run_until_complete(
                self.manager.restart_session(conversation_history, add_message_fn)
            )

    def test_set_conversation_context(self):
        """Test setting conversation context calls set_context."""
        self.conversation_logger.set_context = MagicMock()
        self.conversation_logger.set_provider = MagicMock()

        with patch("importlib.metadata.version", return_value="1.0.0"):
            self.manager.set_conversation_context()

        self.conversation_logger.set_context.assert_called_once_with("1.0.0", [])
        self.conversation_logger.set_provider.assert_called_once_with("test-provider")

    def test_set_conversation_context_with_plugins(self):
        """Test setting conversation context includes active plugins."""
        self.conversation_logger.set_context = MagicMock()
        self.conversation_logger.set_provider = MagicMock()

        mock_hook1 = MagicMock()
        mock_hook1.__self__ = MagicMock()
        type(mock_hook1.__self__).__name__ = "PluginOne"
        mock_hook2 = MagicMock()
        mock_hook2.__self__ = MagicMock()
        type(mock_hook2.__self__).__name__ = "PluginTwo"

        self.event_bus.registry.get_all_hooks = MagicMock(
            return_value={"event1": [mock_hook1], "event2": [mock_hook2]}
        )

        with patch("importlib.metadata.version", return_value="2.0.0"):
            self.manager.set_conversation_context()

        call_args = self.conversation_logger.set_context.call_args
        self.assertEqual(call_args[0][0], "2.0.0")
        self.assertIn("PluginOne", call_args[0][1])
        self.assertIn("PluginTwo", call_args[0][1])

    def test_restart_session_without_saving(self):
        """Test restart when saving is disabled."""
        self.conversation_manager.save_conversations = False

        conversation_history = [ConversationMessage(role="system", content="old")]
        add_message_fn = MagicMock()

        with patch("kollabor_ai.generate_session_name", return_value="new"):
            result = self.loop.run_until_complete(
                self.manager.restart_session(conversation_history, add_message_fn)
            )

        self.conversation_manager.save_conversation.assert_not_called()
        self.assertEqual(result["new_session_id"], "new")


if __name__ == "__main__":
    unittest.main()
