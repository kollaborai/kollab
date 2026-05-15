"""Comprehensive tests for MCP (Model Context Protocol) integration."""

import asyncio
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for package_src in (
    PROJECT_ROOT / "packages" / "kollabor-agent" / "src",
    PROJECT_ROOT / "packages" / "kollabor-events" / "src",
    PROJECT_ROOT / "packages" / "kollabor-tui" / "src",
):
    sys.path.insert(0, str(package_src))

from kollabor_agent.mcp_integration import MCPIntegration, MCPServerConnection
from kollabor_events.bus import EventBus
from kollabor_events.models import EventType, Hook, HookPriority

logger = logging.getLogger(__name__)


class _FakeStdin:
    def __init__(self):
        self.writes = []
        self._closing = False

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        return None

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True


class _FakeStdout:
    def __init__(self, messages):
        self._chunks = [
            (json.dumps(message) + "\n").encode("utf-8")
            for message in messages
        ]

    async def read(self, size):
        if self._chunks:
            await asyncio.sleep(0)
            return self._chunks.pop(0)
        await asyncio.sleep(0)
        return b""

    def close(self):
        pass


class _HangingStdout:
    async def read(self, size):
        await asyncio.sleep(10)
        return b""

    def close(self):
        pass


class _FakeStderr:
    async def read(self):
        return b""

    def close(self):
        pass


class _FakeProcess:
    def __init__(self, stdout_messages=None, stdout=None):
        self.stdin = _FakeStdin()
        self.stdout = stdout if stdout is not None else _FakeStdout(stdout_messages or [])
        self.stderr = _FakeStderr()
        self.returncode = None

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class TestMCPServerConnection(unittest.TestCase):
    """Test MCP server connection management."""

    def test_connection_initialization(self):
        """Test connection object initialization."""
        connection = MCPServerConnection("test-server", "echo test")
        self.assertEqual(connection.server_name, "test-server")
        self.assertEqual(connection.command, "echo test")
        self.assertIsNone(connection.process)
        self.assertFalse(connection.initialized)

    def test_connection_state_tracking(self):
        """Test connection state is properly tracked."""
        connection = MCPServerConnection("test", "echo test")
        self.assertFalse(connection.initialized)

        # Simulate initialization
        connection.initialized = True
        self.assertTrue(connection.initialized)

    def test_json_rpc_notifications_do_not_satisfy_pending_request(self):
        """Notifications without ids are ignored while waiting for a response."""

        async def run_test():
            connection = MCPServerConnection("test", "echo test")
            connection.process = _FakeProcess(
                [
                    {"jsonrpc": "2.0", "method": "notifications/progress"},
                    {"jsonrpc": "2.0", "id": "req-1", "result": {"ok": True}},
                ]
            )

            response = await connection._send_request(
                {"jsonrpc": "2.0", "id": "req-1", "method": "tools/list"}
            )

            self.assertEqual(
                response,
                {"jsonrpc": "2.0", "id": "req-1", "result": {"ok": True}},
            )

        asyncio.run(run_test())

    def test_json_rpc_responses_are_correlated_by_id(self):
        """Out-of-order responses resolve to the matching request futures."""

        async def run_test():
            connection = MCPServerConnection("test", "echo test")
            connection.process = _FakeProcess(
                [
                    {"jsonrpc": "2.0", "id": "req-2", "result": {"order": 2}},
                    {"jsonrpc": "2.0", "id": "req-1", "result": {"order": 1}},
                ]
            )

            first, second = await asyncio.gather(
                connection._send_request(
                    {"jsonrpc": "2.0", "id": "req-1", "method": "first"}
                ),
                connection._send_request(
                    {"jsonrpc": "2.0", "id": "req-2", "method": "second"}
                ),
            )

            self.assertEqual(first["result"], {"order": 1})
            self.assertEqual(second["result"], {"order": 2})

        asyncio.run(run_test())

    def test_cancelled_request_closes_connection(self):
        """Outer MCP timeouts cancel the request and close the poisoned process."""

        async def run_test():
            connection = MCPServerConnection("test", "echo test")
            connection.initialized = True
            connection.process = _FakeProcess(stdout=_HangingStdout())

            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    connection._send_request(
                        {"jsonrpc": "2.0", "id": "req-1", "method": "tools/call"}
                    ),
                    timeout=0.01,
                )

            self.assertIsNone(connection.process)
            self.assertFalse(connection.initialized)
            self.assertEqual(connection._pending_requests, {})

        asyncio.run(run_test())


