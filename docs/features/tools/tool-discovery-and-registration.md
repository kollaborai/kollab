---
title: "Tool Discovery and Registration"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Tool Discovery and Registration

### Native Tools

Built-in tool definitions come from the tool registry and are executed by the
normal tool executor. `MCPIntegration` assembles the schemas sent to the model,
combining built-ins with enabled external MCP tools.

Built-ins do not require an MCP server. In the current source, `--no-mcp` skips
external discovery and schemas while retaining permitted built-in tools, including
file and Hub tools. An external discovery error also leaves built-in loading
available. Built-in schemas are loaded before background discovery begins, so a
slow server does not leave the first request without them; discovered MCP schemas
are added after discovery finishes. The active agent's tool scope,
`kollabor.llm.native_tool_calling`, and
the active profile's tool support still apply. Exposing a schema does not bypass
the workspace's tool permission policy.

Agent bundle scope treats terminal session management as part of the `terminal`
capability. The `terminal-status`, `terminal-output`, and `terminal-kill` tools
require that grant, so an agent without terminal access cannot inspect or stop
a session started by the operator.

### Web Fetch

`web-fetch` streams a response instead of buffering the full page. It retains
at most 500000 downloaded bytes, including redirect response bodies, and reads
at most one additional byte to detect overflow. The final response is
additionally limited to 2000000 decompressed bytes. The optional Playwright
fallback renders the bounded response and fetches script resources within the
remaining byte limits; other browser requests are blocked.

`max_chars` limits the complete returned output, including the URL header and
any truncation marker. It accepts integers from 1 through 50000 and defaults to
5000. Invalid values return an error.

File location: `packages/kollabor-agent/src/kollabor_agent/`

- `tool_executor.py` - Main execution engine
- `file_operations_executor.py` - File operation handlers
- `shell_executor.py` - Shell command execution
- `native_tools_handler.py` - Native schema loading and background MCP discovery
- `mcp_integration.py` - External MCP client and model-facing schema assembly

### MCP Tools

MCP tools are discovered at startup from configured servers. The system:

1. Reads `~/.kollab/mcp/mcp_settings.json`
2. Starts each enabled server via stdio
3. Sends `tools/list` JSON-RPC request
4. Registers discovered tools in the tool registry

Tool registry format:
```python
{
    "tool_name": {
        "server": "server-name",
        "definition": {...},  # MCP tool definition
        "description": "..."
    }
}
```

The Web UI shows completed Hub MCP calls with readable tool labels. Hub tool
rows show the recipient in the collapsed row; thread and reply ids remain in
the expanded call details.

