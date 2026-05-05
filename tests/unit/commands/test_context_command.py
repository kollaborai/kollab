"""Unit tests for ContextCommandHandler.

Verifies that the handler correctly receives a SlashCommand object
(not a raw string) and routes subcommands properly.

The bug this catches: handler signature was `_handle_context(self, args: str)`
but the executor passes a SlashCommand object, causing AttributeError on .strip().
"""

import asyncio
import unittest
from unittest.mock import MagicMock


def _make_command(*args):
    """Build a SlashCommand with the given args list."""
    from kollabor_events.models import SlashCommand

    cmd = SlashCommand.__new__(SlashCommand)
    cmd.name = "context"
    cmd.args = list(args)
    cmd.raw_input = "/context " + " ".join(args) if args else "/context"
    cmd.parameters = {}
    return cmd


class _FakeService:
    """Minimal context service stub that bypasses the unittest.mock module guard."""

    def __init__(self):
        self.build_context_snapshot_display = MagicMock(return_value="snapshot")
        self.all_entries = MagicMock(return_value=[])
        self.evict = MagicMock(return_value=True)
        ledger = MagicMock()
        ledger._entries = MagicMock()
        self._ledger = ledger


def _make_handler(svc=None):
    """Return (handler, fake_service) with a wired event bus."""
    from kollabor.commands.system_commands.handlers.context import ContextCommandHandler

    fake_svc = svc if svc is not None else _FakeService()
    event_bus = MagicMock()
    event_bus.get_service.return_value = fake_svc
    handler = ContextCommandHandler(
        command_registry=MagicMock(),
        event_bus=event_bus,
    )
    return handler, fake_svc


def _run(coro):
    return asyncio.run(coro)


class TestContextCommandHandler(unittest.TestCase):
    """Handler must accept a SlashCommand object and route subcommands correctly."""

    def test_no_subcommand_calls_show(self):
        handler, svc = _make_handler()
        result = _run(handler._handle_context(_make_command()))
        self.assertTrue(result.success)
        svc.build_context_snapshot_display.assert_called_once()

    def test_show_subcommand(self):
        handler, svc = _make_handler()
        result = _run(handler._handle_context(_make_command("show")))
        self.assertTrue(result.success)
        svc.build_context_snapshot_display.assert_called_once()

    def test_stats_subcommand(self):
        entry = MagicMock()
        entry.decision = "keep"
        entry.size_bytes = 2048
        svc = _FakeService()
        svc.all_entries.return_value = [entry]
        handler, _ = _make_handler(svc)

        result = _run(handler._handle_context(_make_command("stats")))

        self.assertTrue(result.success)
        self.assertIn("entries", result.message)

    def test_evict_subcommand(self):
        handler, svc = _make_handler()
        result = _run(handler._handle_context(_make_command("evict", "ctx-1")))
        self.assertTrue(result.success)
        svc.evict.assert_called_once_with("ctx-1", "user requested eviction")

    def test_evict_with_reason(self):
        handler, svc = _make_handler()
        result = _run(handler._handle_context(_make_command("evict", "ctx-2", "too large")))
        self.assertTrue(result.success)
        svc.evict.assert_called_once_with("ctx-2", "too large")

    def test_evict_missing_id_returns_error(self):
        handler, _ = _make_handler()
        result = _run(handler._handle_context(_make_command("evict")))
        self.assertFalse(result.success)
        self.assertIn("usage", result.message)

    def test_evict_not_found_returns_error(self):
        svc = _FakeService()
        svc.evict.return_value = False
        handler, _ = _make_handler(svc)
        result = _run(handler._handle_context(_make_command("evict", "ctx-99")))
        self.assertFalse(result.success)
        self.assertIn("ctx-99", result.message)

    def test_clear_subcommand(self):
        handler, svc = _make_handler()
        result = _run(handler._handle_context(_make_command("clear")))
        self.assertTrue(result.success)
        svc._ledger._entries.clear.assert_called_once()

    def test_unknown_subcommand_returns_error(self):
        handler, _ = _make_handler()
        result = _run(handler._handle_context(_make_command("bogus")))
        self.assertFalse(result.success)
        self.assertIn("unknown subcommand", result.message)

    def test_no_context_service_returns_error(self):
        from kollabor.commands.system_commands.handlers.context import ContextCommandHandler

        event_bus = MagicMock()
        event_bus.get_service.return_value = None
        handler = ContextCommandHandler(
            command_registry=MagicMock(),
            event_bus=event_bus,
        )
        result = _run(handler._handle_context(_make_command("show")))
        self.assertFalse(result.success)
        self.assertIn("not running", result.message)


if __name__ == "__main__":
    unittest.main()
