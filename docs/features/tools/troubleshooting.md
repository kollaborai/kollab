---
title: "Troubleshooting"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Troubleshooting

### Tools Not Available

1. Check MCP server status: `/mcp`
2. Verify native tool calling enabled in profile
3. Check logs: `~/.kollab/projects/*/logs/kollab.log`

### Permission Prompts Not Showing

1. Check approval mode: `/permissions`
2. Verify permission system loaded
3. Check for blocking hooks

### Tool Execution Timing Out

Default timeouts:
- Terminal: 90 seconds
- MCP tools: 180 seconds

Adjust in config:
```json
{
  "kollabor": {
    "agent": {
      "terminal_timeout": 120,
      "mcp_timeout": 300
    }
  }
}
```
