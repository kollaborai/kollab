"""Unit tests for the first-run setup-wizard trigger.

The wizard fires when there is no usable LLM provider (true first install OR a
config that exists but has no provider), and stays quiet for env-var users
(their key auto-activates a profile, so ``is_provider_available()`` is True),
for attach mode, and once setup has been completed/skipped.

See ``TerminalLLMChat._check_first_run_wizard`` in ``kollabor/application.py``.
"""

import asyncio
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor.application import TerminalLLMChat


def _make_app(
    *,
    attach=None,
    setup_completed=False,
    provider_available=False,
    first_install=True,
    with_setup_plugin=True,
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
    app.profile_manager = MagicMock()

    fi = MagicMock()
    instance = MagicMock(completed=True, skipped=False)
    fi.registered_plugins = (
        {"setup": MagicMock(return_value=instance)} if with_setup_plugin else {}
    )
    fsm = MagicMock()
    fsm.launch_plugin = AsyncMock()
    fi._fullscreen_manager = fsm
    app.fullscreen_integrator = fi
    return app


def _run(app):
    asyncio.run(TerminalLLMChat._check_first_run_wizard(app))


class FirstRunWizardGateTests(unittest.TestCase):
    def test_fires_when_no_usable_provider(self):
        app = _make_app(provider_available=False)
        _run(app)
        app.fullscreen_integrator._fullscreen_manager.launch_plugin.assert_awaited_once_with(
            "setup"
        )
        app.config.save_key.assert_called_with("application.setup_completed", True)

    def test_skips_when_provider_available_env_var_user(self):
        # Env-var keys auto-activate a profile -> provider available -> no nag.
        app = _make_app(provider_available=True, first_install=True)
        _run(app)
        app.fullscreen_integrator._fullscreen_manager.launch_plugin.assert_not_awaited()
        app.config.save_key.assert_not_called()

    def test_skips_in_attach_mode(self):
        app = _make_app(attach="unix:///tmp/sock", provider_available=False)
        _run(app)
        app.fullscreen_integrator._fullscreen_manager.launch_plugin.assert_not_awaited()

    def test_skips_when_setup_already_completed(self):
        app = _make_app(setup_completed=True, provider_available=False)
        _run(app)
        app.fullscreen_integrator._fullscreen_manager.launch_plugin.assert_not_awaited()

    def test_probe_failure_does_not_hijack_screen(self):
        app = _make_app(probe_raises=True)
        _run(app)  # must not raise
        app.fullscreen_integrator._fullscreen_manager.launch_plugin.assert_not_awaited()

    def test_graceful_when_setup_plugin_missing(self):
        app = _make_app(provider_available=False, with_setup_plugin=False)
        _run(app)  # must not raise
        app.fullscreen_integrator._fullscreen_manager.launch_plugin.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
