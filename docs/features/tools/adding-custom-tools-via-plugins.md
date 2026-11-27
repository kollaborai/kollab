---
title: "Adding Custom Tools via Plugins"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Adding Custom Tools via Plugins

Plugins can register custom tools through the event system.

### Custom Native Tool

Create a plugin that handles tool execution:

```python
from kollabor_events.models import EventType

class MyToolPlugin:
    def register_hooks(self, registry):
        registry.register_hook(
            EventType.TOOL_CALL_PRE,
            self.handle_custom_tool,
            priority=100
        )

    async def handle_custom_tool(self, data):
        tool_data = data.get("tool_data", {})
        if tool_data.get("type") == "my_custom_tool":
            # Execute tool logic
            result = await self.execute(tool_data)
            # Modify context to return result
            return {"tool_result": result}
```

### Custom MCP Server

Create an MCP server and add it to `mcp_settings.json`:

```json
{
  "servers": {
    "my-tools": {
      "type": "stdio",
      "command": "python /path/to/server.py",
      "enabled": true
    }
  }
}
```

See [mcp.md](../mcp.md) for MCP server development.

