---
title: "MCP Local Server Connection Fix"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# MCP Local Server Connection Fix

## Problem

Local MCP servers were being discovered but never connected. The `_discover_local_servers()` method in `core/llm/mcp_integration.py` would load server manifests from disk but never progress to actually connecting to those servers or registering their tools.

### Current Behavior (Before Fix)

```python
# Line 398-426: _discover_local_servers()
async def _discover_local_servers(self, discovered: Dict):
    """Discover locally running MCP servers."""
    # Load manifests from ~/.mcp/servers/, .mcp/servers/, /usr/local/mcp/servers/
    for path in common_paths:
        if path.exists():
            for server_dir in path.iterdir():
                if server_dir.is_dir():
                    manifest = server_dir / "manifest.json"
                    if manifest.exists():
                        # Load manifest...
                        discovered[server_name] = {
                            "status": "local"  # <-- STUCK HERE, never progresses
                        }
    # NO CONNECTION LOGIC - servers never get connected!
```

**Result:**
- Servers discovered: YES
- Servers connected: NO
- Tools registered: NO
- Status stuck at: "local"

## Solution

Added connection logic in `discover_mcp_servers()` method (lines 260-305) to connect to discovered local servers after the discovery phase.

### Implementation (After Fix)

```python
async def discover_mcp_servers(self) -> Dict[str, Any]:
    discovered = {}

    # Step 1: Discover local servers (load manifests)
    await self._discover_local_servers(discovered)

    # Step 2: CONNECT to discovered local servers
    for server_name, server_info in list(discovered.items()):
        if server_info.get("status") == "local":
            manifest = server_info.get("manifest", {})
            command = manifest.get("command")

            if command:
                # Connect and register tools
                tools = await self._connect_and_list_tools(server_name, command)

                # Update server info with connection results
                discovered[server_name] = {
                    "name": server_name,
                    "type": "local",
                    "tools": [t.get("name") for t in tools],
                    "tool_count": len(tools),
                    "status": "connected",  # <-- NOW CONNECTED!
                    "path": server_info.get("path"),
                    "manifest": manifest
                }
            else:
                discovered[server_name]["status"] = "invalid"

    # Step 3: Continue with stdio servers...
```

## Key Changes

**File: `/path/to/kollab/core/llm/mcp_integration.py`**

### Lines 278-305 (NEW CODE)

Added a loop after `_discover_local_servers()` that:
1. Iterates through discovered servers with status="local"
2. Extracts the command from each server's manifest
3. Calls `_connect_and_list_tools()` to establish connection
4. Updates server status from "local" to "connected"
5. Registers tools in the tool registry

### Behavior Changes

| Aspect | Before | After |
|--------|--------|-------|
| Discovery | Manifests loaded | Manifests loaded |
| Connection | Never connected | Full MCP protocol handshake |
| Status | "local" (stuck) | "connected" (active) |
| Tool Registration | None | All tools registered |
| Tool Calls | Not available | Fully functional |

## Testing

### Unit Tests

Added comprehensive unit tests in `tests/test_mcp_integration.py`:

- `TestLocalMCPServerConnection.test_local_server_discovery_creates_connection`
  - Verifies local servers are connected after discovery
  - Checks status changes from "local" to "connected"
  - Confirms tools are registered in tool_registry

- `TestLocalMCPServerConnection.test_local_server_without_command_is_marked_invalid`
  - Verifies servers without commands are marked "invalid"

All tests pass:
```bash
$ python -m pytest tests/test_mcp_integration.py::TestLocalMCPServerConnection -v
...
tests/test_mcp_integration.py::TestLocalMCPServerConnection::test_local_server_discovery_creates_connection PASSED
tests/test_mcp_integration.py::TestLocalMCPServerConnection::test_local_server_without_command_is_marked_invalid PASSED

============================== 2 passed in 0.38s ===============================
```

### Integration Test

Created verification test: `tests/tmux/verify_mcp_local_server_connection.sh`
- Tests real MCP server discovery and connection
- Verifies tool registration
- Validates server status updates

### Demonstration Script

Created demonstration: `scripts/test_mcp_local_fix.py`
- Shows the fix in action
- Demonstrates status change from "local" to "connected"
- Verifies tool registration

Output:
```
SUCCESS: Local server was connected!
  - Status changed from 'local' to 'connected'
  - Tools were discovered and registered
  - Server is ready for tool calls

Fix verified: Local MCP servers now work correctly!
```

## Verification

The fix ensures that local MCP servers work identically to configured stdio servers:

1. **Discovery Phase**: Load manifests from standard locations
2. **Connection Phase**: Establish MCP JSON-RPC connection
3. **Initialization**: Send initialize request, wait for response
4. **Tool Listing**: Query available tools via tools/list
5. **Registration**: Register all tools in tool_registry
6. **Status Update**: Change from "local" to "connected"

## Files Modified

- `core/llm/mcp_integration.py` (lines 278-305)
  - Added connection loop for local servers

## Files Added

- `tests/test_mcp_integration.py` (TestLocalMCPServerConnection class)
  - Unit tests for the fix

- `tests/tmux/verify_mcp_local_server_connection.sh`
  - Integration test script

- `scripts/test_mcp_local_fix.py`
  - Demonstration script

## Impact

This fix enables local MCP servers to work as intended, allowing users to:
- Install MCP servers in standard locations (~/.mcp/servers/, .mcp/servers/, etc.)
- Have them automatically discovered and connected on application startup
- Use tools from local servers alongside configured stdio servers
- Get consistent behavior between local and configured servers

## Backward Compatibility

The fix maintains full backward compatibility:
- Existing configured stdio servers continue to work unchanged
- No configuration changes required
- Local servers without commands are marked "invalid" (fail gracefully)
- Error handling matches stdio server behavior
