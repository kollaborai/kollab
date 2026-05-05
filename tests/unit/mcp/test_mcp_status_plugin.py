"""Tests for MCP Status Plugin."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor_events.models import Event, EventType
from plugins.mcp_status_plugin import MCPStatusPlugin


class TestMCPStatusPlugin(unittest.TestCase):
    """Test MCP Status Plugin functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.event_bus = MagicMock()
        self.event_bus.register_hook = AsyncMock()
        self.renderer = MagicMock()
        self.config = MagicMock()

        # Set default config values
        self.config.get = lambda key, default=None: {
            "plugins.mcp_status.enabled": True,
            "plugins.mcp_status.show_status": True,
            "plugins.mcp_status.show_errors": True,
        }.get(key, default)

        self.plugin = MCPStatusPlugin(
            name="test_mcp_status",
            event_bus=self.event_bus,
            renderer=self.renderer,
            config=self.config,
        )

    def test_plugin_initialization(self):
        """Test plugin initializes correctly."""
        self.assertEqual(self.plugin.name, "test_mcp_status")
        self.assertEqual(self.plugin.connected_servers, 0)
        self.assertEqual(self.plugin.total_tools, 0)
        self.assertEqual(self.plugin.error_count, 0)
        self.assertEqual(self.plugin.connecting_count, 0)

    def test_get_status_lines_empty(self):
        """Test status lines when no servers connected."""
        status = self.plugin.get_status_lines()
        self.assertIn("B", status)
        self.assertEqual(status["A"], [])
        self.assertEqual(status["C"], [])

    def test_get_status_lines_with_servers(self):
        """Test status lines with connected servers."""
        self.plugin.connected_servers = 3
        self.plugin.total_tools = 12

        status = self.plugin.get_status_lines()
        self.assertIn("B", status)
        self.assertTrue(any("3 servers" in line for line in status["B"]))
        self.assertTrue(any("12 tools" in line for line in status["B"]))

    def test_get_status_lines_with_errors(self):
        """Test status lines when there are errors."""
        self.plugin.connected_servers = 2
        self.plugin.error_count = 1

        status = self.plugin.get_status_lines()
        self.assertIn("B", status)
        self.assertTrue(any("error" in line.lower() for line in status["B"]))

    def test_get_status_lines_connecting(self):
        """Test status lines when servers are connecting."""
        self.plugin.connecting_count = 2
        self.plugin.mcp_servers = {"server1": {}, "server2": {}}

        status = self.plugin.get_status_lines()
        self.assertIn("B", status)
        self.assertTrue(any("Connecting" in line for line in status["B"]))

    def test_default_config(self):
        """Test default configuration."""
        config = MCPStatusPlugin.get_default_config()
        self.assertIn("plugins", config)
        self.assertIn("mcp_status", config["plugins"])
        self.assertTrue(config["plugins"]["mcp_status"]["enabled"])

    def test_startup_info(self):
        """Test startup information."""
        info = MCPStatusPlugin.get_startup_info(self.config)
        self.assertIsInstance(info, list)
        self.assertTrue(len(info) > 0)

    def test_config_widgets(self):
        """Test configuration widgets."""
        widgets = MCPStatusPlugin.get_config_widgets()
        self.assertIn("title", widgets)
        self.assertIn("widgets", widgets)
        self.assertEqual(widgets["title"], "MCP Status Plugin")

    async def test_server_connect_event(self):
        """Test handling server connect event."""
        event = Event(
            type=EventType.MCP_SERVER_CONNECT,
            data={"server_name": "test_server"},
            source="test",
        )

        result = await self.plugin._on_server_connect({}, event)

        self.assertEqual(self.plugin.connecting_count, 1)
        self.assertEqual(result["status"], "monitored")
        self.assertIn("test_server", self.plugin.mcp_servers)

    async def test_server_connected_event(self):
        """Test handling server connected event."""
        # First connect
        self.plugin.connecting_count = 1

        event = Event(
            type=EventType.MCP_SERVER_CONNECTED,
            data={"server_name": "test_server", "tools": ["tool1", "tool2", "tool3"]},
            source="test",
        )

        result = await self.plugin._on_server_connected({}, event)

        self.assertEqual(self.plugin.connected_servers, 1)
        self.assertEqual(self.plugin.total_tools, 3)
        self.assertEqual(self.plugin.connecting_count, 0)
        self.assertEqual(result["status"], "monitored")

    async def test_server_error_event(self):
        """Test handling server error event."""
        self.plugin.connecting_count = 1

        event = Event(
            type=EventType.MCP_SERVER_ERROR,
            data={"server_name": "test_server", "error": "Connection failed"},
            source="test",
        )

        result = await self.plugin._on_server_error({}, event)

        self.assertEqual(self.plugin.error_count, 1)
        self.assertEqual(self.plugin.connecting_count, 0)
        self.assertEqual(result["status"], "monitored")

    async def test_tool_register_event(self):
        """Test handling tool register event."""
        # Setup: server already connected
        self.plugin.mcp_servers["test_server"] = {
            "status": "connected",
            "tools": [],
            "error": None,
        }

        event = Event(
            type=EventType.MCP_TOOL_REGISTER,
            data={"tool_name": "new_tool", "server_name": "test_server"},
            source="test",
        )

        result = await self.plugin._on_tool_register({}, event)

        self.assertEqual(self.plugin.total_tools, 1)
        self.assertIn("new_tool", self.plugin.mcp_servers["test_server"]["tools"])
        self.assertEqual(result["status"], "monitored")

    async def test_register_hooks(self):
        """Test hook registration."""
        await self.plugin.register_hooks()

        # Verify register_hook was called for all MCP events
        self.assertGreater(self.event_bus.register_hook.call_count, 0)

        # Check that hook names contain expected patterns
        hook_names = [
            call.args[0].name for call in self.event_bus.register_hook.call_args_list
        ]
        self.assertIn("mcp_status_server_connect", hook_names)
        self.assertIn("mcp_status_server_connected", hook_names)
        self.assertIn("mcp_status_server_error", hook_names)


if __name__ == "__main__":
    unittest.main()
