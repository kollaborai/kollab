"""Error handling and recovery mechanisms for input processing.

This module provides centralized error handling, error recovery strategies,
and error storm detection for the input processing subsystem.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """Severity levels for input errors."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorType(Enum):
    """Types of input errors."""

    IO_ERROR = "io_error"
    PARSING_ERROR = "parsing_error"
    BUFFER_ERROR = "buffer_error"
    EVENT_ERROR = "event_error"
    TIMEOUT_ERROR = "timeout_error"
    VALIDATION_ERROR = "validation_error"
    SYSTEM_ERROR = "system_error"


@dataclass
class InputError:
    """Represents an input handling error."""

    type: ErrorType
    severity: ErrorSeverity
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    context: Dict[str, Any] = field(default_factory=dict)
    resolved: bool = False
    retry_count: int = 0


class ErrorRecoveryStrategy:
    """Base class for error recovery strategies."""

    def can_handle(self, error: InputError) -> bool:
        """Check if this strategy can handle the error.

        Args:
            error: The error to potentially handle.

        Returns:
            True if this strategy can handle the error.
        """
        raise NotImplementedError

    async def recover(self, error: InputError, context: Dict[str, Any]) -> bool:
        """Attempt to recover from the error.

        Args:
            error: The error to recover from.
            context: Additional context for recovery.

        Returns:
            True if recovery was successful.
        """
        raise NotImplementedError


class IOErrorRecovery(ErrorRecoveryStrategy):
    """Recovery strategy for I/O errors."""

    def can_handle(self, error: InputError) -> bool:
        return error.type == ErrorType.IO_ERROR

    async def recover(self, error: InputError, context: Dict[str, Any]) -> bool:
        """Recover from I/O errors by waiting and resetting."""
        if error.retry_count >= 3:
            logger.error("IO error recovery failed after 3 attempts")
            return False

        # Exponential backoff
        wait_time = min(0.1 * (2**error.retry_count), 1.0)
        await asyncio.sleep(wait_time)

        logger.info(
            f"Attempting IO error recovery " f"(attempt {error.retry_count + 1})"
        )
        return True


class BufferErrorRecovery(ErrorRecoveryStrategy):
    """Recovery strategy for buffer errors."""

    def can_handle(self, error: InputError) -> bool:
        return error.type == ErrorType.BUFFER_ERROR

    async def recover(self, error: InputError, context: Dict[str, Any]) -> bool:
        """Recover from buffer errors by resetting buffer if needed."""
        buffer_manager = context.get("buffer_manager")
        if not buffer_manager:
            return False

        if error.severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
            # Clear buffer on severe errors
            buffer_manager.clear()
            logger.warning("Buffer cleared due to severe error")

        return True


class EventErrorRecovery(ErrorRecoveryStrategy):
    """Recovery strategy for event system errors."""

    def can_handle(self, error: InputError) -> bool:
        return error.type == ErrorType.EVENT_ERROR

    async def recover(self, error: InputError, context: Dict[str, Any]) -> bool:
        """Recover from event system errors."""
        if error.severity == ErrorSeverity.CRITICAL:
            logger.error("Critical event error - recovery not possible")
            return False

        # For non-critical event errors, just log and continue
        logger.warning(f"Event error recovered: {error.message}")
        return True


