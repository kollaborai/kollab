---
title: "Tool Calling"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Tool Calling

Tool calling (also called function calling) allows the LLM to request actions that
the system executes on its behalf. The LLM doesn't run commands directly -- it
outputs structured requests that Kollabor executes, then returns results back to
the LLM.

## Overview

When the LLM needs to take an action, it has two ways to request it:

1. Native tool calling - Structured function calls via the API
2. XML tags - Text-based tags embedded in the response

Both paths go through the same permission system and execution pipeline.

## Execution Flow

```
LLM Request
    |
    v
[Parse Response]
    |
    +---> Native tool_calls -----> [ToolExecutor]
    |
    +---> XML tags ---------------> [ResponseParser] ---> [ToolExecutor]
                                            |
                                            v
                                      [Permission Check]
                                            |
          +-------------------------------+-------------------------------+
          |                               |                               |
    [Approve]                       [Session/Project]                   [Deny]
          |                               |                               |
          v                               v                               v
    [Execute Tool]                  [Execute Tool]                  [Return Error]
          |                               |                               |
          v                               v                               |
    [Return Result] -------------> [Feed Back to LLM] <---------------+
```

## Built-in Tools

note: these are XML tags parsed by kollabor_ai.response_parser, not tools/
  directory entries. execution happens in packages/kollabor-agent/.

### Terminal Commands

The LLM can execute shell commands through the `terminal` XML tag.

XML tag syntax:
```
<terminal>ls -la</terminal>
```

Background execution:
```
<terminal background="true" name="dev-server">npm run dev</terminal>
```

With working directory:
```
<terminal cwd="/tmp">pwd</terminal>
```

Execution methods:
- tmux sessions (preferred, supports background jobs)
- direct subprocess (fallback)

### File Operations

The LLM can read, create, and modify files. All file operations go through
the FileOperationsExecutor with safety features:
- Automatic backups before destructive changes
- Protected path checking
- Binary file detection
- Size limits

XML tag syntax:

Read file:
```
<read>
<path>src/main.py</path>
</read>
```

Edit file (replaces ALL occurrences):
```
<edit>
<path>config.json</path>
<old_str>"timeout": 30</old_str>
<new_str>"timeout": 60</new_str>
</edit>
```

Create new file:
```
<create>
<path>new_file.py</path>
<content>
print("hello world")
</content>
</create>
```

Create or overwrite:
```
<create_overwrite>
<path>output.txt</path>
<content>content</content>
</create_overwrite>
```

Delete file:
```
<delete>
<path>old_file.txt</path>
</delete>
```

Move/rename:
```
<move>
<path>old_name.py</path>
<destination>new_name.py</destination>
</move>
```

Copy:
```
<copy>
<path>source.py</path>
<destination>backup.py</destination>
</copy>
```

Copy with overwrite:
```
<copy_overwrite>
<path>source.py</path>
<destination>backup.py</destination>
</copy_overwrite>
```

Append:
```
<append>
<path>log.txt</append>
<content>New log entry</content>
</append>
```

Insert after pattern:
```
<insert_after>
<path>config.py</path>
<pattern>import os</pattern>
<content>import sys</content>
</insert_after>
```

Insert before pattern:
```
<insert_before>
<path>config.py</path>
<pattern>if __name__</pattern>
<content># Main entry point</content>
</insert_before>
```

Create directory:
```
<mkdir>
<path>new_directory</path>
</mkdir>
```

Remove directory:
```
<rmdir>
<path>empty_directory</path>
</rmdir>
```

Search file:
```
<grep>
<path>main.py</path>
<pattern>TODO</pattern>
</grep>
```

### MCP Tools

MCP (Model Context Protocol) servers provide external tools. See [mcp.md](mcp.md)
for full details.

Common MCP tools:
- `filesystem::read_file` - Read file contents
- `filesystem::write_file` - Write files
- `filesystem::create_directory` - Create directories
- `git::git_status` - Git status
- `git::git_log` - Git log
- `git::git_commit` - Commit changes
- `github::create_issue` - Create GitHub issues
- `github::create_pull_request` - Create PRs
- `brave_search::brave_web_search` - Web search
- `sqlite::query_sqlite_db` - Database queries
- `puppeteer::*` - Browser automation

