"""Status and metrics service for LLMService.

This module contains all status line generation and queue metrics
methods, providing monitoring and observability for the LLM service.
"""

import logging
import time
from typing import Dict, List

logger = logging.getLogger(__name__)


class StatusService:
    """Handles status line generation and metrics for the LLM service.

    This class encapsulates all status-related methods that generate
    status line content and queue metrics for monitoring.

    Takes a single coordinator reference instead of individual dependencies.
    """

    def __init__(self, coordinator):
        """Initialize the status service.

        Args:
            coordinator: LLMService coordinator (provides task_manager, task_config,
                tool_executor, conversation_history, queue_metrics, session_stats,
                and queue/processing state properties)
        """
        self._coordinator = coordinator

    def get_status_line(self) -> Dict[str, List[str]]:
        """Get status information for display."""
        status: Dict[str, List[str]] = {"A": [], "B": [], "C": []}
        coord = self._coordinator

        # Area B - LLM status
        if coord.is_processing:
            # Show elapsed time and tokens
            elapsed = ""
            processing_start_time = coord.processing_start_time
            if processing_start_time:
                elapsed_secs = time.time() - processing_start_time
                elapsed = f" ({elapsed_secs:.1f}s)"

            current_tokens = coord.current_processing_tokens
            if current_tokens > 0:
                status["A"].append(f"Processing: {current_tokens} tokens{elapsed}")
            else:
                status["A"].append(f"Processing: Yes{elapsed}")
        else:
            status["A"].append("Processing: No")

        # Enhanced queue metrics with memory leak monitoring
        processing_queue = coord.processing_queue
        max_queue_size = coord.max_queue_size
        queue_size = processing_queue.qsize()
        queue_utilization = (
            (queue_size / max_queue_size * 100) if max_queue_size > 0 else 0
        )
        dropped_messages = coord.dropped_messages
        dropped_indicator = (
            f" ({dropped_messages} dropped)" if dropped_messages > 0 else ""
        )

        status["C"].append(
            f"Queue: {queue_size}/{max_queue_size} ({queue_utilization:.0f}%){dropped_indicator}"
        )

        # Add warning if queue utilization is high
        if queue_utilization > 80:
            status["C"].append("Warning: Queue usage high!")
        status["C"].append(f"History: {len(coord.conversation_history)}")

        # Show active background tasks with elapsed time
        tm = coord._task_manager
        if tm._background_tasks:
            # Show up to 3 most recent tasks
            for task in list(tm._background_tasks)[:3]:
                task_name = task.get_name()
                if task_name and task_name in tm._task_metadata:
                    elapsed = time.time() - tm._task_metadata[task_name]["start_time"]
                    status["C"].append(f"Task: {task_name} ({elapsed:.0f}s)")
                elif task_name:
                    status["C"].append(f"Task: {task_name}")

        if tm._task_error_count > 0:
            status["C"].append(f"Task Errors: {tm._task_error_count}")

        # Circuit breaker status if enabled
        if coord.task_config.background_tasks.enable_task_circuit_breaker:
            cb_state = tm._circuit_breaker_state
            cb_failures = tm._circuit_breaker_failures
            cb_threshold = coord.task_config.background_tasks.circuit_breaker_threshold

            if cb_state == "OPEN":
                status["C"].append(
                    f"Warning: Circuit: OPEN ({cb_failures}/{cb_threshold})"
                )
            elif cb_state == "HALF_OPEN":
                status["C"].append(f"Circuit: HALF_OPEN ({cb_failures}/{cb_threshold})")
            else:  # CLOSED
                if cb_failures > 0:
                    status["C"].append(
                        f"Circuit: CLOSED ({cb_failures}/{cb_threshold})"
                    )

        # Area C - Session stats
        session_stats = coord.session_stats
        if session_stats["messages"] > 0:
            status["C"].append(f"Messages: {session_stats['messages']}")
            status["C"].append(f"Tokens In: {session_stats.get('input_tokens', 0)}")
            status["C"].append(f"Tokens Out: {session_stats.get('output_tokens', 0)}")

        # Area A - Tool execution stats
        tool_stats = coord.tool_executor.get_execution_stats()
        if tool_stats["total_executions"] > 0:
            status["A"].append(f"Tools: {tool_stats['total_executions']}")
            status["A"].append(f"Terminal: {tool_stats['terminal_executions']}")
            status["A"].append(f"MCP: {tool_stats['mcp_executions']}")
            status["A"].append(f"Success: {tool_stats['success_rate']:.1%}")

        return status

    def get_queue_metrics(self) -> dict:
        """Get comprehensive queue metrics for monitoring."""
        coord = self._coordinator
        processing_queue = coord.processing_queue
        max_queue_size = coord.max_queue_size
        queue_size = processing_queue.qsize()
        queue_utilization = (
            (queue_size / max_queue_size * 100) if max_queue_size > 0 else 0
        )
        dropped_messages = coord.dropped_messages

        base_metrics = {
            "current_size": queue_size,
            "max_size": max_queue_size,
            "utilization_percent": round(queue_utilization, 1),
            "dropped_messages": dropped_messages,
            "status": (
                "healthy"
                if queue_utilization < 80
                else "warning" if queue_utilization < 95 else "critical"
            ),
            "memory_safe": queue_utilization < 90,
            "overflow_strategy": coord.task_config.queue.overflow_strategy,
        }

        # Add overflow strategy metrics if enabled
        queue_metrics = coord._queue_metrics
        if coord.task_config.queue.enable_queue_metrics:
            base_metrics.update(
                {
                    "overflow_metrics": {
                        "drop_oldest_count": queue_metrics["drop_oldest_count"],
                        "drop_newest_count": queue_metrics["drop_newest_count"],
                        "block_count": queue_metrics["block_count"],
                        "block_timeout_count": queue_metrics["block_timeout_count"],
                        "total_enqueue_attempts": queue_metrics[
                            "total_enqueue_attempts"
                        ],
                        "total_enqueue_successes": queue_metrics[
                            "total_enqueue_successes"
                        ],
                        "success_rate": (
                            (
                                queue_metrics["total_enqueue_successes"]
                                / queue_metrics["total_enqueue_attempts"]
                                * 100
                            )
                            if queue_metrics["total_enqueue_attempts"] > 0
                            else 100.0
                        ),
                    }
                }
            )

        return base_metrics

    def reset_queue_metrics(self):
        """Reset queue metrics (for testing or maintenance)."""
        coord = self._coordinator
        coord.dropped_messages = 0

        # Reset overflow strategy metrics
        for key in coord._queue_metrics:
            coord._queue_metrics[key] = 0

        logger.info("Queue metrics reset")
