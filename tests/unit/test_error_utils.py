"""Tests for error utility functions."""

import asyncio
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_events.error_utils import (
    ErrorAccumulator,
    handle_startup_errors,
    log_and_continue,
    retry_on_failure,
    safe_execute,
    safe_execute_async,
    validate_and_log,
)


class TestErrorUtils(unittest.TestCase):
    """Test cases for error utilities."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_logger = MagicMock(spec=logging.Logger)

    def test_log_and_continue(self):
        """Test error logging without raising."""
        exception = Exception("Test error")

        log_and_continue(self.mock_logger, "test operation", exception)

        self.mock_logger.error.assert_called_once_with(
            "Failed test operation: Test error"
        )

    def test_safe_execute_success(self):
        """Test safe_execute with successful function."""

        def success_func():
            return "success"

        result = safe_execute(success_func, "test operation", default="failed")
        self.assertEqual(result, "success")

    def test_safe_execute_failure(self):
        """Test safe_execute with failing function."""

        def failure_func():
            raise Exception("Function failed")

        result = safe_execute(
            failure_func,
            "test operation",
            default="failed",
            logger_instance=self.mock_logger,
        )

        self.assertEqual(result, "failed")
        self.mock_logger.error.assert_called_once_with(
            "test operation: Function failed"
        )

    def test_safe_execute_async_success(self):
        """Test async safe_execute with successful function."""

        async def success_func():
            return "async_success"

        async def test_async():
            result = await safe_execute_async(
                success_func, "async operation", default="failed"
            )
            self.assertEqual(result, "async_success")

        asyncio.run(test_async())

    def test_safe_execute_async_timeout(self):
        """Test async safe_execute with timeout."""

        async def slow_func():
            await asyncio.sleep(2.0)
            return "should_not_return"

        async def test_timeout():
            result = await safe_execute_async(
                slow_func,
                "slow operation",
                default="timed_out",
                logger_instance=self.mock_logger,
                timeout=0.1,
            )
            self.assertEqual(result, "timed_out")
            self.mock_logger.warning.assert_called_once()

        asyncio.run(test_timeout())

    def test_safe_execute_async_failure(self):
        """Test async safe_execute with failing function."""

        async def failure_func():
            raise Exception("Async function failed")

        async def test_failure():
            result = await safe_execute_async(
                failure_func,
                "async operation",
                default="failed",
                logger_instance=self.mock_logger,
            )
            self.assertEqual(result, "failed")
            self.mock_logger.error.assert_called_once_with(
                "async operation: Async function failed"
            )

        asyncio.run(test_failure())

    def test_retry_on_failure_success_first_try(self):
        """Test retry decorator with function that succeeds immediately."""

        @retry_on_failure(max_attempts=3, delay=0.01, logger_instance=self.mock_logger)
        def success_func():
            return "success"

        result = success_func()
        self.assertEqual(result, "success")

        # No warnings should be logged for immediate success
        self.mock_logger.warning.assert_not_called()

    def test_retry_on_failure_eventual_success(self):
        """Test retry decorator with function that succeeds after retries."""
        call_count = 0

        @retry_on_failure(max_attempts=3, delay=0.01, logger_instance=self.mock_logger)
        def eventually_success_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception(f"Attempt {call_count} failed")
            return "success"

        result = eventually_success_func()
        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)

        # Should log warnings for failed attempts
        self.assertEqual(self.mock_logger.warning.call_count, 2)

    def test_retry_on_failure_all_attempts_fail(self):
        """Test retry decorator when all attempts fail."""

        @retry_on_failure(max_attempts=2, delay=0.01, logger_instance=self.mock_logger)
        def always_fail_func():
            raise Exception("Always fails")

        with self.assertRaises(Exception) as context:
            always_fail_func()

        self.assertEqual(str(context.exception), "Always fails")
        self.mock_logger.error.assert_called_once()
        self.mock_logger.warning.assert_called_once()

    def test_error_accumulator_basic(self):
        """Test basic error accumulator functionality."""
        accumulator = ErrorAccumulator(self.mock_logger)

        # Initially no errors
        self.assertFalse(accumulator.has_errors())
        self.assertFalse(accumulator.has_warnings())
        self.assertEqual(accumulator.get_summary(), "No issues")

        # Add error
        accumulator.add_error("test operation", "Something went wrong")
        self.assertTrue(accumulator.has_errors())
        self.assertEqual(len(accumulator.errors), 1)

        # Add warning
        accumulator.add_warning("another operation", Exception("Warning message"))
        self.assertTrue(accumulator.has_warnings())
        self.assertEqual(len(accumulator.warnings), 1)

    def test_error_accumulator_summary(self):
        """Test error accumulator summary generation."""
        accumulator = ErrorAccumulator(self.mock_logger)

        accumulator.add_error("op1", "Error 1")
        accumulator.add_error("op2", "Error 2")
        accumulator.add_warning("op3", "Warning 1")

        summary = accumulator.get_summary()
        self.assertIn("2 errors", summary)
        self.assertIn("1 warnings", summary)

    def test_error_accumulator_report(self):
        """Test error accumulator report generation."""
        accumulator = ErrorAccumulator(self.mock_logger)

        accumulator.add_error("operation1", "Error message")
        accumulator.add_warning("operation2", "Warning message")
        accumulator.report_summary()

        # Should log error summary
        error_calls = [call for call in self.mock_logger.error.call_args_list]
        self.assertTrue(any("1 errors" in str(call) for call in error_calls))

        # Should log warning summary
        warning_calls = [call for call in self.mock_logger.warning.call_args_list]
        self.assertTrue(any("1 warnings" in str(call) for call in warning_calls))

    def test_handle_startup_errors_success(self):
        """Test startup error handler with successful function."""

        @handle_startup_errors("test startup", self.mock_logger)
        def successful_startup():
            return "startup_complete"

        result = successful_startup()
        self.assertEqual(result, "startup_complete")
        self.mock_logger.error.assert_not_called()

    def test_handle_startup_errors_failure(self):
        """Test startup error handler with failing function."""

        @handle_startup_errors("test startup", self.mock_logger)
        def failing_startup():
            raise Exception("Startup failed")

        result = failing_startup()
        self.assertIsNone(result)

        # Should log error but not crash
        self.mock_logger.error.assert_called_once()
        self.mock_logger.info.assert_called_once()

    def test_validate_and_log_success(self):
        """Test validation with passing condition."""
        result = validate_and_log(True, "Should not log", self.mock_logger)
        self.assertTrue(result)
        self.mock_logger.error.assert_not_called()

    def test_validate_and_log_failure(self):
        """Test validation with failing condition."""
        result = validate_and_log(False, "Validation failed", self.mock_logger)
        self.assertFalse(result)
        self.mock_logger.error.assert_called_once_with("Validation failed")

    def test_validate_and_log_raise_on_failure(self):
        """Test validation with raise_on_failure=True."""
        with self.assertRaises(ValueError) as context:
            validate_and_log(
                False, "Validation failed", self.mock_logger, raise_on_failure=True
            )

        self.assertEqual(str(context.exception), "Validation failed")
        self.mock_logger.error.assert_called_once_with("Validation failed")


if __name__ == "__main__":
    unittest.main()