class InputErrorHandler:
    """Centralized error handling and recovery system for input processing."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the error handler.

        Args:
            config: Configuration for error handling behavior.
        """
        self.config = config or {}
        self._errors: List[InputError] = []
        self._error_counts: Dict[ErrorType, int] = {}
        self._last_error_time: Optional[datetime] = None
        self._error_threshold: int = self.config.get("error_threshold", 10)
        self._error_window: timedelta = timedelta(
            minutes=self.config.get("error_window_minutes", 5)
        )
        self._max_errors: int = self.config.get("max_errors", 100)

        # Initialize recovery strategies
        self._recovery_strategies: List[ErrorRecoveryStrategy] = [
            IOErrorRecovery(),
            BufferErrorRecovery(),
            EventErrorRecovery(),
        ]

        logger.info("InputErrorHandler initialized")

    def add_recovery_strategy(self, strategy: ErrorRecoveryStrategy) -> None:
        """Add a custom recovery strategy.

        Args:
            strategy: Recovery strategy to add.
        """
        self._recovery_strategies.append(strategy)
        logger.debug(f"Added recovery strategy: {strategy.__class__.__name__}")

    async def handle_error(
        self,
        error_type: ErrorType,
        message: str,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Handle an input error.

        Args:
            error_type: Type of error that occurred.
            message: Error message.
            severity: Severity level of the error.
            context: Additional context for error handling.

        Returns:
            True if error was handled successfully.
        """
        error = InputError(
            type=error_type,
            severity=severity,
            message=message,
            context=context or {},
        )

        # Record error
        self._record_error(error)

        # Check for error storm
        if self._is_error_storm():
            logger.critical("Error storm detected - input system may be unstable")
            return False

        # Attempt recovery
        return await self._attempt_recovery(error, context or {})

    def _record_error(self, error: InputError) -> None:
        """Record an error in the error log.

        Args:
            error: Error to record.
        """
        self._errors.append(error)
        self._error_counts[error.type] = self._error_counts.get(error.type, 0) + 1
        self._last_error_time = error.timestamp

        # Maintain error log size
        if len(self._errors) > self._max_errors:
            self._errors = self._errors[-self._max_errors :]

        severity_val = getattr(error.severity, "value", error.severity)
        type_val = getattr(error.type, "value", error.type)
        logger.debug(f"Recorded {severity_val} {type_val}: {error.message}")

    def _is_error_storm(self) -> bool:
        """Check if we're experiencing an error storm.

        Returns:
            True if error storm is detected.
        """
        if not self._last_error_time:
            return False

        # Count recent errors
        cutoff_time = datetime.now() - self._error_window
        recent_errors = [e for e in self._errors if e.timestamp >= cutoff_time]

        return len(recent_errors) >= self._error_threshold

    async def _attempt_recovery(
        self, error: InputError, context: Dict[str, Any]
    ) -> bool:
        """Attempt to recover from an error using available strategies.

        Args:
            error: Error to recover from.
            context: Context for recovery.

        Returns:
            True if recovery was successful.
        """
        for strategy in self._recovery_strategies:
            if strategy.can_handle(error):
                try:
                    error.retry_count += 1
                    success = bool(await strategy.recover(error, context))

                    if success:
                        error.resolved = True
                        strategy_name = strategy.__class__.__name__
                        logger.debug(f"Error recovered using {strategy_name}")
                        return True
                    else:
                        strategy_name = strategy.__class__.__name__
                        logger.warning(f"Recovery failed using {strategy_name}")

                except Exception as e:
                    logger.error(f"Recovery strategy failed: {e}")

        error_type_val = getattr(error.type, "value", error.type)
        logger.error(f"No recovery strategy could handle {error_type_val} error")
        return False

    def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics.

        Returns:
            Dictionary containing error statistics.
        """
        recent_cutoff = datetime.now() - timedelta(minutes=5)
        recent_errors = [e for e in self._errors if e.timestamp >= recent_cutoff]

        last_error_iso = (
            self._last_error_time.isoformat() if self._last_error_time else None
        )
        return {
            "total_errors": len(self._errors),
            "recent_errors": len(recent_errors),
            "error_counts": dict(self._error_counts),
            "last_error": last_error_iso,
            "resolved_errors": len([e for e in self._errors if e.resolved]),
            "unresolved_errors": len([e for e in self._errors if not e.resolved]),
        }

    def clear_old_errors(self, max_age_hours: int = 24) -> int:
        """Clear old errors from the log.

        Args:
            max_age_hours: Maximum age of errors to keep in hours.

        Returns:
            Number of errors cleared.
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        initial_count = len(self._errors)

        self._errors = [e for e in self._errors if e.timestamp >= cutoff_time]

        cleared_count = initial_count - len(self._errors)
        if cleared_count > 0:
            logger.info(f"Cleared {cleared_count} old errors")

        return cleared_count
