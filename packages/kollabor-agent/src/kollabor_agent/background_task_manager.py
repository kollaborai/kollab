"""Background task management for Kollab LLM service.

Handles task creation, tracking, circuit breaker pattern, retry logic,
monitoring, and cleanup. Extracted from LLMService as part of the
llm_service.py decomposition (Phase A).
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Set

from kollabor_config import LLMTaskConfig

logger = logging.getLogger(__name__)


class BackgroundTaskManager:
    """Manages background task lifecycle with circuit breaker and monitoring.

    Responsibilities:
    - Task creation with overflow strategies (drop_newest, drop_oldest, block)
    - Circuit breaker pattern (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
    - Retry logic with configurable attempts and delay
    - Task monitoring and cleanup
    - Metrics tracking (optional)
    """

    def __init__(
        self,
        task_config: LLMTaskConfig,
        queue_metrics: Dict[str, int],
        enable_metrics: bool = False,
    ):
        """Initialize the background task manager.

        Args:
            task_config: Task management configuration (from config.core.llm.task_management)
            queue_metrics: Shared metrics dict (also used by queue overflow strategies)
            enable_metrics: Whether to enable detailed per-task metrics
        """
        self.task_config = task_config
        self._queue_metrics = queue_metrics
        self.enable_metrics = enable_metrics

        # Task tracking
        self._background_tasks: Set[asyncio.Task] = set()
        self._task_metadata: Dict[str, Any] = {}
        self._max_concurrent_tasks = task_config.background_tasks.max_concurrent
        self._task_error_count = 0
        self._monitoring_task: Optional[asyncio.Task] = None

        # Circuit breaker state
        self._circuit_breaker_state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self._circuit_breaker_failures = 0
        self._circuit_breaker_last_failure_time: Optional[float] = None
        self._circuit_breaker_test_task_running = False

        # Per-task metrics (optional)
        if self.enable_metrics:
            self._task_metrics: Dict[str, Dict[str, Any]] = {}

    def create_background_task(self, coro, name: Optional[str] = None) -> asyncio.Task:
        """Create and track a background task with proper error handling and circuit breaker."""

        def _close_coro_and_raise(exc):
            """Helper to properly close coroutine before raising exception."""
            coro.close()
            raise exc

        # Check circuit breaker state
        if self.task_config.background_tasks.enable_task_circuit_breaker:
            # Reject tasks if circuit is OPEN
            if self._circuit_breaker_state == "OPEN":
                # Check if timeout has passed to transition to HALF_OPEN
                if self._circuit_breaker_last_failure_time:
                    time_since_failure = (
                        time.time() - self._circuit_breaker_last_failure_time
                    )
                    timeout = self.task_config.background_tasks.circuit_breaker_timeout
                    if time_since_failure >= timeout:
                        logger.info(
                            "Circuit breaker timeout elapsed, transitioning to HALF_OPEN"
                        )
                        self._circuit_breaker_state = "HALF_OPEN"
                        self._circuit_breaker_test_task_running = False
                    else:
                        logger.warning(
                            f"Circuit breaker OPEN - rejecting task '{name or 'unnamed'}'"
                        )
                        _close_coro_and_raise(
                            Exception(
                                f"Circuit breaker OPEN - tasks rejected for {timeout - time_since_failure:.1f}s more"
                            )
                        )
                else:
                    logger.warning(
                        f"Circuit breaker OPEN - rejecting task '{name or 'unnamed'}'"
                    )
                    _close_coro_and_raise(
                        Exception("Circuit breaker OPEN - tasks rejected")
                    )

            # Allow only one test task in HALF_OPEN state
            elif (
                self._circuit_breaker_state == "HALF_OPEN"
                and self._circuit_breaker_test_task_running
            ):
                logger.warning(
                    f"Circuit breaker HALF_OPEN - test task already running, rejecting '{name or 'unnamed'}'"
                )
                _close_coro_and_raise(
                    Exception("Circuit breaker HALF_OPEN - test task already running")
                )

        # Handle task overflow using configured queue strategy
        if len(self._background_tasks) >= self._max_concurrent_tasks:
            strategy = self.task_config.queue.overflow_strategy

            if self.task_config.queue.log_queue_events:
                logger.debug(
                    f"Background task queue full ({len(self._background_tasks)}/"
                    f"{self._max_concurrent_tasks}), applying strategy: {strategy}"
                )

            if strategy == "drop_newest":
                # Raise RuntimeError when task queue is full
                self._queue_metrics["drop_newest_count"] += 1
                if self.task_config.queue.log_queue_events:
                    logger.debug("Background task queue full - raising RuntimeError")
                _close_coro_and_raise(
                    RuntimeError(
                        f"Maximum concurrent tasks ({self._max_concurrent_tasks}) "
                        f"reached and overflow strategy is 'drop_newest'"
                    )
                )

            elif strategy == "drop_oldest":
                # Cancel oldest task by start_time to make room
                oldest_task = None
                oldest_start_time = None

                for task in self._background_tasks:
                    task_name = task.get_name()
                    if task_name in self._task_metadata:
                        start_time = self._task_metadata[task_name].get("created_at")
                        if start_time and (
                            oldest_start_time is None or start_time < oldest_start_time
                        ):
                            oldest_task = task
                            oldest_start_time = start_time

                if oldest_task:
                    oldest_task.cancel()
                    self._queue_metrics["drop_oldest_count"] += 1
                    if self.task_config.queue.log_queue_events:
                        logger.info(
                            f"Cancelled oldest background task {oldest_task.get_name()} to make room"
                        )
                else:
                    # No suitable task found, raise error
                    _close_coro_and_raise(
                        RuntimeError(
                            f"Maximum concurrent tasks ({self._max_concurrent_tasks}) "
                            f"reached and no cancellable tasks found"
                        )
                    )

            elif strategy == "block":
                # For block strategy, create a background task that handles the blocking
                self._queue_metrics["block_count"] += 1
                if self.task_config.queue.log_queue_events:
                    logger.debug(
                        f"Creating background task to handle blocking strategy "
                        f"(timeout: {self.task_config.queue.block_timeout}s)"
                    )

                # Create a task that will wait for space and then run the actual task
                blocking_task = asyncio.create_task(
                    self._create_task_with_blocking(coro, name),
                    name=f"blocking_wrapper_{name or 'unnamed'}",
                )
                return blocking_task

            else:
                # Unknown strategy, default to drop_oldest
                logger.warning(
                    f"Unknown overflow strategy '{strategy}', defaulting to drop_oldest"
                )
                _close_coro_and_raise(
                    RuntimeError(
                        f"Maximum concurrent tasks ({self._max_concurrent_tasks}) reached"
                    )
                )

        task_name = name or f"bg_task_{datetime.now().timestamp()}"
        start_time = time.time()

        # Store original coroutine before timeout wrapping for retry purposes
        original_coro = coro

        # Add timeout wrapping if default_timeout is set (0 = disabled for autonomous LLM work)
        default_timeout = getattr(
            self.task_config.background_tasks, "default_timeout", 0
        )
        if default_timeout is not None and default_timeout > 0:
            wrapped_coro = asyncio.wait_for(coro, timeout=default_timeout)
        else:
            wrapped_coro = coro

        # Mark test task running in HALF_OPEN state
        if (
            self.task_config.background_tasks.enable_task_circuit_breaker
            and self._circuit_breaker_state == "HALF_OPEN"
        ):
            self._circuit_breaker_test_task_running = True
            logger.info(f"Circuit breaker HALF_OPEN - allowing test task '{task_name}'")

        task = asyncio.create_task(
            self._safe_task_wrapper(wrapped_coro, task_name), name=task_name
        )

        # Add to set and register callback before any await so the done_callback
        # always sees the task in _background_tasks even if the task finishes
        # synchronously before the next yield point.
        self._background_tasks.add(task)
        task.add_done_callback(self._task_done_callback)

        # Track the task with retry information
        self._task_metadata[task_name] = {
            "created_at": datetime.now(),
            "coro_name": coro.__name__ if hasattr(coro, "__name__") else str(coro),
            "start_time": start_time,
            "retry_count": 0,
            "original_coro": original_coro,  # Store original coroutine for retries
        }

        return task

    async def _create_task_with_blocking(self, coro, name: Optional[str] = None) -> Any:
        """Handle blocking strategy by waiting for available task slot."""
        start_time = time.time()
        poll_interval = 0.01  # 10ms polling

        while len(self._background_tasks) >= self._max_concurrent_tasks:
            # Check timeout
            elapsed = time.time() - start_time
            if (
                self.task_config.queue.block_timeout is not None
                and elapsed >= self.task_config.queue.block_timeout
            ):
                self._queue_metrics["block_timeout_count"] += 1
                if self.task_config.queue.log_queue_events:
                    logger.warning(
                        f"Background task block timeout after {elapsed:.2f}s"
                    )
                raise RuntimeError(
                    f"Timeout waiting for available task slot (timeout: {self.task_config.queue.block_timeout}s)"
                )

            # Brief sleep before next poll
            await asyncio.sleep(poll_interval)

        # Space is available, create the actual task using the normal path
        return self.create_background_task(coro, name)

    async def _safe_task_wrapper(self, coro, task_name: str):
        """Wrapper that safely executes task and handles exceptions."""
        try:
            if self.task_config.background_tasks.log_task_events:
                logger.debug(f"Starting background task: {task_name}")
            result = await coro
            if self.task_config.background_tasks.log_task_events:
                logger.debug(f"Background task completed successfully: {task_name}")
            return result

        except asyncio.CancelledError:
            logger.info(f"Background task cancelled: {task_name}")
            raise

        except Exception as e:
            if self.task_config.background_tasks.log_task_errors:
                logger.error(
                    f"Background task failed: {task_name} - {type(e).__name__}: {e}"
                )
            self._task_error_count += 1
            await self._handle_task_error(task_name, e)
            raise

    def _task_done_callback(self, task: asyncio.Task):
        """Called when a task completes."""
        self._background_tasks.discard(task)

        task_name = task.get_name()

        # Track duration and metrics if enabled - capture metadata before deletion
        metadata = None
        if task_name in self._task_metadata:
            metadata = self._task_metadata[task_name]

            # Store metrics if enabled and we have start_time
            if self.enable_metrics and hasattr(self, "_task_metrics") and metadata:
                start_time = metadata.get("start_time")

                if start_time:
                    duration = time.time() - start_time

                    # Store metrics
                    self._task_metrics[task_name] = {
                        "duration": duration,
                        "status": (
                            "cancelled"
                            if task.cancelled()
                            else "failed" if task.exception() else "completed"
                        ),
                        "cancelled": task.cancelled(),
                        "exception": (
                            str(task.exception()) if task.exception() else None
                        ),
                        "completed_at": datetime.now(),
                        "coro_name": metadata.get("coro_name", "unknown"),
                    }

            # Clean up metadata
            del self._task_metadata[task_name]

        if task.cancelled():
            if self.task_config.background_tasks.log_task_events:
                logger.debug(f"Task cancelled: {task_name}")
        elif task.exception():
            if self.task_config.background_tasks.log_task_errors:
                logger.error(
                    f"Task failed with exception: {task_name} - {task.exception()}"
                )
        else:
            # Task completed successfully - check circuit breaker state
            if (
                self.task_config.background_tasks.enable_task_circuit_breaker
                and self._circuit_breaker_state == "HALF_OPEN"
            ):
                logger.info(
                    f"Circuit breaker HALF_OPEN - test task '{task_name}' "
                    f"completed successfully, transitioning to CLOSED"
                )
                self._circuit_breaker_state = "CLOSED"
                self._circuit_breaker_failures = 0
                self._circuit_breaker_last_failure_time = None
                self._circuit_breaker_test_task_running = False

            if self.task_config.background_tasks.log_task_events:
                logger.debug(f"Task completed: {task_name}")

    async def _handle_task_error(self, task_name: str, error: Exception):
        """Handle errors from background tasks with circuit breaker and retry logic."""
        # Circuit breaker pattern implementation
        if self.task_config.background_tasks.enable_task_circuit_breaker:
            self._circuit_breaker_failures += 1
            self._circuit_breaker_last_failure_time = time.time()

            # Check if failure threshold reached
            threshold = self.task_config.background_tasks.circuit_breaker_threshold
            if self._circuit_breaker_failures >= threshold:
                if self._circuit_breaker_state != "OPEN":
                    logger.warning(
                        f"Circuit breaker threshold ({threshold}) reached, "
                        f"opening circuit due to task failure: {task_name}"
                    )
                    self._circuit_breaker_state = "OPEN"
                    self._circuit_breaker_test_task_running = False
                else:
                    logger.debug(
                        f"Circuit breaker already OPEN, failure count: {self._circuit_breaker_failures}"
                    )
            else:
                logger.warning(
                    f"Task failure ({self._circuit_breaker_failures}/{threshold}) - "
                    f"circuit breaker {self._circuit_breaker_state}"
                )

        # Retry logic implementation
        task_metadata = self._task_metadata.get(task_name, {})
        retry_count = task_metadata.get("retry_count", 0)
        original_coro = task_metadata.get("original_coro")

        # Check if we should retry this task
        max_retries = self.task_config.background_tasks.task_retry_attempts
        retry_delay = self.task_config.background_tasks.task_retry_delay

        if retry_count < max_retries and original_coro is not None:
            # Increment retry count
            self._task_metadata[task_name]["retry_count"] = retry_count + 1

            logger.warning(
                f"Retrying task {task_name} (attempt {retry_count + 1}/{max_retries}) "
                f"after {retry_delay}s delay due to {type(error).__name__}: {error}"
            )

            # Wait for retry delay
            await asyncio.sleep(retry_delay)

            # Create new task with original coroutine
            new_task_name = f"{task_name}_retry_{retry_count + 1}"
            self.create_background_task(original_coro, new_task_name)

            logger.info(f"Created retry task: {new_task_name}")
        else:
            # No more retries or no original coroutine available
            if retry_count >= max_retries:
                logger.error(
                    f"Task {task_name} failed after {max_retries} retry attempts. "
                    f"Final error: {type(error).__name__}: {error}"
                )
            else:
                logger.error(f"Task {task_name} failed (no retry possible): {error}")

    async def start_task_monitor(self):
        """Start background task monitoring and cleanup."""
        self._monitoring_task = asyncio.create_task(self._monitor_tasks())
        logger.info("Task monitoring started")

    async def _monitor_tasks(self):
        """Monitor and cleanup completed tasks."""
        cleanup_interval = self.task_config.background_tasks.cleanup_interval

        while True:
            try:
                # Remove completed tasks
                completed_tasks = [t for t in self._background_tasks if t.done()]
                for task in completed_tasks:
                    self._background_tasks.discard(task)

                if completed_tasks:
                    logger.debug(f"Cleaned up {len(completed_tasks)} completed tasks")

                # Log status
                if len(self._background_tasks) > 0:
                    logger.debug(
                        f"Active background tasks: {len(self._background_tasks)}"
                    )

                await asyncio.sleep(cleanup_interval)

            except Exception as e:
                logger.error(f"Error in task monitoring: {e}")
                await asyncio.sleep(cleanup_interval)

    async def get_task_status(self):
        """Get status of all background tasks."""
        status = {
            "active_tasks": len(self._background_tasks),
            "max_concurrent": self._max_concurrent_tasks,
            "error_count": self._task_error_count,
            "tasks": [],
        }

        for task in self._background_tasks:
            exception = None
            if task.done() and not task.cancelled():
                exc = task.exception()
                if exc:
                    exception = str(exc)
            task_info = {
                "name": task.get_name(),
                "done": task.done(),
                "cancelled": task.cancelled(),
                "exception": exception,
            }
            status["tasks"].append(task_info)

        return status

    async def cancel_all_tasks(self):
        """Cancel all background tasks and wait for cleanup."""
        logger.info(f"Cancelling {len(self._background_tasks)} background tasks")

        for task in self._background_tasks:
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete (with timeout)
        if self._background_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._background_tasks, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Some tasks didn't finish gracefully")

        self._background_tasks.clear()
        self._task_metadata.clear()

    async def wait_for_tasks(self, timeout: float = 30.0):
        """Wait for all background tasks to complete."""
        if not self._background_tasks:
            return

        try:
            await asyncio.wait_for(
                asyncio.gather(*self._background_tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for tasks to complete")
            # Cancel remaining tasks
            await self.cancel_all_tasks()
