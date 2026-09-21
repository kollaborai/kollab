---
title: "MCP (Model Context Protocol)"
created: 2026-02-24
modified: 2026-09-20
status: active
---
# MCP (Model Context Protocol)

MCP enables Kollab to integrate with external tools and services through standardized JSON-RPC 2.0 over stdio. This allows the LLM to interact with filesystems, databases, APIs, and more.

## Overview

MCP servers provide tools that the LLM can call:

| Server | Tools | Use Case |
|--------|-------|----------|
| Filesystem | `read_file`, `write_file`, `create_directory` | File operations |
| Git | `git_status`, `git_log`, `git_commit` | Version control |
| GitHub | `create_issue`, `create_pull_request` | GitHub integration |
| Brave Search | `brave_web_search` | Web search |
| SQLite | `query_sqlite_db` | Database queries |
| Puppeteer | `puppeteer_navigate`, `puppeteer_screenshot` | Browser automation |

## Quick Start

### 1. Create Configuration

```bash
# Global config
mkdir -p ~/.kollab/mcp
cp docs/mcp/mcp_settings.example.json ~/.kollab/mcp/mcp_settings.json

# Project config
mkdir -p .kollab/mcp
cp docs/mcp/mcp_settings.example.json .kollab/mcp/mcp_settings.json
```

### 2. Configure Servers

`~/.kollab/mcp/mcp_settings.json`:

```json
{
  "servers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/yourname",
      "enabled": true
    },
    "git": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository .",
      "enabled": true
    }
  }
}
```

### 3. Start or Reload Kollab

Servers auto-connect on startup if enabled. If Kollab is already running,
use `/mcp reload` to reload both global and project configuration and reconnect
enabled servers.

## Configuration

### Priority Order

1. Local project - `.kollab/mcp/mcp_settings.json`
2. Global - `~/.kollab/mcp/mcp_settings.json`

Local overrides global for the same server name.

### Server Schema

```json
{
  "servers": {
    "server-name": {
      "type": "stdio",
      "command": "command to start server",
      "enabled": true,
      "description": "Human-readable description",
      "env": {
        "ENV_VAR": "value"
      }
    }
  }
}
```

## Common Servers

### Filesystem

```json
{
  "filesystem": {
    "type": "stdio",
    "command": "npx -y @modelcontextprotocol/server-filesystem /allowed/path",
    "enabled": true
  }
}
```

**Tools**: `read_file`, `write_file`, `create_directory`, `list_directory`, `move_file`, `search_files`

### Git

```json
{
  "git": {
    "type": "stdio",
    "command": "npx -y @modelcontextprotocol/server-git --repository .",
    "enabled": true
  }
}
```

**Tools**: `git_status`, `git_log`, `git_diff`, `git_commit`, `git_branch`

### GitHub

```json
{
  "github": {
    "type": "stdio",
    "command": "npx -y @modelcontextprotocol/server-github",
    "enabled": true,
    "env": {
      "GITHUB_TOKEN": "<your-github-token>"
    }
  }
}
```

**Get Token**: https://github.com/settings/tokens (needs `repo` scope)

### Brave Search

```json
{
  "brave-search": {
    "type": "stdio",
    "command": "npx -y @modelcontextprotocol/server-brave-search",
    "enabled": true,
    "env": {
      "BRAVE_API_KEY": "<your-api-key>"
    }
  }
}
```

**Get Key**: https://api.search.brave.com/app/keys

### SQLite

```json
{
  "sqlite": {
    "type": "stdio",
    "command": "npx -y @modelcontextprotocol/server-sqlite --db-path ./database.db",
    "enabled": true
  }
}
```

## Environment Variables

API keys can be set via:

### In Config

```json
{
  "env": {
    "GITHUB_TOKEN": "<your-github-token>",
    "BRAVE_API_KEY": "xxx"
  }
}
```

### Shell Environment

```bash
export GITHUB_TOKEN="<your-github-token>"
export BRAVE_API_KEY="xxx"
```

## MCP Commands

