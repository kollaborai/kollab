"""`--provider` / `--model` / `--effort` replace `--profile`.

`--profile` conflated a provider profile name with a loadout name and offered
no way to override just the model or just the reasoning effort at launch. It is
removed outright, not aliased. A short-lived intermediate name `--llm` is also
removed, since "llm" is a meaningless bucket label (every model is an LLM).

Spec: docs/specs/llm-loadout-live-catalog.md (CLI surface, rules R1-R5).
"""

import unittest

from kollabor.cli import parse_arguments
from kollabor_ai.profile_manager import EFFORT_LEVELS


class TestProviderLaunchFlags(unittest.TestCase):
    def test_provider_model_and_effort_parse_together(self):
        args = parse_arguments(
            plugin_classes=[],
            argv=[
                "--provider",
                "openai",
                "--model",
                "gpt-5.6-luna",
                "--effort",
                "max",
            ],
        )
        self.assertEqual(args.provider, "openai")
        self.assertEqual(args.model, "gpt-5.6-luna")
        self.assertEqual(args.effort, "max")

    def test_profile_flag_is_gone(self):
        """Hard removal, not an alias -- argparse must reject it."""
        with self.assertRaises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--profile", "openai"])

    def test_llm_flag_is_gone(self):
        """The interim --llm name is also removed."""
        with self.assertRaises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--llm", "openai"])

    def test_all_three_default_to_none(self):
        args = parse_arguments(plugin_classes=[], argv=[])
        self.assertIsNone(args.provider)
        self.assertIsNone(args.model)
        self.assertIsNone(args.effort)

    # -- R1: model/effort stand alone -------------------------------------

    def test_model_alone_is_accepted(self):
        """Applies to the already-active profile; no --provider required."""
        args = parse_arguments(plugin_classes=[], argv=["--model", "gpt-5.6-terra"])
        self.assertEqual(args.model, "gpt-5.6-terra")
        self.assertIsNone(args.provider)

    def test_effort_alone_is_accepted(self):
        args = parse_arguments(plugin_classes=[], argv=["--effort", "high"])
        self.assertEqual(args.effort, "high")
        self.assertIsNone(args.provider)

    # -- R4: effort is validated at parse time ----------------------------

    def test_every_documented_effort_level_is_accepted(self):
        for level in EFFORT_LEVELS:
            with self.subTest(level=level):
                args = parse_arguments(
                    plugin_classes=[],
                    argv=["--provider", "openai", "--effort", level],
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
            argv=["--provider", "openrouter", "--model", "anthropic/claude-opus-4.5"],
        )
        self.assertEqual(args.model, "anthropic/claude-opus-4.5")

    # -- --default now hangs off --provider -------------------------------

    def test_default_requires_provider(self):
        with self.assertRaises(SystemExit):
            parse_arguments(plugin_classes=[], argv=["--default"])

    def test_default_with_provider_is_accepted(self):
        args = parse_arguments(
            plugin_classes=[], argv=["--provider", "work", "--default"]
        )
        self.assertTrue(args.make_default_profile)
        self.assertEqual(args.provider, "work")

    # -- attach forwarding -------------------------------------------------

    def test_new_flags_are_daemon_launch_flags(self):
        """Otherwise auto-attach refuses to start with them (cli.py whitelist)."""
        import inspect

        from kollabor import cli

        source = inspect.getsource(cli)
        start = source.index("daemon_launch_flags = {")
        block = source[start : source.index("}", start)]
        for flag in ('"--provider"', '"--model"', '"--effort"'):
            self.assertIn(flag, block)
        for stale in ('"--profile"', '"--llm"'):
            self.assertNotIn(stale, block)


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

    def test_application_stashes_both_for_attach(self):
        import inspect

        from kollabor import application

        source = inspect.getsource(application.TerminalLLMChat.__init__)
        self.assertIn('"model": model_override', source)
        self.assertIn('"effort": effort_override', source)


if __name__ == "__main__":
    unittest.main()