## Tool Discovery and Registration

### Native Tools

Native tools are defined in the tool executor and exposed via MCP integration:

File location: `packages/kollabor-agent/src/kollabor_agent/`

- `tool_executor.py` - Main execution engine
- `file_operations_executor.py` - File operation handlers
- `shell_executor.py` - Shell command execution
- `mcp_integration.py` - MCP protocol client

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

## Tool Calling Methods

Different LLM providers use different protocols for tool calling:

### Anthropic (Claude)

Uses native tool calling with structured `tool_use` blocks:

```json
{
  "type": "tool_use",
  "id": "toolu_01...",
  "name": "terminal",
  "input": {"command": "ls -la"}
}
```

Also supports XML tags as fallback when native calling is disabled.

### OpenAI / OpenAI-compatible

Uses native `tool_calls` format:

```json
{
  "tool_calls": [{
    "id": "call_01...",
    "type": "function",
    "function": {
      "name": "terminal",
      "arguments": "{\"command\": \"ls -la\"}"
    }
  }]
}
```

### Gemini

Transformed to/from Anthropic format for compatibility.

### OpenRouter

Depends on the underlying model. Routes through appropriate transformer.

### Configuration

Native tool calling can be disabled per profile:

```json
{
  "profiles": {
    "my-profile": {
      "native_tool_calling": false,
      ...
    }
  }
}
```

When disabled, the LLM uses XML tags instead.

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

See [mcp.md](mcp.md) for MCP server development.

## Tool Output Formatting

Tool results are fed back to the LLM as user messages.

The format depends on result type:

Successful execution:
```
[terminal] $ ls -la
drwxr-xr-x  5 user  staff  160 Feb 24 10:00 .
drwxr-xr-x 10 user  staff  320 Feb 24 09:00 ..
-rw-r--r--  1 user  staff   42 Feb 24 10:00 file.txt
```

File edit with diff:
```
[file_edit] Edited config.json
--- before
+++ after
@@ -1 +1 @@
-"timeout": 30
+"timeout": 60
```

MCP tool result:
```
[mcp:filesystem::read_file]
Contents of /path/to/file.txt...
```

Error:
```
[terminal] ERROR: Command failed with exit code 1
```

## Safety: Permission System

All tool execution goes through the permission system. See [permissions.md](permissions.md)
for complete documentation.

### Risk Levels

- LOW - Read operations, safe commands (`ls`, `cat`)
- MEDIUM - Package installs, git operations
- HIGH - Destructive commands (`rm`, file writes)
- UNKNOWN - New tools

### Approval Prompt

```
[tool] terminal: git commit -m "fix"
[risk] MEDIUM

approve? [o]nce/[s]ession/[p]roject/[d]eny/[ESC] cancel
```

### Key Bindings

- `o` - Approve once
- `s` - Approve for session
- `p` - Approve for project (persistent)
- `d` - Deny
- `ESC` - Cancel entire response
- `A` - Always approve file edits (context-specific)
- `t` - Trust root command (context-specific)

### Blocked Tools

Dangerous tools can be blocked in config:

```json
{
  "kollabor": {
    "permissions": {
      "blocked_tools": ["rm", "dd", "mkfs"]
    }
  }
}
```

## Implementation

### Key Files

Tool execution:
- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`
- `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`
- `packages/kollabor-agent/src/kollabor_agent/shell_executor.py`
- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py`

Response parsing:
- `packages/kollabor-ai/src/kollabor_ai/response_parser.py`

Permission integration:
- `packages/kollabor-agent/src/kollabor_agent/permissions/`
- `kollabor/llm/permissions/`

### Events

- `TOOL_CALL_PRE` - Before execution (permission check)
- `TOOL_CALL_POST` - After execution (result handling)
- `PERMISSION_CHECK` - Permission evaluation
- `PERMISSION_CONFIRMATION` - User prompted
- `PERMISSION_GRANTED` - Permission approved
- `PERMISSION_DENIED` - Permission denied

### Configuration

```json
{
  "kollabor": {
    "llm": {
      "native_tool_calling": true
    },
    "permissions": {
      "approval_mode": "default",
      "blocked_tools": [],
      "trusted_tools": ["ls", "cat", "grep"]
    }
  }
}
```

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
