"""Tests for StatusService."""

import asyncio
import time
import unittest
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from kollabor.llm.status_service import StatusService


@dataclass
class MockBackgroundTasksConfig:
    """Mock config for testing."""

    enable_task_circuit_breaker: bool = False
    circuit_breaker_threshold: int = 5


@dataclass
class MockTaskConfig:
    """Mock task config for testing."""

    background_tasks: MockBackgroundTasksConfig = field(
        default_factory=MockBackgroundTasksConfig
    )
    queue: MagicMock = field(default_factory=lambda: MagicMock())


def _make_coordinator(**overrides):
    """Create a mock coordinator with standard defaults for status service."""
    coord = MagicMock()

    # Task manager
    coord._task_manager._background_tasks = set()
    coord._task_manager._task_metadata = {}
    coord._task_manager._task_error_count = 0
    coord._task_manager._circuit_breaker_state = "CLOSED"
    coord._task_manager._circuit_breaker_failures = 0

    # Task config
    task_config = MockTaskConfig()
    task_config.queue = MagicMock()
    task_config.queue.overflow_strategy = "drop_oldest"
    task_config.queue.enable_queue_metrics = False
    coord.task_config = task_config

    # Tool executor
    coord.tool_executor.get_execution_stats = MagicMock(
        return_value={
            "total_executions": 0,
            "terminal_executions": 0,
            "mcp_executions": 0,
            "success_rate": 1.0,
        }
    )

    # State
    coord.conversation_history = []
    coord._queue_metrics = {
        "drop_oldest_count": 0,
        "drop_newest_count": 0,
        "block_count": 0,
        "block_timeout_count": 0,
        "total_enqueue_attempts": 0,
        "total_enqueue_successes": 0,
    }
    coord.session_stats = {"messages": 0, "input_tokens": 100, "output_tokens": 50}
    coord.is_processing = False
    coord.processing_start_time = None
    coord.current_processing_tokens = 0
    coord.processing_queue = asyncio.Queue(maxsize=10)
    coord.max_queue_size = 10
    coord.dropped_messages = 0

    # Apply overrides
    for key, value in overrides.items():
        setattr(coord, key, value)

    return coord


class TestStatusService(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.coordinator = _make_coordinator()
        self.service = StatusService(coordinator=self.coordinator)

    def test_init(self):
        """Test StatusService initialization."""
        self.assertIsNotNone(self.service)
        self.assertIs(self.service._coordinator, self.coordinator)

    def test_get_status_line_not_processing(self):
        """Test status line when not processing."""
        status = self.service.get_status_line()

        self.assertIn("Processing: No", status["A"])
        c_joined = " ".join(status["C"])
        self.assertIn("Queue:", c_joined)
        self.assertIn("History: 0", c_joined)

    def test_get_status_line_processing_with_tokens(self):
        """Test status line when processing with tokens."""
        coord = _make_coordinator(
            is_processing=True,
            processing_start_time=time.time(),
            current_processing_tokens=42,
        )
        service = StatusService(coordinator=coord)

        status = service.get_status_line()

        a_joined = " ".join(status["A"])
        self.assertIn("Processing: 42 tokens", a_joined)

    def test_get_status_line_with_session_stats(self):
        """Test status line includes session stats."""
        status = self.service.get_status_line()

        c_joined = " ".join(status["C"])
        self.assertIn("Queue:", c_joined)
        self.assertIn("History:", c_joined)

    def test_get_status_line_with_tool_stats(self):
        """Test status line includes tool execution stats."""
        self.coordinator.tool_executor.get_execution_stats = MagicMock(
            return_value={
                "total_executions": 10,
                "terminal_executions": 5,
                "mcp_executions": 5,
                "success_rate": 0.9,
            }
        )

        status = self.service.get_status_line()

        self.assertIn("Tools: 10", status["A"])
        self.assertIn("Terminal: 5", status["A"])
        self.assertIn("MCP: 5", status["A"])
        self.assertIn("Success: 90.0%", status["A"])

    def test_get_status_line_queue_warning(self):
        """Test status line shows warning when queue is full."""
        queue = asyncio.Queue(maxsize=10)
        for i in range(10):
            queue.put_nowait(f"item-{i}")

        coord = _make_coordinator(processing_queue=queue)
        service = StatusService(coordinator=coord)

        status = service.get_status_line()

        warning_found = any("Queue usage high" in item for item in status["C"])
        self.assertTrue(warning_found)

    def test_get_queue_metrics(self):
        """Test queue metrics retrieval."""
        metrics = self.service.get_queue_metrics()

        self.assertEqual(metrics["current_size"], 0)
        self.assertEqual(metrics["max_size"], 10)
        self.assertEqual(metrics["utilization_percent"], 0.0)
        self.assertEqual(metrics["dropped_messages"], 0)
        self.assertEqual(metrics["status"], "healthy")
        self.assertTrue(metrics["memory_safe"])
        self.assertEqual(metrics["overflow_strategy"], "drop_oldest")

    def test_get_queue_metrics_with_overflow(self):
        """Test queue metrics with overflow tracking enabled."""
        self.coordinator.task_config.queue.enable_queue_metrics = True
        self.coordinator._queue_metrics["total_enqueue_attempts"] = 100
        self.coordinator._queue_metrics["total_enqueue_successes"] = 95

        metrics = self.service.get_queue_metrics()

        self.assertIn("overflow_metrics", metrics)
        self.assertEqual(metrics["overflow_metrics"]["total_enqueue_attempts"], 100)
        self.assertEqual(metrics["overflow_metrics"]["total_enqueue_successes"], 95)
        self.assertEqual(metrics["overflow_metrics"]["success_rate"], 95.0)

    def test_get_queue_metrics_warning_status(self):
        """Test queue metrics status levels."""
        queue = asyncio.Queue(maxsize=10)
        for i in range(8):
            queue.put_nowait(f"item-{i}")

        coord = _make_coordinator(processing_queue=queue)
        service = StatusService(coordinator=coord)

        metrics = service.get_queue_metrics()

        self.assertEqual(metrics["status"], "warning")

    def test_reset_queue_metrics(self):
        """Test resetting queue metrics."""
        self.coordinator._queue_metrics["drop_oldest_count"] = 5
        self.coordinator._queue_metrics["drop_newest_count"] = 3

        self.service.reset_queue_metrics()

        self.assertEqual(self.coordinator.dropped_messages, 0)
        self.assertEqual(self.coordinator._queue_metrics["drop_oldest_count"], 0)
        self.assertEqual(self.coordinator._queue_metrics["drop_newest_count"], 0)


if __name__ == "__main__":
    unittest.main()
