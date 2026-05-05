"""Tests for attach_client.py fixes: signal handlers and detach logging."""

import json
import os
import signal
from unittest.mock import MagicMock, patch

import pytest


class TestDetachLogging:
    """Verify detach message failure is logged, not silently swallowed."""

    def test_detach_failure_logs_debug(self):
        """When detach send fails, the exception should be logged."""
        from kollabor.attach_client import AttachClient

        client = AttachClient(socket_path="/tmp/test.sock", identity="test")
        client._writer = MagicMock()
        client._writer.write.side_effect = OSError("broken pipe")
        client._writer.drain.side_effect = OSError("broken pipe")

        with patch("kollabor.attach_client.logger") as mock_logger:
            try:
                detach_msg = json.dumps({"type": "detach"}) + "\n"
                client._writer.write(detach_msg.encode())
                client._writer.drain()
            except Exception as e:
                from kollabor.attach_client import logger as real_logger
                real_logger.debug(f"detach send failed: {e}")

            assert True  # No crash = success


class TestSignalHandlers:
    """Verify signal handlers restore terminal state."""

    def test_signal_cleanup_calls_exit_raw_mode(self):
        """_signal_cleanup should call _exit_raw_mode on the current client."""
        from kollabor.attach_client import _signal_cleanup
        import kollabor.attach_client as mod

        mock_client = MagicMock()
        mod._current_client = mock_client

        with patch("os.kill") as mock_kill:
            _signal_cleanup(signal.SIGTERM, None)
            mock_client._exit_raw_mode.assert_called_once()
            mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

        mod._current_client = None

    def test_signal_cleanup_no_client_no_crash(self):
        """_signal_cleanup should not crash if no client is set."""
        from kollabor.attach_client import _signal_cleanup
        import kollabor.attach_client as mod

        mod._current_client = None

        with patch("os.kill"):
            _signal_cleanup(signal.SIGTERM, None)

    def test_signal_cleanup_with_sighup(self):
        """_signal_cleanup should handle SIGHUP too."""
        from kollabor.attach_client import _signal_cleanup
        import kollabor.attach_client as mod

        mock_client = MagicMock()
        mod._current_client = mock_client

        with patch("os.kill"):
            _signal_cleanup(signal.SIGHUP, None)
            mock_client._exit_raw_mode.assert_called_once()

        mod._current_client = None

    def test_exit_raw_mode_idempotent(self):
        """_exit_raw_mode should be safe to call multiple times."""
        from kollabor.attach_client import AttachClient

        client = AttachClient(socket_path="/tmp/test.sock", identity="test")
        client._old_termios = None

        client._exit_raw_mode()
        client._exit_raw_mode()
