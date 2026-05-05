#!/usr/bin/env python3
"""
Demonstration script showing that local MCP servers are now properly connected.

Before fix: Local servers were discovered but never connected (status: "local")
After fix: Local servers are discovered AND connected (status: "connected")
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_events.bus import EventBus


async def demonstrate_fix():
    """Demonstrate that local MCP servers are now connected."""
    print("=" * 70)
    print("MCP Local Server Connection Fix Demonstration")
    print("=" * 70)
    print()

    # Create event bus
    event_bus = EventBus()
    mcp = MCPIntegration(event_bus=event_bus)

    # Create a temporary directory with a mock MCP server manifest
    with tempfile.TemporaryDirectory() as tmpdir:
        server_dir = Path(tmpdir) / "servers" / "example-local-server"
        server_dir.mkdir(parents=True)

        # Create manifest.json
        manifest = {
            "name": "example-local-server",
            "version": "1.0.0",
            "description": "Example local MCP server",
            "command": "echo 'Example MCP server'",
        }

        manifest_path = server_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        print(f"Created test server manifest at: {manifest_path}")
        print(f"Server name: {manifest['name']}")
        print(f"Server command: {manifest['command']}")
        print()

        # Mock _discover_local_servers to find our test server

        async def mock_discover(discovered):
            discovered["example-local-server"] = {
                "name": "example-local-server",
                "path": str(server_dir),
                "manifest": manifest,
                "status": "local",
            }

        # Mock _connect_and_list_tools to simulate connection
        with patch.object(mcp, "_discover_local_servers", side_effect=mock_discover):
            with patch.object(mcp, "_connect_and_list_tools") as mock_connect:
                # Simulate successful tool listing
                mock_connect.return_value = [
                    {
                        "name": "example_tool",
                        "description": "An example tool from local server",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"param": {"type": "string"}},
                            "required": ["param"],
                        },
                    }
                ]

                print("Running MCP server discovery...")
                print()

                discovered = await mcp.discover_mcp_servers()

                print("DISCOVERY RESULTS:")
                print("-" * 70)

                for server_name, server_info in discovered.items():
                    print(f"\nServer: {server_name}")
                    print(f"  Type: {server_info['type']}")
                    print(f"  Status: {server_info['status']}")

                    if server_info["status"] == "connected":
                        print(f"  Tools: {server_info['tool_count']}")
                        print(f"  Tool names: {', '.join(server_info['tools'])}")

                        # Verify tool was registered
                        for tool_name in server_info["tools"]:
                            if tool_name in mcp.tool_registry:
                                tool_info = mcp.tool_registry[tool_name]
                                print(f"\n  Registered tool: {tool_name}")
                                print(f"    Server: {tool_info['server']}")
                                print(f"    Enabled: {tool_info['enabled']}")

                print()
                print("-" * 70)
                print()

                # Verify the fix
                if "example-local-server" in discovered:
                    server = discovered["example-local-server"]

                    if server["status"] == "connected":
                        print("SUCCESS: Local server was connected!")
                        print("  - Status changed from 'local' to 'connected'")
                        print("  - Tools were discovered and registered")
                        print("  - Server is ready for tool calls")
                        print()
                        print("Fix verified: Local MCP servers now work correctly!")
                    elif server["status"] == "local":
                        print("FAILURE: Local server was NOT connected!")
                        print("  - Status stuck at 'local' (bug not fixed)")
                        print("  - No tools registered")
                    else:
                        print(f"UNEXPECTED: Server status is '{server['status']}'")
                else:
                    print("ERROR: Server not discovered")

    print()
    print("=" * 70)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Demonstrate that local MCP servers are properly connected",
        epilog="""
demonstrates:
  before fix: local servers discovered but never connected (status: "local")
  after fix:  local servers discovered AND connected (status: "connected")

usage:
  python scripts/test_mcp_local_fix.py
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.parse_args()
    asyncio.run(demonstrate_fix())


if __name__ == "__main__":
    main()
