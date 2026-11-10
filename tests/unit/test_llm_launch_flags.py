"""`--llm` / `--model` / `--effort` replace `--profile`.

`--profile` conflated a provider profile name with a loadout name and offered
no way to override just the model or just the reasoning effort at launch. It is
removed outright, not aliased.

Spec: docs/specs/llm-loadout-live-catalog.md (CLI surface, rules R1-R5).
"""

import json
import logging
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kollabor.cli import parse_arguments
from kollabor_ai.profile_manager import EFFORT_LEVELS


class TestLlmLaunchFlags(unittest.TestCase):
    def test_llm_model_and_effort_parse_together(self):
        args = parse_arguments(
            plugin_classes=[],
            argv=["--llm", "openai", "--model", "gpt-5.6-luna", "--effort", "max"],
        )
        self.assertEqual(args.llm, "openai")
        self.assertEqual(args.model, "gpt-5.6-luna")
        self.assertEqual(args.effort, "max")

    def test_profile_flag_is_gone(self):
        """Hard removal, not an alias -- argparse must reject it."""
        with self.assertRaises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--profile", "openai"])

    def test_all_three_default_to_none(self):
        args = parse_arguments(plugin_classes=[], argv=[])
        self.assertIsNone(args.llm)
        self.assertIsNone(args.model)
        self.assertIsNone(args.effort)

    # -- R1: model/effort stand alone -------------------------------------

    def test_model_alone_is_accepted(self):
        """Applies to the already-active profile; no --llm required."""
        args = parse_arguments(plugin_classes=[], argv=["--model", "gpt-5.6-terra"])
        self.assertEqual(args.model, "gpt-5.6-terra")
        self.assertIsNone(args.llm)

    def test_effort_alone_is_accepted(self):
        args = parse_arguments(plugin_classes=[], argv=["--effort", "high"])
        self.assertEqual(args.effort, "high")
        self.assertIsNone(args.llm)

    # -- R4: effort is validated at parse time ----------------------------

    def test_every_documented_effort_level_is_accepted(self):
        for level in EFFORT_LEVELS:
            with self.subTest(level=level):
                args = parse_arguments(
                    plugin_classes=[], argv=["--llm", "openai", "--effort", level]
                )
                self.assertEqual(args.effort, level)

    def test_unknown_effort_is_rejected(self):
        """Providers 400 on anything outside EFFORT_LEVELS -- fail early."""
        with self.assertRaises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--effort", "turbo"])

    # -- R5: model is deliberately unvalidated ----------------------------

    def test_unknown_model_id_still_launches(self):
        """Matches /model's free-form entry: the registry is not a gate."""
        args = parse_arguments(
            plugin_classes=[], argv=["--model", "some-model-nobody-has-heard-of"]
        )
        self.assertEqual(args.model, "some-model-nobody-has-heard-of")

    def test_provider_prefixed_model_ids_survive_intact(self):
        args = parse_arguments(
            plugin_classes=[],
            argv=["--llm", "openrouter", "--model", "anthropic/claude-opus-4.5"],
        )
        self.assertEqual(args.model, "anthropic/claude-opus-4.5")

    # -- --default now hangs off --llm ------------------------------------

    def test_default_requires_llm(self):
        with self.assertRaises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--default"])

    def test_default_with_llm_is_accepted(self):
        args = parse_arguments(plugin_classes=[], argv=["--llm", "work", "--default"])
        self.assertTrue(args.make_default_profile)
        self.assertEqual(args.llm, "work")

    # -- attach forwarding -------------------------------------------------

    def test_new_flags_are_daemon_launch_flags(self):
        """Otherwise auto-attach refuses to start with them (cli.py whitelist)."""
        import inspect

        from kollabor import cli

        source = inspect.getsource(cli)
        start = source.index("daemon_launch_flags = {")
        block = source[start : source.index("}", start)]
        for flag in ('"--llm"', '"--model"', '"--effort"'):
            self.assertIn(flag, block)
        self.assertNotIn('"--profile"', block)


