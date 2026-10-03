"""A plugin that initializes twice must not conflict with its own commands.

Task 22: start() runs every plugin's initialize() once to validate CLI arguments
(`_initialize_plugins_for_commands`) and again for the full init, so each startup
with a plugin CLI command (`kollab --hub status`, ...) logged
"Command name conflict: 'compact' already registered by plugin 'contextcompaction'"
(and terminal, sub, save, resume, hub, fork, branch, connect): 35 of 35 such starts.

Run: python -m pytest tests/unit/test_command_registry_reinit.py -v
"""

import unittest

from kollabor.commands.registry import SlashCommandRegistry as CommandRegistry
from kollabor_events.models import CommandCategory, CommandDefinition


def _cmd(plugin_name: str, handler) -> CommandDefinition:
    return CommandDefinition(
        name="compact",
        description="Show compaction profile and trigger info",
        plugin_name=plugin_name,
        category=CommandCategory.SYSTEM,
        handler=handler,
    )


async def _first(command):
    return "first"


async def _second(command):
    return "second"


class TestCommandReinitialization(unittest.TestCase):
    def test_same_plugin_registering_again_replaces_its_command(self) -> None:
        registry = CommandRegistry()
        self.assertTrue(registry.register_command(_cmd("contextcompaction", _first)))

        with self.assertNoLogs(level="ERROR"):
            ok = registry.register_command(_cmd("contextcompaction", _second))

        self.assertTrue(ok)
        self.assertIs(registry.get_command("compact").handler, _second)
        self.assertEqual(len(registry.get_commands_by_plugin("contextcompaction")), 1)

    def test_a_different_plugin_still_conflicts(self) -> None:
        registry = CommandRegistry()
        self.assertTrue(registry.register_command(_cmd("contextcompaction", _first)))

        with self.assertLogs(level="ERROR") as logs:
            ok = registry.register_command(_cmd("someone_else", _second))

        self.assertFalse(ok)
        self.assertIs(registry.get_command("compact").handler, _first)
        self.assertIn("already registered by plugin 'contextcompaction'", logs.output[0])


if __name__ == "__main__":
    unittest.main()
