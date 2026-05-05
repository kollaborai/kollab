---
title: "MCP Server Examples"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# MCP Server Examples

This document provides comprehensive examples of MCP (Model Context Protocol) server configurations for common use cases.

## Table of Contents

- [Getting Started](#getting-started)
- [File System Operations](#file-system-operations)
- [Version Control](#version-control)
- [Database Operations](#database-operations)
- [Web Scraping & Browsing](#web-scraping--browsing)
- [Cloud Services](#cloud-services)
- [Development Tools](#development-tools)
- [Advanced Configurations](#advanced-configurations)

---

## Getting Started

### Quick Setup

```bash
# 1. Create MCP directory
mkdir -p ~/.kollab/mcp

# 2. Copy example configuration
cp docs/mcp/mcp_settings.example.json ~/.kollab/mcp/mcp_settings.json

# 3. Edit configuration
nano ~/.kollab/mcp/mcp_settings.json

# 4. Enable desired servers (set "enabled": true)
# 5. Restart Kollab
```

### Configuration Template

```json
{
  "servers": {
    "server-name": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-name",
      "enabled": false,
      "description": "Human-readable description",
      "env": {
        "ENV_VAR": "value"
      }
    }
  }
}
```

---

## File System Operations

### Basic File Access

```json
{
  "servers": {
    "filesystem-home": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/$(whoami)",
      "enabled": true,
      "description": "Access home directory files"
    },
    "filesystem-projects": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/$(whoami)/projects",
      "enabled": true,
      "description": "Access project directories"
    }
  }
}
```

### Multiple Project Directories

```json
{
  "servers": {
    "fs-projects": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/dev/projects /Users/dev/work",
      "enabled": true,
      "description": "Access multiple project directories"
    },
    "fs-downloads": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/$(whoami)/Downloads",
      "enabled": false,
      "description": "Access downloads folder"
    },
    "fs-tmp": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /tmp",
      "enabled": true,
      "description": "Temporary file access"
    }
  }
}
```

**Security Note**: Only specify paths you want the LLM to access. The filesystem server respects these boundaries.

---

## Version Control

### Git Operations

```json
{
  "servers": {
    "git": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository /Users/dev/projects/myproject",
      "enabled": true,
      "description": "Git operations for specific project"
    },
    "git-current": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository .",
      "enabled": true,
      "description": "Git operations for current directory"
    }
  }
}
```

**Available Tools**:
- `git_status` - Get working directory status
- `git_log` - View commit history
- `git_diff` - Show changes between commits
- `git_commit` - Create commits
- `git_branch` - Manage branches
- `git_show` - View commit details

### GitHub Integration

```json
{
  "servers": {
    "github": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-github",
      "enabled": false,
      "description": "GitHub API integration",
      "env": {
        "GITHUB_TOKEN": "<your-github-token>"
      }
    }
  }
}
```

**Get GitHub Token**: https://github.com/settings/tokens (requires `repo` scope)

**Available Tools**:
- `create_issue` - Create GitHub issues
- `create_pull_request` - Create pull requests
- `push_files` - Push files to repository
- `search_issues_and_prs` - Search issues and PRs
- `create_or_update_file` - Create/update files
- `create_repository` - Create new repositories

---

## Database Operations

### SQLite Database

```json
{
  "servers": {
    "sqlite-main": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-sqlite --db-path ./database.db",
      "enabled": true,
      "description": "SQLite database operations"
    },
    "sqlite-readonly": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-sqlite --db-path /usr/local/share/data.db --readonly",
      "enabled": false,
      "description": "Read-only SQLite access"
    }
  }
}
```

**Available Tools**:
- `query_sqlite_db` - Execute SQL queries
- `create_sqlite_table` - Create tables
- `list_sqlite_tables` - List all tables

### PostgreSQL Database

```json
{
  "servers": {
    "postgres-local": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-postgres --connection-string postgresql://user:password@localhost:5432/dbname",
      "enabled": false,
      "description": "Local PostgreSQL database"
    },
    "postgres-production": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-postgres --connection-string postgresql://user:password@prod.example.com:5432/appdb",
      "enabled": false,
      "description": "Production PostgreSQL (requires SSL)"
    }
  }
}
```

**Connection String Format**:
```
postgresql://[user[:password]@][host][:port][/dbname][?param1=value1&...]
```

**Available Tools**:
- `query_postgres_db` - Execute SQL queries
- `create_postgres_table` - Create tables
- `list_postgres_tables` - List all tables

---

## Web Scraping & Browsing

### Brave Search

```json
{
  "servers": {
    "brave-search": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-brave-search",
      "enabled": false,
      "description": "Web search using Brave Search API",
      "env": {
        "BRAVE_API_KEY": "your-brave-api-key"
      }
    }
  }
}
```

**Get API Key**: https://api.search.brave.com/app/keys (free tier available)

**Available Tools**:
- `brave_web_search` - Search the web

### Puppeteer Web Automation

```json
{
  "servers": {
    "puppeteer": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-puppeteer",
      "enabled": false,
      "description": "Browser automation and web scraping"
    }
  }
}
```

**Available Tools**:
- `puppeteer_navigate` - Navigate to URL
- `puppeteer_screenshot` - Take screenshot
- `puppeteer_click` - Click elements
- `puppeteer_fill` - Fill forms
- `puppeteer_select` - Select from dropdowns
- `puppeteer_read` - Read page content

### HTTP Fetch

```json
{
  "servers": {
    "fetch": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-fetch",
      "enabled": true,
      "description": "HTTP requests to web APIs"
    }
  }
}
```

**Available Tools**:
- `fetch` - Make HTTP GET/POST requests

---

## Cloud Services

### Exa AI-Powered Search

```json
{
  "servers": {
    "exa": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-exa",
      "enabled": false,
      "description": "Exa AI-powered semantic search",
      "env": {
        "EXA_API_KEY": "your-exa-api-key"
      }
    }
  }
}
```

**Get API Key**: https://exa.ai

**Available Tools**:
- `exa_search` - Semantic web search

### Google Drive (Future)

```json
{
  "servers": {
    "google-drive": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-gdrive",
      "enabled": false,
      "description": "Google Drive integration (coming soon)",
      "env": {
        "GOOGLE_CREDENTIALS": "/path/to/credentials.json"
      }
    }
  }
}
```

---

## Development Tools

### Memory Storage

```json
{
  "servers": {
    "memory": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-memory",
      "enabled": true,
      "description": "Persistent memory for conversation context"
    }
  }
}
```

**Available Tools**:
- `query_memory` - Query memories
- `create_memory` - Create memories
- `update_memory` - Update memories
- `delete_memory` - Delete memories

### Python REPL (Future)

```json
{
  "servers": {
    "python-repl": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-python-repl",
      "enabled": false,
      "description": "Python code execution (coming soon)"
    }
  }
}
```

### Shell Access (Advanced)

**WARNING**: Shell access is powerful and potentially dangerous. Use with caution.

```json
{
  "servers": {
    "shell": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-shell --allow-commands ls,cat,grep,find",
      "enabled": false,
      "description": "Restricted shell command execution"
    }
  }
}
```

---

## Advanced Configurations

### Environment Variables

Some servers require API keys or configuration via environment variables:

#### Method 1: In Configuration File

```json
{
  "servers": {
    "github": {
      "command": "npx -y @modelcontextprotocol/server-github",
      "env": {
        "GITHUB_TOKEN": "<your-github-token>",
        "GITHUB_API_URL": "https://api.github.com"
      }
    }
  }
}
```

#### Method 2: Shell Environment

Add to `~/.zshrc` or `~/.bashrc`:

```bash
export GITHUB_TOKEN="<your-github-token>"
export BRAVE_API_KEY="your-brave-api-key"
export EXA_API_KEY="your-exa-api-key"
```

### Multiple Instances of Same Server

You can run multiple instances of the same server type with different configurations:

```json
{
  "servers": {
    "fs-work": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/dev/work",
      "enabled": true
    },
    "fs-personal": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /Users/dev/personal",
      "enabled": true
    },
    "git-work": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository /Users/dev/work",
      "enabled": true
    },
    "git-personal": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository /Users/dev/personal",
      "enabled": true
    }
  }
}
```

### Project-Specific Configuration

Create project-specific MCP servers:

```bash
cd /path/to/project
mkdir -p .kollab/mcp
cat > .kollab/mcp/mcp_settings.json << 'EOF'
{
  "servers": {
    "project-git": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-git --repository .",
      "enabled": true
    },
    "project-files": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-filesystem /path/to/project",
      "enabled": true
    }
  }
}
EOF
```

Local project configs override global configs.

### Conditional Server Enablement

Use different configurations for different environments:

```json
{
  "servers": {
    "database-dev": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-postgres --connection-string postgresql://dev@localhost:5432/app",
      "enabled": true,
      "description": "Development database"
    },
    "database-prod": {
      "type": "stdio",
      "command": "npx -y @modelcontextprotocol/server-postgres --connection-string postgresql://prod@production.example.com:5432/app",
      "enabled": false,
      "description": "Production database (disabled by default)"
    }
  }
}
```

---

## Troubleshooting

### Server Won't Start

1. Check npx is installed: `npx --version`
2. Test server command manually:
   ```bash
   npx -y @modelcontextprotocol/server-filesystem /tmp
   ```
3. Check logs: `~/.kollab/projects/*/logs/kollab.log`

### Permission Errors

1. Verify filesystem server allowed paths
2. Check API tokens are valid
3. Ensure database connection strings are correct

### Tools Not Available

1. Verify server is enabled in config
2. Check server connected successfully (use `/mcp servers`)
3. Review logs for connection errors

---

## Best Practices

### Security

1. **Principle of Least Privilege**: Only enable servers you need
2. **Restrict File Paths**: Only specify necessary directories
3. **Use Scoped Tokens**: Create API tokens with minimum required permissions
4. **Disable Unused Servers**: Keep disabled servers set to `"enabled": false`
5. **Never Commit Tokens**: Use environment variables for sensitive data

### Performance

1. **Disable Unused Servers**: Reduces startup time
2. **Use Local Servers**: Prefer local filesystem over cloud services
3. **Limit Tool Count**: Too many tools can impact performance

### Organization

1. **Use Descriptive Names**: `fs-work` instead of `filesystem1`
2. **Group by Function**: Keep all filesystem servers together
3. **Document Purpose**: Use description field for each server
4. **Project-Specific Configs**: Use `.kollab/mcp/` for project tools

---

## Resources

- **MCP Specification**: https://modelcontextprotocol.io
- **Official MCP Servers**: https://github.com/modelcontextprotocol/servers
- **Server Development Guide**: https://modelcontextprotocol.io/docs/concepts/servers
- **Community Servers**: https://github.com/topics/mcp-server

---

## Contributing

Have an MCP server example to share? Please contribute!

1. Fork the repository
2. Add your example to this document
3. Submit a pull request

We welcome examples for:
- Cloud services (AWS, Azure, GCP)
- Additional databases (MongoDB, Redis)
- Development tools (Docker, Kubernetes)
- APIs and integrations