The bare command and `setup` open the interactive MCP manager in the normal
TUI. The manager reads runtime status and provides per-server actions; the
slash subcommands below are also usable from attach mode where supported.

| Command | Description |
|---------|-------------|
| `/mcp` | Open the interactive MCP manager |
| `/mcp setup` | Open the interactive MCP manager (alias) |
| `/mcp show` | Show server status |
| `/mcp list` | Show server status (alias) |
| `/mcp servers` | Show server status (alias) |
| `/mcp tools [server]` | Show available tools |
| `/mcp test <server>` | Test one server connection |
| `/mcp enable <server>` | Enable a server in configuration |
| `/mcp disable <server>` | Disable a server in configuration |
| `/mcp reload` | Reload MCP config and reconnect enabled servers; report failures |
| `/mcps`, `/servers` | Top-level aliases for `/mcp` |

`/mcp status` is accepted as a compatibility alias for `/mcp show`.

Inside the `/mcp` manager:

- `g` toggles the global MCP subsystem (`plugins.mcp.enabled`).
- Space toggles the selected configured server.
- `a` adds the selected available server to the global configuration.
- `d` deletes the selected configured server from the global configuration.
- `t` tests the selected server; `r` reloads enabled servers and reports failures.
- `/` filters; arrows, Page Up/Down, Home, and End navigate; Esc exits.

The manager's add, delete, and enable/disable actions write the global
`~/.kollab/mcp/mcp_settings.json`. Edit `.kollab/mcp/mcp_settings.json`
directly for project-specific configuration, then run `/mcp reload`.

## Tool Execution

MCP tools integrate with the permission system:

```
[tool] mcp:filesystem:read_file
[server] filesystem
[path] /Users/you/file.txt

approve? [o]nce/[s]ession/[p]roject/[d]eny
```

## Multiple Instances

Run multiple servers of the same type:

```json
{
  "servers": {
    "fs-work": {
      "command": "npx -y @modelcontextprotocol/server-filesystem /work"
    },
    "fs-home": {
      "command": "npx -y @modelcontextprotocol/server-filesystem /home"
    },
    "git-work": {
      "command": "npx -y @modelcontextprotocol/server-git --repository /work"
    },
    "git-personal": {
      "command": "npx -y @modelcontextprotocol/server-git --repository /personal"
    }
  }
}
```

## Protocol

MCP uses JSON-RPC 2.0 over stdio:

### Initialize

```json
→ {"jsonrpc":"2.0","id":"1","method":"initialize","params":{...}}
← {"jsonrpc":"2.0","id":"1","result":{...}}
→ {"jsonrpc":"2.0","method":"notifications/initialized"}
```

### List Tools

```json
→ {"jsonrpc":"2.0","id":"2","method":"tools/list","params":{}}
← {"jsonrpc":"2.0","id":"2","result":{"tools":[...]}}
```

### Call Tool

```json
→ {"jsonrpc":"2.0","id":"3","method":"tools/call","params":{"name":"read_file","arguments":{...}}}
← {"jsonrpc":"2.0","id":"3","result":{...}}
```

## Implementation

### Key Files

- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py` - MCP client and protocol
- `packages/kollabor-agent/src/kollabor_agent/mcp_manager.py` - MCP configuration and server operations
- `kollabor/commands/mcp_command.py` - slash-command handler
- `plugins/altview/mcp_wizard_altview.py` - interactive MCP manager
- `docs/reference/commands.md` - canonical command reference

### Tool Registry

Discovered tools are stored in `tool_registry`:

```python
{
    "tool_name": {
        "server": "server-name",
        "definition": {...},
        "description": "..."
    }
}
```

## Troubleshooting

### Server Not Connecting

1. Test command manually:
   ```bash
   npx -y @modelcontextprotocol/server-filesystem /tmp
   ```
2. Check logs: `~/.kollab/projects/*/logs/kollab.log`
3. Verify Node.js 18+: `node --version`

### Tools Not Available

1. Verify `"enabled": true` in config
2. Open `/mcp` for manager state or `/mcp show` for status output
3. Review logs for initialization errors

### Permission Errors

MCP tools respect the permission system. Use `/permissions` to configure.
