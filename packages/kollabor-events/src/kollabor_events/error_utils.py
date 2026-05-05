"""Error handling utility functions for consistent error management."""

import asyncio
import functools
import logging
from typing import Callable, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar("T")


def log_and_continue(
    operation_logger: logging.Logger, operation: str, exception: Exception
) -> None:
    """Log an error and continue execution.

    Standardized error logging format for non-fatal errors.

    Args:
        operation_logger: Logger to use for error message.
        operation: Description of the operation that failed.
        exception: Exception that occurred.

    Example:
        >>> try:
        ...     risky_operation()
        ... except Exception as e:
        ...     log_and_continue(logger, "loading plugin config", e)
    """
    operation_logger.error(f"Failed {operation}: {exception}")


def safe_execute(
    func: Callable[[], T],
    error_msg: str,
    default: Optional[T] = None,
    logger_instance: Optional[logging.Logger] = None,
) -> T:
    """Execute function with error handling and default return value.

    Args:
        func: Function to execute.
        error_msg: Error message to log if function fails.
        default: Default value to return on error.
        logger_instance: Logger to use (defaults to module logger).

    Returns:
        Function result or default value on error.

    Example:
        >>> def risky_function():
        ...     return 1 / 0
        >>> result = safe_execute(risky_function, "dividing by zero", default=42)
        >>> result  # Returns 42 since function raises exception
        42
    """
    log = logger_instance or logger

    try:
        return func()
    except Exception as e:
        log.error(f"{error_msg}: {e}")
        return default  # type: ignore[return-value]


async def safe_execute_async(
    func: Callable[[], T],
    error_msg: str,
    default: Optional[T] = None,
    logger_instance: Optional[logging.Logger] = None,
    timeout: Optional[float] = None,
) -> T:
    """Execute async function with error handling and default return value.

    Args:
        func: Async function to execute.
        error_msg: Error message to log if function fails.
        default: Default value to return on error.
        logger_instance: Logger to use (defaults to module logger).
        timeout: Optional timeout in seconds.

    Returns:
        Function result or default value on error/timeout.

    Example:
        >>> async def risky_async_function():
        ...     await asyncio.sleep(10)  # Long operation
        >>> result = await safe_execute_async(
        ...     risky_async_function,
        ...     "long operation",
        ...     default="timed_out",
        ...     timeout=1.0
        ... )
        >>> result  # Returns "timed_out" due to timeout
        "timed_out"
    """
    log = logger_instance or logger

    try:
        if timeout is not None:
            return await asyncio.wait_for(func(), timeout=timeout)  # type: ignore[arg-type]
        else:
            return await func()  # type: ignore[misc, no-any-return]
    except asyncio.TimeoutError:
        log.warning(f"{error_msg}: operation timed out after {timeout}s")
        return default  # type: ignore[return-value]
    except Exception as e:
        log.error(f"{error_msg}: {e}")
        return default  # type: ignore[return-value]


def retry_on_failure(
    max_attempts: int = 3,
    delay: float = 0.1,
    backoff_multiplier: float = 2.0,
    logger_instance: Optional[logging.Logger] = None,
):
    """Decorator to retry function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts.
        delay: Initial delay between attempts in seconds.
        backoff_multiplier: Multiplier for delay after each failure.
        logger_instance: Logger to use for retry messages.

    Example:
        >>> @retry_on_failure(max_attempts=3, delay=0.1)
        ... def flaky_function():
        ...     import random
        ...     if random.random() < 0.7:  # 70% chance of failure
        ...         raise Exception("Random failure")
        ...     return "success"
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            log = logger_instance or logger
            current_delay = delay

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        # Last attempt, re-raise the exception
                        log.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    else:
                        log.warning(
                            f"Function {func.__name__} failed (attempt {attempt + 1}/{max_attempts}): {e}"
                        )
                        if current_delay > 0:
                            import time

                            time.sleep(current_delay)
                            current_delay *= backoff_multiplier

        return wrapper

    return decorator


class ErrorAccumulator:
    """Accumulate errors during batch operations for later reporting."""

    def __init__(self, logger_instance: Optional[logging.Logger] = None):
        """Initialize error accumulator.

        Args:
            logger_instance: Logger to use for error reporting.
        """
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.logger = logger_instance or logger

    def add_error(self, operation: str, error: Union[str, Exception]) -> None:
        """Add an error to the accumulator.

        Args:
            operation: Description of operation that failed.
            error: Error message or exception.
        """
        error_msg = str(error)
        self.errors.append(f"{operation}: {error_msg}")
        self.logger.error(f"Accumulated error - {operation}: {error_msg}")

    def add_warning(self, operation: str, warning: Union[str, Exception]) -> None:
        """Add a warning to the accumulator.

        Args:
            operation: Description of operation that had issues.
            warning: Warning message or exception.
        """
        warning_msg = str(warning)
        self.warnings.append(f"{operation}: {warning_msg}")
        self.logger.warning(f"Accumulated warning - {operation}: {warning_msg}")

    def has_errors(self) -> bool:
        """Check if any errors were accumulated."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if any warnings were accumulated."""
        return len(self.warnings) > 0

    def get_summary(self) -> str:
        """Get summary of accumulated errors and warnings."""
        parts = []
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")

        if not parts:
            return "No issues"

        return ", ".join(parts)

    def report_summary(self) -> None:
        """Log a summary of accumulated errors and warnings."""
        if self.errors:
            self.logger.error(
                f"Batch operation completed with {len(self.errors)} errors:"
            )
            for error in self.errors:
                self.logger.error(f"  - {error}")

        if self.warnings:
            self.logger.warning(
                f"Batch operation completed with {len(self.warnings)} warnings:"
            )
            for warning in self.warnings:
                self.logger.warning(f"  - {warning}")

        if not self.errors and not self.warnings:
            self.logger.info("Batch operation completed successfully")


def handle_startup_errors(
    operation_name: str, logger_instance: Optional[logging.Logger] = None
):
    """Decorator for startup operations that should not crash the application.

    Args:
        operation_name: Name of the startup operation.
        logger_instance: Logger to use for error reporting.

    Example:
        >>> @handle_startup_errors("plugin initialization")
        ... def initialize_plugin():
        ...     # Plugin initialization code that might fail
        ...     pass
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            log = logger_instance or logger
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log.error(f"Startup operation '{operation_name}' failed: {e}")
                log.info(f"Continuing startup despite {operation_name} failure")
                return None

        return wrapper

    return decorator


def validate_and_log(
    condition: bool,
    error_msg: str,
    logger_instance: Optional[logging.Logger] = None,
    raise_on_failure: bool = False,
) -> bool:
    """Validate condition and log error if validation fails.

    Args:
        condition: Condition to validate.
        error_msg: Error message to log if condition is False.
        logger_instance: Logger to use for error reporting.
        raise_on_failure: Whether to raise exception on validation failure.

    Returns:
        True if condition is True, False otherwise.

    Raises:
        ValueError: If condition is False and raise_on_failure is True.

    Example:
        >>> validate_and_log(len("test") > 10, "String too short")
        False  # Logs error and returns False
        >>> validate_and_log(len("test") > 0, "String empty")
        True   # Returns True, no logging
    """
    if not condition:
        log = logger_instance or logger
        log.error(error_msg)
        if raise_on_failure:
            raise ValueError(error_msg)
        return False
    return True
