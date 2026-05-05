---
title: "MCP (Model Context Protocol) Setup Guide"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# MCP (Model Context Protocol) Setup Guide

## Overview

MCP (Model Context Protocol) enables Kollab to integrate with external tools and services through standardized JSON-RPC 2.0 protocol over stdio. This allows the LLM to interact with filesystems, databases, APIs, and more.

## Quick Start

### 1. Copy Example Configuration

```bash
# For global configuration (recommended)
mkdir -p ~/.kollab/mcp
cp docs/mcp/mcp_settings.example.json ~/.kollab/mcp/mcp_settings.json

# For project-specific configuration
mkdir -p .kollab/mcp
cp docs/mcp/mcp_settings.example.json .kollab/mcp/mcp_settings.json
```

### 2. Edit Configuration

Edit `mcp_settings.json` and set `"enabled": true` for the servers you want to use:

```json
{
  "servers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/yourname",
      "enabled": true
    }
  }
}
```

### 3. Restart Kollab

Restart the application for MCP servers to be discovered and tools to be loaded.

## Configuration Structure

### Server Configuration Schema

```json
{
  "servers": {
    "server-name": {
      "type": "stdio",
      "command": "command to start the server",
      "enabled": true,
      "description": "Human-readable description",
      "env": {
        "ENV_VAR": "value"
      }
    }
  }
}
```

### Configuration Priority

1. **Local project config** (`.kollab/mcp/mcp_settings.json`) - higher priority
2. **Global config** (`~/.kollab/mcp/mcp_settings.json`) - lower priority

Local configuration overrides global settings for the same server name.

## Available MCP Servers

### Filesystem Server

**Purpose**: Read, write, and manipulate files

**Installation**: Automatically installed via npx

**Configuration**:
```json
{
  "servers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /allowed/path",
      "enabled": true
    }
  }
}
```

**Security Note**: Only specify paths you want the LLM to access. Multiple paths can be separated by spaces.

**Tools Provided**:
- `read_file` - Read file contents
- `write_file` - Write to file
- `create_directory` - Create directory
- `list_directory` - List directory contents
- `move_file` - Move/rename file
- `search_files` - Search for files by pattern

### Git Server

**Purpose**: Git repository operations

**Configuration**:
```json
{
  "servers": {
    "git": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository .",
      "enabled": true
    }
  }
}
```

**Tools Provided**:
- `git_status` - Get git status
- `git_log` - View commit history
- `git_diff` - View changes
- `git_commit` - Create commits
- `git_branch` - Manage branches

### GitHub Server

**Purpose**: GitHub API integration

**Prerequisites**: GitHub personal access token

**Configuration**:
```json
{
  "servers": {
    "github": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-github",
      "enabled": true,
      "env": {
        "GITHUB_TOKEN": "<your-github-token>"
      }
    }
  }
}
```

**Get Token**: https://github.com/settings/tokens (needs `repo` scope)

**Tools Provided**:
- `create_issue` - Create GitHub issues
- `create_pull_request` - Create PRs
- `push_files` - Push files to repository
- `search_issues_and_prs` - Search issues and PRs

### Brave Search Server

**Purpose**: Web search using Brave Search API

**Prerequisites**: Brave API key (free tier available)

