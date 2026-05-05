"""Tests for LLM Service Task Management.

Comprehensive test suite for the background task tracking and management system.
Tests cover both the BackgroundTaskManager directly and the LLMService forwarding layer.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio

from kollabor.llm.llm_coordinator import LLMService


class TestTaskManagement:
    """Test suite for background task management functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "kollabor.llm.max_history": 90,
            "kollabor.llm.background_tasks.max_concurrent": 50,
            "kollabor.llm.background_tasks.enable_monitoring": False,
            "kollabor.llm.background_tasks.cleanup_interval": 60,
            "kollabor.llm.terminal_timeout": 30,
            "kollabor.llm.mcp_timeout": 60,
            "kollabor.llm.processing_delay": 0.1,
            "kollabor.llm.thinking_delay": 0.3,
        }.get(key, default)
        return config

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        event_bus = Mock()
        event_bus.register_hook = AsyncMock()
        return event_bus

    @pytest.fixture
    def mock_renderer(self):
        """Create a mock renderer."""
        return Mock()

    @pytest_asyncio.fixture
    async def llm_service(self, mock_config, mock_event_bus, mock_renderer):
        """Create an LLM service instance for testing."""
        with (
            patch("kollabor_config.config_utils.get_conversations_dir"),
            patch("kollabor.llm.llm_coordinator.KollaborConversationLogger"),
            patch("kollabor.llm.llm_coordinator.LLMHookSystem"),
            patch("kollabor.llm.llm_coordinator.MCPIntegration"),
            patch("kollabor.llm.llm_coordinator.ResponseParser"),
            patch("kollabor.llm.llm_coordinator.ToolExecutor"),
            patch("kollabor.llm.llm_coordinator.MessageDisplayService"),
            patch("kollabor.llm.llm_coordinator.APICommunicationService"),
        ):

            service = LLMService(mock_config, mock_event_bus, mock_renderer)
            return service

    @pytest.mark.asyncio
    async def test_create_background_task_success(self, llm_service):
        """Test successful background task creation and execution."""

        async def sample_task():
            await asyncio.sleep(0.1)
            return "task_result"

        task = llm_service.create_background_task(sample_task(), name="test_task")

        # Verify task was created and tracked
        assert task is not None
        assert task.get_name() == "test_task"
        assert task in llm_service._task_manager._background_tasks
        assert "test_task" in llm_service._task_manager._task_metadata

        # Wait for task completion
        result = await task
        assert result == "task_result"

        # Verify task was cleaned up
        assert task not in llm_service._task_manager._background_tasks
        assert "test_task" not in llm_service._task_manager._task_metadata

    @pytest.mark.asyncio
    async def test_create_background_task_with_exception(self, llm_service):
        """Test background task exception handling."""

        async def failing_task():
            await asyncio.sleep(0.1)
            raise ValueError("Test error")

        task = llm_service.create_background_task(failing_task(), name="failing_task")

        # Wait for task to complete
        with pytest.raises(ValueError, match="Test error"):
            await task

        # Verify error was handled and counted
        assert llm_service._task_manager._task_error_count == 1
        assert task not in llm_service._task_manager._background_tasks

    @pytest.mark.asyncio
    async def test_create_background_task_cancellation(self, llm_service):
        """Test background task cancellation."""

        async def long_running_task():
            await asyncio.sleep(10)
            return "should_not_reach"

        task = llm_service.create_background_task(long_running_task(), name="long_task")

        # Cancel the task
        task.cancel()

        # Wait for cancellation
        with pytest.raises(asyncio.CancelledError):
            await task

        # Verify task was cleaned up
        assert task not in llm_service._task_manager._background_tasks

    @pytest.mark.asyncio
    async def test_max_concurrent_tasks_limit(self, llm_service):
        """Test maximum concurrent tasks limit."""
        # Set a low limit for testing
        llm_service._task_manager._max_concurrent_tasks = 2

        async def dummy_task():
            await asyncio.sleep(0.5)

        # Create tasks up to the limit
        llm_service.create_background_task(dummy_task(), name="task1")
        llm_service.create_background_task(dummy_task(), name="task2")

        # Third task triggers overflow strategy (drop_oldest by default)
        llm_service.create_background_task(dummy_task(), name="task3")

        # Clean up
        await llm_service.cancel_all_tasks()

    @pytest.mark.asyncio
    async def test_task_status_reporting(self, llm_service):
        """Test task status reporting functionality."""

        async def sample_task():
            await asyncio.sleep(0.2)
            return "done"

        task = llm_service.create_background_task(sample_task(), name="status_test")

        # Get status while task is running
        status = await llm_service.get_task_status()
        assert status["active_tasks"] == 1
        assert status["max_concurrent"] == 50
        assert len(status["tasks"]) == 1
        assert status["tasks"][0]["name"] == "status_test"
        assert status["tasks"][0]["done"] is False

        # Wait for task completion
        await task

        # Get status after task completion
        status = await llm_service.get_task_status()
        assert status["active_tasks"] == 0
        assert len(status["tasks"]) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_tasks(self, llm_service):
        """Test cancelling all background tasks."""

        async def long_task():
            await asyncio.sleep(5)
            return "should_not_complete"

        # Create multiple tasks
        tasks = [
            llm_service.create_background_task(long_task(), name=f"task_{i}")
            for i in range(3)
        ]

        # Verify tasks were created
        assert len(llm_service._task_manager._background_tasks) == 3

        # Cancel all tasks
        await llm_service.cancel_all_tasks()

        # Verify cleanup
        assert len(llm_service._task_manager._background_tasks) == 0
        assert len(llm_service._task_manager._task_metadata) == 0

        # Verify tasks were cancelled
        for task in tasks:
            assert task.cancelled()

    @pytest.mark.asyncio
    async def test_wait_for_tasks_timeout(self, llm_service):
        """Test waiting for tasks with timeout."""

        async def long_task():
            await asyncio.sleep(5)
            return "late_result"

        # Create a long-running task
        task = llm_service.create_background_task(long_task(), name="timeout_test")

        # Wait with short timeout (should timeout and cancel)
        with patch("kollabor_agent.background_task_manager.logger") as mock_logger:
            await llm_service.wait_for_tasks(timeout=0.1)
            mock_logger.warning.assert_called_with(
                "Timeout waiting for tasks to complete"
            )

        # Verify task was cancelled
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_wait_for_tasks_success(self, llm_service):
        """Test successfully waiting for tasks."""

        async def quick_task():
            await asyncio.sleep(0.1)
            return "quick_result"

        # Create a quick task
        task = llm_service.create_background_task(quick_task(), name="wait_test")

        # Wait with sufficient timeout
        await llm_service.wait_for_tasks(timeout=5.0)

        # Verify task completed successfully
        assert task.done()
        assert not task.cancelled()
        assert task.exception() is None

    @pytest.mark.asyncio
    async def test_task_metadata_tracking(self, llm_service):
        """Test that task metadata is properly tracked."""

        async def named_task():
            return "metadata_test"

        task = llm_service.create_background_task(
            named_task(), name="metadata_test_task"
        )

        # Verify metadata was created
        tm = llm_service._task_manager
        assert "metadata_test_task" in tm._task_metadata
        metadata = tm._task_metadata["metadata_test_task"]
        assert "created_at" in metadata
        assert "coro_name" in metadata

        # Wait for completion
        await task

        # Verify metadata was cleaned up
        assert "metadata_test_task" not in tm._task_metadata

    @pytest.mark.asyncio
    async def test_task_monitoring_cleanup(self, llm_service):
        """Test that completed tasks are cleaned up by monitoring."""

        async def quick_task():
            return "monitor_test"

        task = llm_service.create_background_task(quick_task(), name="monitor_test")

        # Wait for task completion
        await task

        # Simulate monitoring cleanup (normally runs in background)
        tm = llm_service._task_manager
        completed_tasks = [t for t in tm._background_tasks if t.done()]
        for completed_task in completed_tasks:
            tm._background_tasks.discard(completed_task)

        # Verify cleanup
        assert len(tm._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_error_handling_wrapper(self, llm_service):
        """Test the safe task wrapper error handling."""
        error_logged = False

        async def error_handler(task_name, error):
            nonlocal error_logged
            error_logged = True
            assert task_name == "wrapper_test"
            assert isinstance(error, ValueError)

        with patch.object(
            llm_service._task_manager, "_handle_task_error", side_effect=error_handler
        ):

            async def failing_task():
                raise ValueError("Wrapper test error")

            task = llm_service.create_background_task(
                failing_task(), name="wrapper_test"
            )

            # Wait for task to fail
            with pytest.raises(ValueError):
                await task

            # Verify error was handled
            assert error_logged
            assert llm_service._task_manager._task_error_count == 1

    @pytest.mark.asyncio
    async def test_task_naming_auto_generation(self, llm_service):
        """Test automatic task name generation."""

        async def unnamed_task():
            return "auto_named"

        task = llm_service.create_background_task(unnamed_task())  # No name provided

        # Verify auto-generated name
        task_name = task.get_name()
        assert task_name.startswith("bg_task_")

        # Verify metadata was created with auto-generated name
        assert task_name in llm_service._task_manager._task_metadata

        await task

    @pytest.mark.asyncio
    async def test_task_metadata_storage_content(self, llm_service):
        """Test the content stored in task metadata."""

        async def test_task():
            return "test"

        # Test with named coroutine
        task = llm_service.create_background_task(test_task(), name="named_task")
        metadata = llm_service._task_manager._task_metadata["named_task"]
        assert isinstance(metadata["coro_name"], str)
        await task

    @pytest.mark.asyncio
    async def test_shutdown_cleanup(self, llm_service):
        """Test that shutdown properly cleans up tasks."""

        async def cleanup_test_task():
            await asyncio.sleep(1)
            return "should_not_complete"

        # Create a task
        task = llm_service.create_background_task(
            cleanup_test_task(), name="cleanup_test"
        )
        assert len(llm_service._task_manager._background_tasks) == 1

        # Start monitoring task
        await llm_service.start_task_monitor()
        monitoring_task = llm_service._task_manager._monitoring_task
        assert monitoring_task is not None

        # Make mocked async methods awaitable for shutdown
        llm_service.conversation_logger.log_conversation_end = AsyncMock()
        llm_service.api_service.shutdown = AsyncMock()
        llm_service.mcp_integration.shutdown = AsyncMock()

        # Shutdown the service
        await llm_service.shutdown()

        # Verify all tasks were cancelled and cleaned up
        assert len(llm_service._task_manager._background_tasks) == 0
        assert task.cancelled()
        assert monitoring_task.cancelled()


class TestTaskManagementIntegration:
    """Integration tests for task management with other LLM service components."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration."""
        config = Mock()
        config.get.side_effect = lambda key, default=None: {
            "kollabor.llm.max_history": 90,
            "kollabor.llm.background_tasks.max_concurrent": 50,
            "kollabor.llm.background_tasks.enable_monitoring": False,
            "kollabor.llm.background_tasks.cleanup_interval": 60,
            "kollabor.llm.terminal_timeout": 30,
            "kollabor.llm.mcp_timeout": 60,
            "kollabor.llm.processing_delay": 0.1,
            "kollabor.llm.thinking_delay": 0.3,
        }.get(key, default)
        return config

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        event_bus = Mock()
        event_bus.register_hook = AsyncMock()
        return event_bus

    @pytest.fixture
    def mock_renderer(self):
        """Create a mock renderer."""
        return Mock()

    @pytest_asyncio.fixture
    async def llm_service(self, mock_config, mock_event_bus, mock_renderer):
        """Create an LLM service instance for testing."""
        with (
            patch("kollabor_config.config_utils.get_conversations_dir"),
            patch("kollabor.llm.llm_coordinator.KollaborConversationLogger"),
            patch("kollabor.llm.llm_coordinator.LLMHookSystem"),
            patch("kollabor.llm.llm_coordinator.MCPIntegration"),
            patch("kollabor.llm.llm_coordinator.ResponseParser"),
            patch("kollabor.llm.llm_coordinator.ToolExecutor"),
            patch("kollabor.llm.llm_coordinator.MessageDisplayService"),
            patch("kollabor.llm.llm_coordinator.APICommunicationService"),
        ):

            service = LLMService(mock_config, mock_event_bus, mock_renderer)
            return service

    @pytest.mark.asyncio
    async def test_status_line_includes_task_info(self, llm_service):
        """Test that status line includes task information."""

        async def status_test_task():
            await asyncio.sleep(0.5)

        # Mock tool_executor.get_execution_stats to return proper dict
        llm_service.tool_executor.get_execution_stats.return_value = {
            "total_executions": 0,
            "terminal_executions": 0,
            "mcp_executions": 0,
            "success_rate": 0.0,
        }

        # Create a task
        task = llm_service.create_background_task(
            status_test_task(), name="status_line_test"
        )

        # Get status line
        status = llm_service.get_status_line()

        # Status should have task info in area C
        c_items = status.get("C", [])
        has_task_info = any("status_line_test" in item for item in c_items)
        assert has_task_info, f"Expected task info in status C, got: {c_items}"

        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
