"""Tests for BackgroundTaskManager."""

import asyncio
import time
import unittest
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from kollabor_agent import BackgroundTaskManager


@dataclass
class MockBackgroundTasksConfig:
    max_concurrent: int = 5
    default_timeout: float = 0
    cleanup_interval: int = 30
    enable_monitoring: bool = True
    log_task_events: bool = False
    log_task_errors: bool = False
    enable_metrics: bool = False
    task_retry_attempts: int = 3
    task_retry_delay: float = 1
    enable_task_circuit_breaker: bool = False
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60


@dataclass
class MockQueueConfig:
    max_size: int = 1000
    overflow_strategy: str = "drop_oldest"
    block_timeout: float = None
    enable_queue_metrics: bool = False
    log_queue_events: bool = False


@dataclass
class MockTaskConfig:
    background_tasks: MockBackgroundTasksConfig = field(
        default_factory=MockBackgroundTasksConfig
    )
    queue: MockQueueConfig = field(default_factory=MockQueueConfig)


class TestBackgroundTaskManager(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.task_config = MockTaskConfig()
        self.queue_metrics = {
            "drop_oldest_count": 0,
            "drop_newest_count": 0,
            "block_count": 0,
            "block_timeout_count": 0,
        }
        self.manager = BackgroundTaskManager(
            task_config=self.task_config,
            queue_metrics=self.queue_metrics,
            enable_metrics=False,
        )

    def tearDown(self):
        for task in list(self.manager._background_tasks):
            task.cancel()
        self.loop.close()

    def test_init(self):
        self.assertEqual(self.manager._max_concurrent_tasks, 5)
        self.assertEqual(self.manager._circuit_breaker_state, "CLOSED")
        self.assertEqual(self.manager._circuit_breaker_failures, 0)
        self.assertEqual(len(self.manager._background_tasks), 0)

    def test_init_with_metrics(self):
        mgr = BackgroundTaskManager(
            task_config=self.task_config,
            queue_metrics=self.queue_metrics,
            enable_metrics=True,
        )
        self.assertTrue(hasattr(mgr, "_task_metrics"))

    def test_create_background_task_success(self):
        async def run():
            task = self.manager.create_background_task(asyncio.sleep(0.01), "test_task")
            self.assertIsNotNone(task)
            self.assertIn(task, self.manager._background_tasks)
            self.assertEqual(task.get_name(), "test_task")
            task.cancel()

        self.loop.run_until_complete(run())

    def test_create_background_task_auto_naming(self):
        async def run():
            task = self.manager.create_background_task(asyncio.sleep(0.01))
            self.assertTrue(task.get_name().startswith("bg_task_"))
            task.cancel()

        self.loop.run_until_complete(run())

    def test_circuit_breaker_open_rejects(self):
        self.task_config.background_tasks.enable_task_circuit_breaker = True
        self.manager._circuit_breaker_state = "OPEN"

        async def noop():
            return "done"

        with self.assertRaises(Exception) as ctx:
            self.manager.create_background_task(noop(), "test_task")
        self.assertIn("Circuit breaker OPEN", str(ctx.exception))

    def test_circuit_breaker_half_open_allows_one(self):
        self.task_config.background_tasks.enable_task_circuit_breaker = True
        self.manager._circuit_breaker_state = "HALF_OPEN"
        self.manager._circuit_breaker_test_task_running = False

        async def run():
            task = self.manager.create_background_task(asyncio.sleep(0.01), "test")
            self.assertIsNotNone(task)
            self.assertTrue(self.manager._circuit_breaker_test_task_running)
            task.cancel()

        self.loop.run_until_complete(run())

    def test_circuit_breaker_half_open_rejects_second(self):
        self.task_config.background_tasks.enable_task_circuit_breaker = True
        self.manager._circuit_breaker_state = "HALF_OPEN"
        self.manager._circuit_breaker_test_task_running = True

        async def noop():
            return "done"

        with self.assertRaises(Exception) as ctx:
            self.manager.create_background_task(noop(), "test")
        self.assertIn("HALF_OPEN", str(ctx.exception))

    def test_overflow_drop_newest(self):
        self.task_config.queue.overflow_strategy = "drop_newest"
        for _ in range(5):
            self.manager._background_tasks.add(MagicMock())

        async def noop():
            return "done"

        with self.assertRaises(RuntimeError):
            self.manager.create_background_task(noop(), "test")
        self.assertEqual(self.queue_metrics["drop_newest_count"], 1)

    def test_overflow_drop_oldest(self):
        self.task_config.queue.overflow_strategy = "drop_oldest"
        for i in range(5):
            task = MagicMock()
            task.get_name = MagicMock(return_value=f"task_{i}")
            task.cancel = MagicMock()
            self.manager._background_tasks.add(task)
            self.manager._task_metadata[f"task_{i}"] = {
                "created_at": time.time() - (5 - i),
                "start_time": time.time() - (5 - i),
            }

        async def run():
            task = self.manager.create_background_task(asyncio.sleep(0.01), "new_task")
            task.cancel()

        self.loop.run_until_complete(run())

        self.assertEqual(self.queue_metrics["drop_oldest_count"], 1)

    def test_task_done_callback_removes_task(self):
        async def run():
            async def quick():
                pass

            task = self.manager.create_background_task(quick(), "test")
            self.assertIn(task, self.manager._background_tasks)
            await task  # let it finish so task.exception() works
            self.manager._task_done_callback(task)
            self.assertNotIn(task, self.manager._background_tasks)

        self.loop.run_until_complete(run())

    def test_task_done_callback_closes_circuit(self):
        self.task_config.background_tasks.enable_task_circuit_breaker = True
        self.manager._circuit_breaker_state = "HALF_OPEN"

        async def run():
            async def quick():
                pass

            task = self.manager.create_background_task(quick(), "test")
            await task  # let it finish
            self.manager._task_done_callback(task)
            self.assertEqual(self.manager._circuit_breaker_state, "CLOSED")
            self.assertEqual(self.manager._circuit_breaker_failures, 0)

        self.loop.run_until_complete(run())

    def test_handle_task_error_increments_failures(self):
        self.task_config.background_tasks.enable_task_circuit_breaker = True
        self.task_config.background_tasks.circuit_breaker_threshold = 3

        self.loop.run_until_complete(
            self.manager._handle_task_error("test", Exception("fail"))
        )
        self.assertEqual(self.manager._circuit_breaker_failures, 1)

    def test_handle_task_error_opens_circuit(self):
        self.task_config.background_tasks.enable_task_circuit_breaker = True
        self.task_config.background_tasks.circuit_breaker_threshold = 3
        self.manager._circuit_breaker_failures = 2

        self.loop.run_until_complete(
            self.manager._handle_task_error("test", Exception("fail"))
        )
        self.assertEqual(self.manager._circuit_breaker_state, "OPEN")

    def test_get_task_status(self):
        async def run():
            task = self.manager.create_background_task(asyncio.sleep(10), "test")
            status = await self.manager.get_task_status()
            self.assertEqual(status["active_tasks"], 1)
            self.assertEqual(status["max_concurrent"], 5)
            self.assertEqual(status["error_count"], 0)
            self.assertEqual(len(status["tasks"]), 1)
            task.cancel()

        self.loop.run_until_complete(run())

    def test_cancel_all_tasks(self):
        async def run():
            for i in range(3):
                self.manager.create_background_task(asyncio.sleep(10), f"task_{i}")
            self.assertEqual(len(self.manager._background_tasks), 3)
            await self.manager.cancel_all_tasks()
            self.assertEqual(len(self.manager._background_tasks), 0)

        self.loop.run_until_complete(run())

    def test_wait_for_tasks(self):
        completed = []

        async def quick():
            completed.append(1)

        async def run():
            for i in range(3):
                self.manager.create_background_task(quick(), f"task_{i}")
            await self.manager.wait_for_tasks(timeout=5.0)
            self.assertEqual(len(completed), 3)

        self.loop.run_until_complete(run())

    def test_safe_task_wrapper_success(self):
        async def noop():
            return "done"

        result = self.loop.run_until_complete(
            self.manager._safe_task_wrapper(noop(), "test")
        )
        self.assertEqual(result, "done")

    def test_safe_task_wrapper_cancelled(self):
        async def cancel():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(
                self.manager._safe_task_wrapper(cancel(), "test")
            )

    def test_safe_task_wrapper_exception(self):
        self.task_config.background_tasks.log_task_errors = True

        async def fail():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            self.loop.run_until_complete(
                self.manager._safe_task_wrapper(fail(), "test")
            )
        self.assertEqual(self.manager._task_error_count, 1)


if __name__ == "__main__":
    unittest.main()