class TestAttachFlagPlumbing(unittest.TestCase):
    """--model/--effort must survive the client->daemon boundary.

    Attach mode is the default whenever a daemon is running, and it applies
    launch flags via state_service RPC rather than locally. A flag missing from
    that path is silently dropped -- the failure mode this project calls an
    orphaned subsystem.
    """

    def test_state_interface_accepts_model_and_effort(self):
        import inspect

        from kollabor.state.interface import StateService

        sig = inspect.signature(StateService.set_active_profile)
        self.assertIn("model", sig.parameters)
        self.assertIn("effort", sig.parameters)

    def test_local_and_remote_signatures_match_the_interface(self):
        import inspect

        from kollabor.state.interface import StateService
        from kollabor.state.local import LocalStateService
        from kollabor.state.remote import RemoteStateService

        expected = set(inspect.signature(StateService.set_active_profile).parameters)
        for impl in (LocalStateService, RemoteStateService):
            with self.subTest(impl=impl.__name__):
                actual = set(inspect.signature(impl.set_active_profile).parameters)
                self.assertEqual(expected, actual)

    def test_rpc_handler_forwards_both(self):
        import inspect

        from kollabor.state import handlers

        source = inspect.getsource(handlers)
        self.assertIn('params.get("model")', source)
        self.assertIn('params.get("effort")', source)

    def test_default_loadout_forwards_all_parameters(self):
        """Persisted startup loadouts must apply model and every optional tuning field."""
        import inspect

        from kollabor import application

        source = inspect.getsource(application.TerminalLLMChat.__init__)
        start = source.index("# Apply a persisted loadout default")
        end = source.index("# Log auto-detection result", start)
        block = source[start:end]
        for field in ("temperature", "max_tokens"):
            with self.subTest(field=field):
                self.assertIn(f'update_kwargs["{field}"] = loadout.{field}', block)
        # effort goes through the sendable filter (an unsent level is dropped)
        self.assertIn("loadout_sendable_effort(", block)
        self.assertIn('update_kwargs["effort"] = loadout_effort', block)
        self.assertIn("loadout.provider_profile, **update_kwargs", block)

    def test_cli_loadout_probe_suppresses_missing_profile_error(self):
        import inspect

        from kollabor import application

        source = inspect.getsource(application.TerminalLLMChat.__init__)
        start = source.index("if profile_name:")
        end = source.index("elif save_profile or make_default_profile", start)
        block = source[start:end]
        self.assertIn("log_not_found=False", block)

    def test_application_stashes_both_for_attach(self):
        import inspect

        from kollabor import application

        source = inspect.getsource(application.TerminalLLMChat.__init__)
        self.assertIn('"model": model_override', source)
        self.assertIn('"effort": effort_override', source)