**Configuration**:
```json
{
  "servers": {
    "brave-search": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-brave-search",
      "enabled": true,
      "env": {
        "BRAVE_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

**Get Key**: https://api.search.brave.com/app/keys

**Tools Provided**:
- `brave_web_search` - Search the web

### SQLite Server

**Purpose**: SQLite database operations

**Configuration**:
```json
{
  "servers": {
    "sqlite": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-sqlite --db-path ./database.db",
      "enabled": true
    }
  }
}
```

**Tools Provided**:
- `query_sqlite_db` - Execute SQL queries
- `create_sqlite_table` - Create tables

### PostgreSQL Server

**Purpose**: PostgreSQL database operations

**Configuration**:
```json
{
  "servers": {
    "postgres": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-postgres --connection-string postgresql://user:password@localhost:5432/dbname",
      "enabled": true
    }
  }
}
```

### Fetch Server

**Purpose**: HTTP requests to web APIs

**Configuration**:
```json
{
  "servers": {
    "fetch": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-fetch",
      "enabled": true
    }
  }
}
```

**Tools Provided**:
- `fetch` - Make HTTP GET/POST requests

### Puppeteer Server

**Purpose**: Web browser automation and scraping

**Prerequisites**: Node.js and Chromium installed

**Configuration**:
```json
{
  "servers": {
    "puppeteer": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-puppeteer",
      "enabled": true
    }
  }
}
```

**Tools Provided**:
- `puppeteer_navigate` - Navigate to URL
- `puppeteer_screenshot` - Take screenshot
- `puppeteer_click` - Click elements
- `puppeteer_fill` - Fill forms

## Environment Variables

Some MCP servers require API keys or configuration via environment variables:

### Method 1: In Configuration File

```json
{
  "servers": {
    "github": {
      "command": "npx -y @modelcontextprotocol/server-github",
      "env": {
        "GITHUB_TOKEN": "<your-github-token>"
      }
    }
  }
}
```

### Method 2: Shell Environment

Add to `~/.zshrc` or `~/.bashrc`:

```bash
export GITHUB_TOKEN="<your-github-token>"
export BRAVE_API_KEY="<your-api-key>"
```

## Verification

### Check MCP Server Status

```bash
# View application logs
tail -f ~/.kollab/projects/*/logs/kollab.log

# Look for lines like:
# "Loaded 3 MCP server configurations"
# "MCP server filesystem initialized"
# "Discovered 5 tools from filesystem server"
```

### Test MCP Tools

Start Kollab and ask the LLM to use MCP tools:

```
You: List the files in the current directory using the filesystem tools
You: What's the git status of this repository?
You: Search for "python async tutorial" using Brave Search
```

## Troubleshooting

### Issue: MCP Tools Not Available

**Symptoms**: LLM doesn't show MCP tools in tool list

**Solutions**:
1. Check configuration file syntax: `cat ~/.kollab/mcp/mcp_settings.json | python -m json.tool`
2. Verify `"enabled": true` for desired servers
3. Restart Kollab
4. Check logs for errors: `tail -f ~/.kollab/projects/*/logs/kollab.log`

### Issue: Server Fails to Start

**Symptoms**: Log shows "Failed to start MCP server"

**Solutions**:
1. Verify npx is installed: `npx --version`
2. Test server command manually:
   ```bash
   npx -y @modelcontextprotocol/server-filesystem /tmp
   ```
3. Check Node.js version: `node --version` (requires Node.js 18+)

### Issue: Permission Errors

**Symptoms**: Tools fail with permission denied

**Solutions**:
1. Check filesystem server allowed paths
2. Verify API tokens are valid and have required scopes
3. Check database connection strings

### Issue: No Tools Discovered

**Symptoms**: Server connects but no tools available

**Solutions**:
1. Verify server is MCP-compliant (implements `tools/list`)
2. Check server logs for initialization errors
3. Test server manually with MCP client

## Security Best Practices

### 1. Principle of Least Privilege

Only enable servers you need. Disable unused servers:

```json
{
  "servers": {
    "filesystem": {
      "enabled": true
    },
    "github": {
      "enabled": false  // Not using GitHub right now
    }
  }
}
```

### 2. Restrict Filesystem Access

Specify only necessary directories:

```json
{
  "servers": {
    "filesystem": {
      "command": "npx -y @modelcontextprotocol/server-filesystem /home/user/projects /tmp"
    }
  }
}
```

### 3. Use Scoped API Tokens

- Use GitHub tokens with minimum required scopes
- Use environment variables instead of hardcoding tokens
- Rotate tokens regularly

### 4. Review Tool Permissions

MCP tools respect the permission system. Configure approval mode:

```
/permissions strict    // Approve each MCP tool execution
/permissions default   // Use default approval settings
```

## Advanced Usage

### Custom MCP Servers

You can create custom MCP servers. See https://modelcontextprotocol.io for development guide.

### Multiple Filesystem Servers

Configure multiple filesystem servers for different directories:

```json
{
  "servers": {
    "fs-projects": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /home/user/projects",
      "enabled": true
    },
    "fs-downloads": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /home/user/Downloads",
      "enabled": true
    }
  }
}
```

### Project-Specific Configuration

Create local `.kollab/mcp/mcp_settings.json` for project-specific MCP servers:

```bash
cd /path/to/project
mkdir -p .kollab/mcp
cat > .kollab/mcp/mcp_settings.json << EOF
{
  "servers": {
    "git": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository .",
      "enabled": true
    }
  }
}
EOF
```

## Resources

- **MCP Specification**: https://modelcontextprotocol.io
- **Official MCP Servers**: https://github.com/modelcontextprotocol/servers
- **MCP SDK**: https://github.com/modelcontextprotocol/python-sdk

## Support

For issues or questions:
1. Check application logs: `~/.kollab/projects/*/logs/kollab.log`
2. Verify configuration with JSON validator
3. Test MCP server commands manually
4. Report issues at: https://github.com/kollaborai/kollab/issues
