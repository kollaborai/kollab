"""Tests for resume command functionality."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor_ai import ConversationManager, KollaborConversationLogger
from kollabor_events.models import SlashCommand


class TestResumeCommand:
    """Test suite for resume command functionality."""

    @pytest.fixture
    def temp_conversations_dir(self):
        """Create temporary conversations directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)

    @pytest.fixture
    def mock_conversation_logger(self, temp_conversations_dir):
        """Create mock conversation logger with test data."""
        # Create test session files first
        self._create_test_session_files(temp_conversations_dir)

        # Create logger with the directory containing test files
        logger = KollaborConversationLogger(temp_conversations_dir / "conversations")

        return logger

    @pytest.fixture
    def mock_conversation_manager(self, temp_conversations_dir):
        """Create mock conversation manager."""
        conversations_dir = temp_conversations_dir / "conversations"
        conversations_dir.mkdir(parents=True, exist_ok=True)

        class MockConfig:
            _conversations_dir = conversations_dir

            def get(self, key, default=None):
                return default

        config = MockConfig()
        manager = ConversationManager(config)
        return manager

    def _create_test_session_files(self, conversations_dir):
        """Create test session files for testing."""
        # Create conversations subdirectory
        conv_dir = conversations_dir / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)

        # Test session 1 - JSONL format for conversation_logger
        session1_data = [
            {
                "type": "conversation_metadata",
                "sessionId": "session_2025-12-10_200000",
                "startTime": "2025-12-10T20:00:00Z",
                "cwd": "/home/user/dev/test_project",
                "gitBranch": "main",
                "uuid": "test-uuid-1",
            },
            {
                "type": "user",
                "message": {"role": "user", "content": "Help me debug this issue"},
                "uuid": "test-uuid-2",
                "timestamp": "2025-12-10T20:01:00Z",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": "I'll help you debug the issue",
                },
                "uuid": "test-uuid-3",
                "timestamp": "2025-12-10T20:01:30Z",
            },
        ]

        # Test session 2 - JSONL format for conversation_logger
        session2_data = [
            {
                "type": "conversation_metadata",
                "sessionId": "session_2025-12-09_150000",
                "startTime": "2025-12-09T15:00:00Z",
                "cwd": "/home/user/dev/another_project",
                "gitBranch": "feature-branch",
                "uuid": "test-uuid-4",
            },
            {
                "type": "user",
                "message": {"role": "user", "content": "Implement new feature"},
                "uuid": "test-uuid-5",
                "timestamp": "2025-12-09T15:01:00Z",
            },
        ]

        # Write session files to conversations subdirectory (JSONL format)
        session1_file = conv_dir / "session_2025-12-10_200000.jsonl"
        session2_file = conv_dir / "session_2025-12-09_150000.jsonl"

        with open(session1_file, "w") as f:
            for line in session1_data:
                f.write(json.dumps(line) + "\n")

        with open(session2_file, "w") as f:
            for line in session2_data:
                f.write(json.dumps(line) + "\n")

    def test_conversation_manager_save_session(self, mock_conversation_manager):
        """Test conversation manager save session functionality."""
        # Add some test messages
        mock_conversation_manager.add_message("user", "Test message")
        mock_conversation_manager.add_message("assistant", "Test response")

        # Save session
        result = mock_conversation_manager.save_session("test_session")

        assert result is True

        # Check if session file was created ({session_id}.jsonl)
        session_file = (
            mock_conversation_manager.conversations_dir / "test_session.jsonl"
        )
        assert session_file.exists()

        # Verify session file contents (single-line JSON)
        with open(session_file, "r") as f:
            session_data = json.loads(f.readline())

        assert session_data["session_id"] == "test_session"
        assert len(session_data["messages"]) == 2
        assert "metadata" in session_data
        assert session_data["messages"][0]["content"] == "Test message"
        assert session_data["messages"][1]["content"] == "Test response"

    def test_conversation_manager_load_session(self, mock_conversation_manager):
        """Test conversation manager load session functionality."""
        # First save a session
        mock_conversation_manager.add_message("user", "Original message")
        mock_conversation_manager.save_session("load_test")

        # Clear current state
        mock_conversation_manager.clear_conversation()

        # Load the session
        result = mock_conversation_manager.load_session("load_test")

        assert result is True
        assert len(mock_conversation_manager.messages) == 1
        assert mock_conversation_manager.messages[0]["content"] == "Original message"
        assert mock_conversation_manager.current_session_id == "load_test"

    def test_conversation_manager_validate_session(self, mock_conversation_manager):
        """Test conversation manager session validation."""
        # Save a session first
        mock_conversation_manager.add_message("user", "Test message")
        mock_conversation_manager.save_session("validate_test")

        # Validate the session
        validation = mock_conversation_manager.validate_session("validate_test")

        assert validation["valid"] is True
        assert len(validation["issues"]) == 0
        assert validation["compatibility_score"] >= 0.0

    def test_conversation_logger_list_sessions(self, mock_conversation_logger):
        """Test conversation logger session listing."""
        sessions = mock_conversation_logger.list_sessions()

        # Should find our 2 test sessions
        assert len(sessions) >= 2

        # Check session structure
        for session in sessions:
            assert "session_id" in session
            assert "file_path" in session
            assert "message_count" in session
            assert "working_directory" in session

    def test_conversation_logger_search_sessions(self, mock_conversation_logger):
        """Test conversation logger session search."""
        # Search for "debug" (should match first session)
        results = mock_conversation_logger.search_sessions("debug")

        assert len(results) >= 1

        # Check that results contain search relevance
        for result in results:
            assert "search_relevance" in result

    def test_conversation_logger_get_session_summary(self, mock_conversation_logger):
        """Test conversation logger session summary."""
        # Get summary for first session
        summary = mock_conversation_logger.get_session_summary(
            "session_2025-12-10_200000"
        )

        assert "metadata" in summary
        assert "key_topics" in summary
        assert "project_context" in summary
        assert "compatibility_score" in summary

    @pytest.mark.asyncio
    async def test_resume_plugin_initialization(self):
        """Test resume conversation plugin initialization."""
        from plugins.resume_conversation_plugin import ResumeConversationPlugin

        plugin = ResumeConversationPlugin()

        # Mock dependencies
        event_bus = Mock()
        config = Mock()
        command_registry = Mock()
        conversation_manager = Mock()
        conversation_logger = Mock()

        # Initialize plugin
        await plugin.initialize(
            event_bus=event_bus,
            config=config,
            command_registry=command_registry,
            conversation_manager=conversation_manager,
            conversation_logger=conversation_logger,
        )

        # Check that dependencies were set
        assert plugin.event_bus == event_bus
        assert plugin.config == config
        assert plugin.conversation_manager == conversation_manager
        assert plugin.conversation_logger == conversation_logger

        # Check that commands were registered
        assert (
            command_registry.register_command.call_count >= 2
        )  # resume and sessions commands

    @pytest.mark.asyncio
    async def test_resume_plugin_handle_resume(self):
        """Test resume plugin handle_resume method."""
        from plugins.resume_conversation_plugin import ResumeConversationPlugin

        plugin = ResumeConversationPlugin()

        # Mock dependencies
        plugin.conversation_manager = Mock()
        plugin.conversation_logger = Mock()

        # Test with no args (should show menu)
        plugin.discover_conversations = AsyncMock(return_value=[])

        command = SlashCommand(name="resume", args=[], raw_input="/resume")
        result = await plugin.handle_resume(command)

        assert result.success is False  # No conversations found
        assert "No saved conversations found" in result.message

    def test_session_metadata_dataclass(self):
        """Test SessionMetadata dataclass."""
        from kollabor_events.data_models import SessionMetadata

        metadata = SessionMetadata(
            session_id="test_session",
            start_time=datetime.now(),
            end_time=None,
            message_count=5,
            turn_count=3,
            working_directory="/test/dir",
            git_branch="main",
            themes=["debugging", "feature"],
            files_mentioned=["test.py"],
            last_activity=datetime.now(),
            size_bytes=1024,
            is_valid=True,
            validation_issues=[],
        )

        assert metadata.session_id == "test_session"
        assert metadata.message_count == 5
        assert metadata.is_valid is True
        assert len(metadata.validation_issues) == 0

    def test_conversation_metadata_dataclass(self):
        """Test ConversationMetadata dataclass."""
        from kollabor_events.data_models import ConversationMetadata

        metadata = ConversationMetadata(
            file_path="/test/session.jsonl",
            session_id="test_session",
            title="Test Conversation",
            message_count=5,
            created_time=datetime.now(),
            modified_time=None,
            last_message_preview="Test preview...",
            topics=["debugging"],
            file_id="#12345",
            working_directory="/test/dir",
            git_branch="main",
            duration="10m",
            size_bytes=1024,
            preview_messages=[],
        )

        assert metadata.session_id == "test_session"
        assert metadata.file_id == "#12345"
        assert metadata.duration == "10m"
        assert len(metadata.topics) == 1