def test_cli_loadout_startup_resolves_without_profile_error(
    tmp_path, monkeypatch, caplog
):
    """Exercise real TerminalLLMChat startup, not just source wiring."""
    from kollabor import application as application_module
    from kollabor_config import config_utils

    project = tmp_path / "project"
    home = tmp_path / "home"
    global_config = tmp_path / "global"
    project.mkdir()
    home.mkdir()
    global_config.mkdir()
    (global_config / "config.json").write_text(
        json.dumps(
            {
                "kollabor": {
                    "llm": {
                        "profiles": {
                            "ghost": {
                                "provider": "anthropic",
                                "model": "claude-haiku-4.5",
                                "api_key": "test-key",
                            }
                        },
                        "loadouts": {
                            "fast-haiku": {
                                "provider_profile": "ghost",
                                "model": "claude-haiku-4.5",
                            }
                        },
                        "default_profile": "ghost",
                    }
                }
            }
        )
    )
    registry = SimpleNamespace(
        get_merged_config=lambda: {},
        list_plugins=lambda: [],
        instantiate_plugins=lambda *args, **kwargs: {},
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KOLLAB_NO_AUTO_DETECT", "1")
    # TerminalLLMChat's project context writes these process-global keys; track
    # their pre-test state so this real-startup regression cannot leak it.
    for env_name in ("KOLLAB_ROOT_SOCKET", "KOLLAB_HUB_PROJECT_SCOPED"):
        # setenv records even an initially absent key, so teardown removes
        # values that the real constructor creates.
        monkeypatch.setenv(env_name, os.environ.get(env_name, ""))
    caplog.set_level(logging.INFO)

    with patch.object(
        config_utils, "get_config_directory", return_value=global_config
    ), patch.object(Path, "home", return_value=home):
        app = application_module.TerminalLLMChat(
            args=SimpleNamespace(simple=True),
            profile_name="fast-haiku",
            plugin_registry=registry,
        )

    active = app.profile_manager.get_active_profile()
    assert app.profile_manager.active_profile_name == "ghost"
    assert active.model == "claude-haiku-4.5"
    assert "Resolved 'fast-haiku' as loadout" in caplog.text
    assert not any(
        "Profile not found: fast-haiku" in record.getMessage()
        for record in caplog.records
    )


def _startup_with_default_loadout(
    tmp_path, monkeypatch, caplog, loadouts, default_loadout
):
    """Start a real TerminalLLMChat with no --llm and a persisted default loadout."""
    from kollabor import application as application_module
    from kollabor_config import config_utils

    project = tmp_path / "project"
    home = tmp_path / "home"
    global_config = tmp_path / "global"
    project.mkdir()
    home.mkdir()
    global_config.mkdir()
    (global_config / "config.json").write_text(
        json.dumps(
            {
                "kollabor": {
                    "llm": {
                        "profiles": {
                            "ghost": {
                                "provider": "anthropic",
                                "model": "claude-haiku-4.5",
                                "api_key": "test-key",
                            }
                        },
                        "loadouts": loadouts,
                        "default_loadout": default_loadout,
                        "default_profile": "ghost",
                    }
                }
            }
        )
    )
    registry = SimpleNamespace(
        get_merged_config=lambda: {},
        list_plugins=lambda: [],
        instantiate_plugins=lambda *args, **kwargs: {},
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KOLLAB_NO_AUTO_DETECT", "1")
    for env_name in ("KOLLAB_ROOT_SOCKET", "KOLLAB_HUB_PROJECT_SCOPED"):
        monkeypatch.setenv(env_name, os.environ.get(env_name, ""))
    caplog.set_level(logging.INFO)

    with patch.object(
        config_utils, "get_config_directory", return_value=global_config
    ), patch.object(Path, "home", return_value=home):
        app = application_module.TerminalLLMChat(
            args=SimpleNamespace(simple=True),
            profile_name=None,
            plugin_registry=registry,
        )
    return app


def test_deleted_default_loadout_starts_on_the_active_profile(
    tmp_path, monkeypatch, caplog
):
    """The saved default is an exact name (#964): with 'luna' deleted and only
    'luna-max' saved, the terminal starts on the active profile, not a loadout
    nobody chose. Adding an unrelated loadout must not move where sessions run."""
    app = _startup_with_default_loadout(
        tmp_path,
        monkeypatch,
        caplog,
        loadouts={"luna-max": {"provider_profile": "ghost", "model": "gpt-6-luna"}},
        default_loadout={"name": "luna", "level": "global"},
    )

    active = app.profile_manager.get_active_profile()
    assert app.profile_manager.active_profile_name == "ghost"
    # the profile's own model: the substring-matching loadout must not apply
    assert active.model == "claude-haiku-4.5"
    assert "Applied default loadout" not in caplog.text


def test_default_matching_no_loadout_logs_a_warning_naming_it(
    tmp_path, monkeypatch, caplog
):
    """A saved default that matches no loadout (#964) is logged by name, so the
    silent fall back to the active profile is visible in the log."""
    _startup_with_default_loadout(
        tmp_path,
        monkeypatch,
        caplog,
        loadouts={"luna-max": {"provider_profile": "ghost", "model": "gpt-6-luna"}},
        default_loadout={"name": "luna", "level": "global"},
    )

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "Default loadout 'luna' matches no saved loadout" in message
        for message in warnings
    ), warnings


def test_default_with_different_case_does_not_apply_and_is_logged(
    tmp_path, monkeypatch, caplog
):
    """The saved default is compared exactly (#964): 'Luna' does not apply to a
    saved 'luna', and the fall back is logged naming 'Luna'."""
    app = _startup_with_default_loadout(
        tmp_path,
        monkeypatch,
        caplog,
        loadouts={"luna": {"provider_profile": "ghost", "model": "gpt-6-luna"}},
        default_loadout={"name": "Luna", "level": "global"},
    )

    active = app.profile_manager.get_active_profile()
    assert active.model == "claude-haiku-4.5"
    assert "Applied default loadout" not in caplog.text
    assert "Default loadout 'Luna' matches no saved loadout" in caplog.text


def test_exact_default_loadout_still_applies_at_startup(tmp_path, monkeypatch, caplog):
    """The guard for the fix above: an exact saved default keeps applying."""
    app = _startup_with_default_loadout(
        tmp_path,
        monkeypatch,
        caplog,
        loadouts={"luna-max": {"provider_profile": "ghost", "model": "gpt-6-luna"}},
        default_loadout={"name": "luna-max", "level": "global"},
    )

    active = app.profile_manager.get_active_profile()
    assert active.model == "gpt-6-luna"
    assert "Applied default loadout 'luna-max'" in caplog.text


if __name__ == "__main__":
    unittest.main()
