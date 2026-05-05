---
title: "MCP (Model Context Protocol)"
created: 2026-02-24
modified: 2026-02-24
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

### 3. Restart Kollabor

Servers auto-connect on startup if enabled.

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

| Command | Description |
|---------|-------------|
| `/mcp` | Show server status |
| `/mcp show <server>` | Show server details and tools |
| `/mcp add` | Add new server (interactive) |
| `/mcp remove <server>` | Remove server |

## Tool Execution

MCP tools integrate with the permission system:

```
[tool] mcp:filesystem::read_file
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
- `kollabor/commands/mcp_command.py` - CLI commands

note: mcp_manager.py does not exist. mcp_integration.py handles both
      protocol and configuration management.

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
2. Check `/mcp` output for connection status
3. Review logs for initialization errors

### Permission Errors

MCP tools respect the permission system. Use `/permissions` to configure.
