"""Unit tests for slash command system."""

import unittest
from unittest.mock import AsyncMock, Mock

from kollabor.commands.executor import SlashCommandExecutor
from kollabor.commands.parser import SlashCommandParser
from kollabor.commands.registry import SlashCommandRegistry
from kollabor_events.models import (
    CommandDefinition,
    CommandResult,
    SlashCommand,
)


class TestSlashCommandParser(unittest.TestCase):
    """Test the slash command parser."""

    def setUp(self):
        self.parser = SlashCommandParser()

    def test_is_slash_command_detection(self):
        """Test slash command detection."""
        # Valid slash commands
        self.assertTrue(self.parser.is_slash_command("/help"))
        self.assertTrue(self.parser.is_slash_command("/save file.txt"))
        self.assertTrue(self.parser.is_slash_command("  /clear  "))

        # Invalid inputs
        self.assertFalse(self.parser.is_slash_command(""))
        self.assertFalse(self.parser.is_slash_command("/"))
        self.assertFalse(self.parser.is_slash_command("help"))
        self.assertFalse(self.parser.is_slash_command("regular text"))

    def test_parse_simple_command(self):
        """Test parsing simple commands."""
        command = self.parser.parse_command("/help")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "help")
        self.assertEqual(command.args, [])

    def test_parse_command_with_args(self):
        """Test parsing commands with arguments."""
        command = self.parser.parse_command("/save filename.txt")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "save")
        self.assertEqual(command.args, ["filename.txt"])

    def test_parse_command_with_quoted_args(self):
        """Test parsing commands with quoted arguments."""
        command = self.parser.parse_command('/save "my file.txt" --format json')
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "save")
        self.assertEqual(command.args, ["my file.txt", "--format", "json"])

    def test_parse_command_with_parameters(self):
        """Test parameter extraction."""
        command = self.parser.parse_command("/config --theme=dark --verbose")
        self.assertIsNotNone(command)
        self.assertEqual(command.parameters["theme"], "dark")
        self.assertEqual(command.parameters["verbose"], True)

    def test_validation(self):
        """Test command validation."""
        valid_command = SlashCommand(name="help", raw_input="/help")
        errors = self.parser.validate_command(valid_command)
        self.assertEqual(len(errors), 0)

        invalid_command = SlashCommand(name="", raw_input="/")
        errors = self.parser.validate_command(invalid_command)
        self.assertGreater(len(errors), 0)


class TestSlashCommandRegistry(unittest.TestCase):
    """Test the command registry."""

    def setUp(self):
        self.registry = SlashCommandRegistry()
        self.mock_handler = Mock()

    def test_register_command(self):
        """Test command registration."""
        command_def = CommandDefinition(
            name="test",
            description="Test command",
            handler=self.mock_handler,
            plugin_name="test_plugin",
        )

        success = self.registry.register_command(command_def)
        self.assertTrue(success)

        # Verify command is registered
        retrieved = self.registry.get_command("test")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "test")

    def test_register_command_with_aliases(self):
        """Test command registration with aliases."""
        command_def = CommandDefinition(
            name="clear",
            description="Clear screen",
            handler=self.mock_handler,
            plugin_name="test_plugin",
            aliases=["reset", "new"],
        )

        success = self.registry.register_command(command_def)
        self.assertTrue(success)

        # Test alias resolution
        self.assertIsNotNone(self.registry.get_command("clear"))
        self.assertIsNotNone(self.registry.get_command("reset"))
        self.assertIsNotNone(self.registry.get_command("new"))

    def test_prevent_duplicate_registration(self):
        """Test prevention of duplicate command registration."""
        command_def = CommandDefinition(
            name="test",
            description="Test command",
            handler=self.mock_handler,
            plugin_name="plugin1",
        )

        # First registration should succeed
        self.assertTrue(self.registry.register_command(command_def))

        # Second registration should fail
        duplicate_def = CommandDefinition(
            name="test",
            description="Duplicate test",
            handler=self.mock_handler,
            plugin_name="plugin2",
        )
        self.assertFalse(self.registry.register_command(duplicate_def))

    def test_unregister_plugin_commands(self):
        """Test unregistering all commands from a plugin."""
        # Register multiple commands from same plugin
        for i in range(3):
            command_def = CommandDefinition(
                name=f"test{i}",
                description=f"Test command {i}",
                handler=self.mock_handler,
                plugin_name="test_plugin",
            )
            self.registry.register_command(command_def)

        # Verify commands are registered
        self.assertEqual(len(self.registry.get_commands_by_plugin("test_plugin")), 3)

        # Unregister plugin commands
        unregistered = self.registry.unregister_plugin_commands("test_plugin")
        self.assertEqual(unregistered, 3)

        # Verify commands are gone
        self.assertEqual(len(self.registry.get_commands_by_plugin("test_plugin")), 0)

    def test_search_commands(self):
        """Test command search functionality."""
        command_def = CommandDefinition(
            name="save",
            description="Save conversation to file",
            handler=self.mock_handler,
            plugin_name="test_plugin",
            aliases=["store"],
        )
        self.registry.register_command(command_def)

        # Search by name
        results = self.registry.search_commands("save")
        self.assertEqual(len(results), 1)

        # Search by description
        results = self.registry.search_commands("conversation")
        self.assertEqual(len(results), 1)

        # Search by alias
        results = self.registry.search_commands("store")
        self.assertEqual(len(results), 1)


class TestSlashCommandExecutor(unittest.TestCase):
    """Test the command executor."""

    def setUp(self):
        self.registry = SlashCommandRegistry()
        self.executor = SlashCommandExecutor(self.registry)
        self.mock_event_bus = AsyncMock()

    async def test_execute_simple_command(self):
        """Test executing a simple command."""

        # Register a test command
        async def test_handler(command):
            return CommandResult(success=True, message="Test successful")

        command_def = CommandDefinition(
            name="test",
            description="Test command",
            handler=test_handler,
            plugin_name="test_plugin",
        )
        self.registry.register_command(command_def)

        # Execute the command
        command = SlashCommand(name="test", raw_input="/test")
        result = await self.executor.execute_command(command, self.mock_event_bus)

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Test successful")

    async def test_execute_unknown_command(self):
        """Test executing an unknown command."""
        command = SlashCommand(name="unknown", raw_input="/unknown")
        result = await self.executor.execute_command(command, self.mock_event_bus)

        self.assertFalse(result.success)
        self.assertIn("Unknown command", result.message)

    async def test_execute_disabled_command(self):
        """Test executing a disabled command."""
        # Register a disabled command
        command_def = CommandDefinition(
            name="disabled",
            description="Disabled command",
            handler=Mock(),
            plugin_name="test_plugin",
            enabled=False,
        )
        self.registry.register_command(command_def)

        command = SlashCommand(name="disabled", raw_input="/disabled")
        result = await self.executor.execute_command(command, self.mock_event_bus)

        self.assertFalse(result.success)
        self.assertIn("disabled", result.message)


if __name__ == "__main__":
    unittest.main()
