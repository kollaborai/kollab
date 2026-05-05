"""Hook executor for individual hook execution with error handling."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from .error_utils import log_and_continue
from .models import Event, Hook, HookStatus

logger = logging.getLogger(__name__)

# Configuration constants with safe limits
DEFAULT_HOOK_TIMEOUT = 30
DEFAULT_HOOK_RETRIES = 3
DEFAULT_ERROR_ACTION = "continue"
ABSOLUTE_MAX_RETRIES = 10
MIN_TIMEOUT = 1
MAX_TIMEOUT = 300  # 5 minutes
VALID_ERROR_ACTIONS = {"continue", "stop"}

# Absolute maximum time for all retry attempts combined (5 minutes)
MAX_TOTAL_RETRY_DURATION = 300


class HookExecutor:
    """Executes individual hooks with timeout and error handling.

    This class is responsible for the safe execution of a single hook,
    including timeout management, error handling, status tracking, and retry logic.

    Thread Safety:
        Uses per-hook locks to prevent concurrent execution of the same hook,
        avoiding race conditions on hook status mutations.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the hook executor.

        Args:
            config: Configuration dictionary for hook execution settings.
        """
        self.config = config or {}
        self._hook_locks: Dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()  # Lock for creating locks
        logger.debug("HookExecutor initialized with config")

    async def _get_hook_lock(self, hook_key: str) -> asyncio.Lock:
        """Get or create a lock for a specific hook.

        Args:
            hook_key: The hook identifier (plugin_name.hook_name)

        Returns:
            asyncio.Lock for the specified hook
        """
        if hook_key not in self._hook_locks:
            async with self._locks_lock:
                # Double-check after acquiring lock
                if hook_key not in self._hook_locks:
                    self._hook_locks[hook_key] = asyncio.Lock()
        return self._hook_locks[hook_key]

    async def execute_hook(self, hook: Hook, event: Event) -> Dict[str, Any]:
        """Execute a single hook with error handling, timeout, and retry logic.

        Thread-safe: Uses per-hook locks to prevent concurrent execution of the
        same hook, avoiding race conditions on hook status mutations.

        Args:
            hook: The hook to execute.
            event: The event being processed.

        Returns:
            Dictionary with execution result and metadata.
        """
        hook_key = f"{hook.plugin_name}.{hook.name}"
        result_metadata: Dict[str, Any] = {
            "hook_key": hook_key,
            "success": False,
            "result": None,
            "error": None,
            "duration_ms": 0,
            "retry_count": 0,
            "attempts": [],
        }

        if not hook.enabled:
            result_metadata["error"] = "hook_disabled"
            logger.debug(f"Skipping disabled hook: {hook_key}")
            return result_metadata

        if event.cancelled:
            result_metadata["error"] = "event_cancelled"
            logger.debug(f"Skipping hook due to cancelled event: {hook_key}")
            return result_metadata

        # Acquire lock to prevent concurrent execution of the same hook
        hook_lock = await self._get_hook_lock(hook_key)
        async with hook_lock:
            # Apply fallback defaults if values are None (for hooks created without registration)
            hooks_config = self.config.get("hooks", {})
            timeout = (
                hook.timeout
                if hook.timeout is not None
                else hooks_config.get("default_timeout", DEFAULT_HOOK_TIMEOUT)
            )
            retry_attempts = (
                hook.retry_attempts
                if hook.retry_attempts is not None
                else hooks_config.get("default_retries", DEFAULT_HOOK_RETRIES)
            )
            error_action = (
                hook.error_action
                if hook.error_action is not None
                else hooks_config.get("default_error_action", DEFAULT_ERROR_ACTION)
            )

            # Validate and clamp timeout
            if not isinstance(timeout, (int, float)) or timeout < MIN_TIMEOUT:
                logger.warning(
                    f"Invalid timeout {timeout} for {hook_key}, using minimum: {MIN_TIMEOUT}"
                )
                timeout = MIN_TIMEOUT
            elif timeout > MAX_TIMEOUT:
                logger.warning(
                    f"Timeout {timeout} exceeds maximum for {hook_key}, capping at: {MAX_TIMEOUT}"
                )
                timeout = MAX_TIMEOUT

            # Validate and cap retry attempts
            if not isinstance(retry_attempts, int) or retry_attempts < 0:
                logger.warning(
                    f"Invalid retry_attempts {retry_attempts} for {hook_key}, using default: {DEFAULT_HOOK_RETRIES}"
                )
                retry_attempts = DEFAULT_HOOK_RETRIES
            elif retry_attempts > ABSOLUTE_MAX_RETRIES:
                logger.warning(
                    f"Retry attempts {retry_attempts} exceeds absolute maximum for {hook_key}, "
                    f"capping at: {ABSOLUTE_MAX_RETRIES}"
                )
                retry_attempts = ABSOLUTE_MAX_RETRIES

            # Validate error_action
            if error_action not in VALID_ERROR_ACTIONS:
                logger.error(
                    f"Invalid error_action '{error_action}' for {hook_key}, "
                    f"must be one of {VALID_ERROR_ACTIONS}. Using default: '{DEFAULT_ERROR_ACTION}'"
                )
                error_action = DEFAULT_ERROR_ACTION

            # Track overall execution time
            overall_start = time.time()

            # Retry loop with exponential backoff and absolute timeout
            max_attempts = retry_attempts + 1  # Initial attempt + retries
            for attempt in range(max_attempts):
                # Check if we've exceeded the absolute maximum retry duration
                elapsed_time = time.time() - overall_start
                if elapsed_time > MAX_TOTAL_RETRY_DURATION:
                    logger.error(
                        f"Hook {hook_key} exceeded maximum total retry duration "
                        f"({MAX_TOTAL_RETRY_DURATION}s). Aborting after {attempt} attempts."
                    )
                    result_metadata["error"] = "max_retry_duration_exceeded"
                    result_metadata["retry_count"] = attempt
                    break

                attempt_start = time.time()
                attempt_info: Dict[str, Any] = {
                    "attempt": attempt + 1,
                    "duration_ms": 0,
                    "success": False,
                    "error": None,
                }

                try:
                    # Update hook status to working
                    hook.status = HookStatus.WORKING

                    # Execute hook with timeout
                    result = await asyncio.wait_for(
                        hook.callback(event.data, event), timeout=timeout
                    )

                    # Calculate attempt execution time
                    attempt_end = time.time()
                    attempt_info["duration_ms"] = max(
                        1, int((attempt_end - attempt_start) * 1000)
                    )
                    attempt_info["success"] = True
                    result_metadata["attempts"].append(attempt_info)

                    # Mark as successful
                    hook.status = HookStatus.COMPLETED
                    result_metadata["success"] = True
                    result_metadata["result"] = result
                    result_metadata["retry_count"] = attempt

                    # Handle data transformation if hook returns modified data
                    if isinstance(result, dict) and "data" in result:
                        self._apply_data_transformation(event, result["data"])
                        logger.debug(f"Hook {hook_key} modified event data")

                    # Success - break out of retry loop
                    break

                except asyncio.TimeoutError:
                    attempt_end = time.time()
                    attempt_info["duration_ms"] = max(
                        1, int((attempt_end - attempt_start) * 1000)
                    )
                    attempt_info["error"] = "timeout"
                    result_metadata["attempts"].append(attempt_info)

                    hook.status = HookStatus.TIMEOUT
                    logger.warning(
                        f"Hook {hook_key} timed out after {timeout}s (attempt {attempt + 1}/{max_attempts})"
                    )

                    # On final attempt, mark as error
                    if attempt == max_attempts - 1:
                        result_metadata["error"] = "timeout"
                        result_metadata["retry_count"] = attempt

                        # Handle timeout based on error action
                        if error_action == "stop":
                            event.cancelled = True
                            logger.info(
                                f"Event cancelled due to hook timeout: {hook_key}"
                            )
                    else:
                        # Wait before retry with exponential backoff
                        backoff_delay = min(2**attempt, 30)  # Max 30 seconds
                        logger.debug(f"Retrying hook {hook_key} in {backoff_delay}s")
                        await asyncio.sleep(backoff_delay)

                except Exception as e:
                    attempt_end = time.time()
                    attempt_info["duration_ms"] = max(
                        1, int((attempt_end - attempt_start) * 1000)
                    )
                    attempt_info["error"] = str(e)
                    result_metadata["attempts"].append(attempt_info)

                    hook.status = HookStatus.FAILED
                    log_and_continue(
                        logger,
                        f"executing hook {hook_key} (attempt {attempt + 1}/{max_attempts})",
                        e,
                    )

                    # On final attempt, mark as error
                    if attempt == max_attempts - 1:
                        result_metadata["error"] = str(e)
                        result_metadata["retry_count"] = attempt

                        # Handle error based on error action
                        if error_action == "stop":
                            event.cancelled = True
                            logger.info(
                                f"Event cancelled due to hook error: {hook_key}"
                            )
                    else:
                        # Wait before retry with exponential backoff
                        backoff_delay = min(2**attempt, 30)  # Max 30 seconds
                        logger.debug(
                            f"Retrying hook {hook_key} in {backoff_delay}s after error: {e}"
                        )
                        await asyncio.sleep(backoff_delay)

            # Calculate total execution time including retries
            overall_end = time.time()
            result_metadata["duration_ms"] = max(
                1, int((overall_end - overall_start) * 1000)
            )

        return result_metadata

    def _apply_data_transformation(
        self, event: Event, hook_data: Dict[str, Any]
    ) -> None:
        """Apply data transformation from hook result to event.

        Args:
            event: The event to modify.
            hook_data: Data transformation from hook.
        """
        try:
            if isinstance(hook_data, dict):
                event.data.update(hook_data)
            else:
                logger.warning(
                    f"Hook returned non-dict data transformation: {type(hook_data)}"
                )
        except Exception as e:
            log_and_continue(logger, "applying hook data transformation", e)

    def get_execution_stats(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get execution statistics from a list of hook results.

        Args:
            results: List of hook execution results.

        Returns:
            Dictionary with execution statistics.
        """
        if not results:
            return {
                "total_hooks": 0,
                "successful": 0,
                "failed": 0,
                "timed_out": 0,
                "total_duration_ms": 0,
                "avg_duration_ms": 0,
            }

        successful = sum(1 for r in results if r.get("success", False))
        failed = sum(
            1
            for r in results
            if r.get("error")
            and r["error"] not in ["timeout", "hook_disabled", "event_cancelled"]
        )
        timed_out = sum(1 for r in results if r.get("error") == "timeout")
        total_duration = sum(r.get("duration_ms", 0) for r in results)

        return {
            "total_hooks": len(results),
            "successful": successful,
            "failed": failed,
            "timed_out": timed_out,
            "total_duration_ms": total_duration,
            "avg_duration_ms": int(total_duration / len(results)) if results else 0,
        }
