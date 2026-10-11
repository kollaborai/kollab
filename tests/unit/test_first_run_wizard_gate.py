"""Unit tests for the first-run setup-wizard trigger.

The wizard fires when there is no usable LLM provider (true first install OR a
config that exists but has no provider), and stays quiet for env-var users
(their key auto-activates a profile, so ``is_provider_available()`` is True),
for attach mode, and once setup has been completed/skipped.

It opens what typing ``/setup`` opens: the ``setup`` command's handler. An
earlier version looked up a fullscreen ``setup`` plugin that no longer exists, so
a stubbed lookup passed here while real first launches never saw the wizard.

See ``TerminalLLMChat._check_first_run_wizard`` in ``kollabor/application.py``.
"""

import asyncio
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor.application import TerminalLLMChat
from kollabor_events.models import CommandResult
from kollabor_tui.altview.stack_manager import AltViewUnavailable


def _make_app(
    *,
    attach=None,
    setup_completed=False,
    provider_available=False,
    first_install=True,
    registry=None,
    probe_raises=False,
):
    """Build a minimal stub carrying only what the gate reads."""
    app = types.SimpleNamespace()
    app._attach_to = attach
    app._is_first_install = first_install

    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: (
        setup_completed if key == "application.setup_completed" else default
    )
    cfg.save_key = MagicMock()
    app.config = cfg

    api = MagicMock()
    if probe_raises:
        api.is_provider_available.side_effect = RuntimeError("probe boom")
    else:
        api.is_provider_available.return_value = provider_available
    app.llm_service = types.SimpleNamespace(api_service=api)

    if registry is None:
        registry = MagicMock()
        registry.get_command.side_effect = lambda name: (
            app.setup_command if name == "setup" else None
        )
    app.setup_command = types.SimpleNamespace(
        handler=AsyncMock(return_value=CommandResult(success=True, message="ok"))
    )
    app.input_handler = types.SimpleNamespace(command_registry=registry)
    return app


def _run(app):
    asyncio.run(TerminalLLMChat._check_first_run_wizard(app))


class FirstRunWizardGateTests(unittest.TestCase):
    def test_fires_when_no_usable_provider(self):
        app = _make_app(provider_available=False)
        _run(app)
        app.setup_command.handler.assert_awaited_once()
        command = app.setup_command.handler.await_args.args[0]
        self.assertEqual((command.name, command.raw_input), ("setup", "/setup"))
        app.config.save_key.assert_called_with("application.setup_completed", True)

    def test_skips_when_provider_available_env_var_user(self):
        # Env-var keys auto-activate a profile -> provider available -> no nag.
        app = _make_app(provider_available=True, first_install=True)
        _run(app)
        app.setup_command.handler.assert_not_awaited()
        app.config.save_key.assert_not_called()

    def test_skips_in_attach_mode(self):
        app = _make_app(attach="unix:///tmp/sock", provider_available=False)
        _run(app)
        app.setup_command.handler.assert_not_awaited()

    def test_skips_when_setup_already_completed(self):
        app = _make_app(setup_completed=True, provider_available=False)
        _run(app)
        app.setup_command.handler.assert_not_awaited()

    def test_probe_failure_does_not_hijack_screen(self):
        app = _make_app(probe_raises=True)
        _run(app)  # must not raise
        app.setup_command.handler.assert_not_awaited()

    def test_graceful_when_setup_command_missing(self):
        registry = MagicMock()
        registry.get_command.return_value = None
        app = _make_app(provider_available=False, registry=registry)
        _run(app)  # must not raise
        app.setup_command.handler.assert_not_awaited()
        app.config.save_key.assert_not_called()

    def test_no_terminal_defers_setup_instead_of_marking_it_done(self):
        # A detached daemon or pipe process cannot show the wizard. Marking
        # setup completed there would hide it from the next real terminal.
        app = _make_app(provider_available=False)
        app.setup_command.handler.side_effect = AltViewUnavailable("no terminal")
        _run(app)
        app.config.save_key.assert_not_called()


class FirstRunWizardRealCommandTests(unittest.TestCase):
    def test_first_launch_pushes_the_setup_altview(self):
        """The real /setup handler, not a stand-in, receives the first-run call."""
        from kollabor.commands.registry import SlashCommandRegistry
        from kollabor.commands.system_commands.handlers.setup import (
            SetupCommandHandler,
        )

        stack = MagicMock(push=AsyncMock())
        event_bus = MagicMock()
        event_bus.get_service.side_effect = lambda name: (
            stack if name == "altview_stack_manager" else MagicMock()
        )
        registry = SlashCommandRegistry()
        SetupCommandHandler(registry, event_bus).register_commands()

        app = _make_app(provider_available=False, registry=registry)
        _run(app)

        stack.push.assert_awaited_once()
        view, name = stack.push.await_args.args[:2]
        self.assertEqual((type(view).__name__, name), ("SetupAltView", "setup"))
        app.config.save_key.assert_called_with("application.setup_completed", True)


if __name__ == "__main__":
    unittest.main()