class TestMCPIntegration(unittest.TestCase):
    """Test MCP integration functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create event bus for testing
        self.event_bus = EventBus()

    @patch.object(MCPIntegration, "_load_mcp_config", return_value=None)
    def test_initialization(self, mock_load):
        """Test MCP integration initializes without configuration."""
        mcp = MCPIntegration(event_bus=self.event_bus)
        self.assertEqual(len(mcp.mcp_servers), 0)
        self.assertEqual(len(mcp.tool_registry), 0)
        self.assertEqual(len(mcp.server_connections), 0)
        self.assertIsNotNone(mcp.event_bus)

    def test_tool_registration(self):
        """Test tool registration from server."""
        mcp = MCPIntegration(event_bus=self.event_bus)

        # Simulate tool registration
        tool_definition = {
            "name": "test_tool",
            "description": "A test tool",
            "inputSchema": {
                "type": "object",
                "properties": {"param1": {"type": "string"}},
                "required": ["param1"],
            },
        }

        mcp.tool_registry["test_tool"] = {
            "server": "test-server",
            "definition": tool_definition,
            "enabled": True,
        }

        self.assertIn("test_tool", mcp.tool_registry)
        self.assertEqual(mcp.tool_registry["test_tool"]["server"], "test-server")

    def test_get_tool_definitions_for_api(self):
        """Test getting tool definitions in API format."""
        mcp = MCPIntegration(event_bus=self.event_bus)

        # Register a test tool
        mcp.tool_registry["test_tool"] = {
            "server": "test-server",
            "definition": {
                "name": "test_tool",
                "description": "Test",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "enabled": True,
        }

        # Get tool definitions (includes MCP tools + built-in file operation tools)
        tools = mcp.get_tool_definitions_for_api()

        # Should have our test tool + built-in file operation tools (16 built-ins)
        self.assertGreater(len(tools), 0)

        # Find our test tool in the list
        test_tools = [t for t in tools if t["name"] == "test_tool"]
        self.assertEqual(len(test_tools), 1)
        self.assertEqual(test_tools[0]["name"], "test_tool")

    def test_event_emission_on_discovery(self):
        """Test that events are emitted during server discovery."""
        mcp = MCPIntegration(event_bus=self.event_bus)

        # Track emitted events
        discovered_events = []

        async def track_discovery(data, event):
            if event.type in (
                EventType.MCP_SERVER_DISCOVER,
                EventType.MCP_SERVER_DISCOVERED,
            ):
                discovered_events.append((event.type, data))

        # Register hook for events using POSTPROCESSING priority
        hook = Hook(
            name="test_tracker",
            plugin_name="test_plugin",
            event_type=EventType.MCP_SERVER_DISCOVER,
            callback=track_discovery,
            priority=HookPriority.POSTPROCESSING.value,
        )

        hook2 = Hook(
            name="test_tracker2",
            plugin_name="test_plugin",
            event_type=EventType.MCP_SERVER_DISCOVERED,
            callback=track_discovery,
            priority=HookPriority.POSTPROCESSING.value,
        )

        # Mock a server configuration
        mcp.mcp_servers = {
            "mock-server": {"type": "stdio", "command": "echo test", "enabled": True}
        }

        # Run discovery with hooks registered
        async def run_test():
            await self.event_bus.register_hook(hook)
            await self.event_bus.register_hook(hook2)
            await mcp.discover_mcp_servers()

        asyncio.run(run_test())

        # Verify events were emitted
        self.assertGreater(
            len(discovered_events), 0, "Discovery events should be emitted"
        )

    @patch.object(MCPIntegration, "_load_mcp_config", return_value=None)
    def test_reload_mcp_servers_clears_stale_state(self, mock_load):
        """Reload closes old connections and removes stale server config."""
        mcp = MCPIntegration(event_bus=self.event_bus)
        old_connection = MagicMock()
        old_connection.close = AsyncMock()
        mcp.server_connections["old-server"] = old_connection
        mcp.tool_registry["old_tool"] = {"server": "old-server"}
        mcp.mcp_servers["old-server"] = {
            "type": "stdio",
            "command": "old",
            "enabled": True,
        }

        async def fake_discover():
            mcp.mcp_servers["new-server"] = {
                "type": "stdio",
                "command": "new",
                "enabled": True,
            }
            return {"new-server": {"status": "connected"}}

        mcp.discover_mcp_servers = AsyncMock(side_effect=fake_discover)  # type: ignore[method-assign]

        summary = asyncio.run(mcp.reload_mcp_servers())

        old_connection.close.assert_awaited_once()
        self.assertNotIn("old-server", mcp.mcp_servers)
        self.assertNotIn("old_tool", mcp.tool_registry)
        self.assertEqual(summary["configured"], 1)
        self.assertEqual(summary["discovered"], 1)
        self.assertEqual(summary["reconnected"], 0)
        self.assertGreaterEqual(mock_load.call_count, 2)


class TestMCPStatusView(unittest.TestCase):
    """Test MCP status view rendering."""

    def setUp(self):
        """Set up test fixtures."""
        from kollabor_tui.status.mcp_status_view import MCPStatusView

        self.event_bus = EventBus()
        self.mcp_integration = MCPIntegration(event_bus=self.event_bus)
        self.status_view = MCPStatusView(self.mcp_integration)

    def test_render_with_no_servers(self):
        """Test rendering status when no servers configured."""
        lines = self.status_view.render()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_with_connected_servers(self):
        """Test rendering status with connected servers."""
        # Mock server connection
        mock_connection = MagicMock()
        mock_connection.initialized = True

        self.mcp_integration.server_connections["test-server"] = mock_connection
        self.mcp_integration.tool_registry["test_tool"] = {
            "server": "test-server",
            "definition": {
                "name": "test_tool",
                "description": "Test tool",
                "parameters": {},
            },
            "enabled": True,
        }

        lines = self.status_view.render()
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_render_tool_detail(self):
        """Test rendering tool detail view."""
        from kollabor_tui.status.mcp_status_view import MCPToolDetailView

        tool_info = {
            "server": "test-server",
            "definition": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param1": {"type": "string", "description": "Test parameter"}
                    },
                    "required": ["param1"],
                },
            },
            "enabled": True,
        }

        detail_view = MCPToolDetailView("test_tool", tool_info)
        lines = detail_view.render()

        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)


class TestMCPEventIntegration(unittest.TestCase):
    """Test MCP integration with event system."""

    def test_event_bus_integration(self):
        """Test that MCP integration has event bus reference."""
        event_bus = EventBus()
        mcp = MCPIntegration(event_bus=event_bus)
        self.assertIsNotNone(mcp.event_bus)
        self.assertEqual(mcp.event_bus, event_bus)

    def test_mcp_events_exist(self):
        """Test that all MCP events are defined in EventType."""
        mcp_event_names = [
            "MCP_SERVER_CONNECT",
            "MCP_SERVER_CONNECTED",
            "MCP_SERVER_DISCONNECT",
            "MCP_SERVER_ERROR",
            "MCP_SERVER_DISCOVER",
            "MCP_SERVER_DISCOVERED",
            "MCP_TOOL_REGISTER",
            "MCP_TOOL_UNREGISTER",
            "MCP_TOOL_CALL_PRE",
            "MCP_TOOL_CALL_POST",
        ]

        for event_name in mcp_event_names:
            self.assertTrue(
                hasattr(EventType, event_name), f"EventType should have {event_name}"
            )

    def test_event_types_are_valid(self):
        """Test that MCP events are valid EventType enum values."""
        mcp_events = [
            EventType.MCP_SERVER_CONNECT,
            EventType.MCP_SERVER_CONNECTED,
            EventType.MCP_SERVER_DISCONNECT,
            EventType.MCP_SERVER_ERROR,
            EventType.MCP_SERVER_DISCOVER,
            EventType.MCP_SERVER_DISCOVERED,
            EventType.MCP_TOOL_REGISTER,
            EventType.MCP_TOOL_UNREGISTER,
            EventType.MCP_TOOL_CALL_PRE,
            EventType.MCP_TOOL_CALL_POST,
        ]

        for event in mcp_events:
            self.assertIsInstance(event, EventType)


class TestLocalMCPServerConnection(unittest.TestCase):
    """Test local MCP server discovery and connection bug fix."""

    def setUp(self):
        """Set up test fixtures."""
        self.event_bus = EventBus()

    def test_local_server_discovery_creates_connection(self):
        """Test that discovered local servers are connected, not just discovered.

        Bug: Local servers were discovered but never connected.
        Fix: discover_mcp_servers() now connects to local servers after discovery.
        """
        mcp = MCPIntegration(event_bus=self.event_bus)

        # Create a temporary directory with a mock MCP server manifest
        with tempfile.TemporaryDirectory() as tmpdir:
            server_dir = Path(tmpdir) / "servers" / "test-local-server"
            server_dir.mkdir(parents=True)

            # Create manifest.json
            manifest = {
                "name": "test-local-server",
                "version": "1.0.0",
                "description": "Test server",
                "command": "echo 'mock MCP server'",
            }

            with open(server_dir / "manifest.json", "w") as f:
                json.dump(manifest, f)

            # Mock the common paths to use our temp directory
            with patch.object(mcp, "_discover_local_servers") as mock_discover:
                # Simulate _discover_local_servers finding our server
                async def mock_discover_impl(discovered):
                    discovered["test-local-server"] = {
                        "name": "test-local-server",
                        "path": str(server_dir),
                        "manifest": manifest,
                        "status": "local",
                    }

                mock_discover.side_effect = mock_discover_impl

                # Mock _connect_and_list_tools to avoid actually starting a process
                with patch.object(mcp, "_connect_and_list_tools") as mock_connect:
                    test_tool = {
                        "name": "test_tool",
                        "description": "A test tool",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    }
                    mock_connect.return_value = [test_tool]

                    # Run discovery
                    async def run_discovery():
                        # Manually register the tool (simulating what _connect_and_list_tools does)
                        mcp.tool_registry["test_tool"] = {
                            "server": "test-local-server",
                            "definition": {
                                "name": "test_tool",
                                "description": "A test tool",
                                "parameters": test_tool["inputSchema"],
                            },
                            "enabled": True,
                        }
                        return await mcp.discover_mcp_servers()

                    discovered = asyncio.run(run_discovery())

                    # Verify that _connect_and_list_tools was called for the local server
                    local_calls = [
                        call_args
                        for call_args in mock_connect.call_args_list
                        if call_args[0][0] == "test-local-server"
                    ]
                    self.assertEqual(len(local_calls), 1)
                    call_args = local_calls[0]

                    # Should be called with server name and command from manifest
                    self.assertEqual(call_args[0][0], "test-local-server")
                    self.assertEqual(call_args[0][1], "echo 'mock MCP server'")

                    # Verify the discovered server has status "connected" not "local"
                    self.assertIn("test-local-server", discovered)
                    server_info = discovered["test-local-server"]
                    self.assertEqual(server_info["status"], "connected")
                    self.assertEqual(server_info["type"], "local")
                    self.assertEqual(server_info["tool_count"], 1)
                    self.assertIn("test_tool", server_info["tools"])

                    # Verify tool was registered in tool_registry
                    self.assertIn("test_tool", mcp.tool_registry)
                    self.assertEqual(
                        mcp.tool_registry["test_tool"]["server"], "test-local-server"
                    )

    def test_local_server_without_command_is_marked_invalid(self):
        """Test that local servers without commands are marked invalid."""
        mcp = MCPIntegration(event_bus=self.event_bus)

        # Mock discovery of a server without a command
        with patch.object(mcp, "_discover_local_servers") as mock_discover:

            async def mock_discover_impl(discovered):
                discovered["invalid-server"] = {
                    "name": "invalid-server",
                    "path": "/invalid/path",
                    "manifest": {},  # No command
                    "status": "local",
                }

            mock_discover.side_effect = mock_discover_impl

            # Run discovery
            async def run_discovery():
                return await mcp.discover_mcp_servers()

            discovered = asyncio.run(run_discovery())

            # Verify the server is marked invalid
            self.assertIn("invalid-server", discovered)
            self.assertEqual(discovered["invalid-server"]["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
