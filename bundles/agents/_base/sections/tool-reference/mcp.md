MCP (model context protocol) tools:

MCP servers provide external tools that the LLM can call alongside native tools.
servers are configured in mcp_settings.json and auto-connect on startup.

configuration locations (priority order):
  1. local project: .kollab/mcp/mcp_settings.json
  2. global:        ~/.kollab/mcp/mcp_settings.json

server schema:
  {
    "servers": {
      "server-name": {
        "type": "stdio",
        "command": "npx -y @modelcontextprotocol/server-filesystem /path",
        "enabled": true,
        "env": { "API_KEY": "value" }
      }
    }
  }

common servers:
  filesystem:  npx -y @modelcontextprotocol/server-filesystem /allowed/path
  git:         npx -y @modelcontextprotocol/server-git --repository .
  github:      npx -y @modelcontextprotocol/server-github (needs GITHUB_TOKEN env)
  brave-search: npx -y @modelcontextprotocol/server-brave-search (needs BRAVE_API_KEY env)
  sqlite:      npx -y @modelcontextprotocol/server-sqlite --db-path ./database.db
  postgres:    npx -y @modelcontextprotocol/server-postgres --connection-string <url>
  fetch:       npx -y @modelcontextprotocol/server-fetch
  memory:      npx -y @modelcontextprotocol/server-memory

slash commands:
  /mcp                    show server status
  /mcp show <server>      show server details and available tools
  /mcp add                add new server (interactive)
  /mcp remove <server>    remove server

tool execution:
  MCP tools appear in the tool list prefixed with the server name.
  they integrate with the permission system -- agent prompts for approval
  based on the current /permissions setting.

  example: mcp:filesystem::read_file reads a file via the filesystem server.

adding a new server:
  1. edit mcp_settings.json (global or project)
  2. add server entry with command, set enabled: true
  3. set any required env vars (API keys)
  4. restart kollabor or use /mcp to verify connection

security:
  [ok] restrict filesystem paths to only what the agent needs
  [ok] use scoped API tokens with minimum permissions
  [ok] keep API keys in env block or shell environment, never hardcoded
  [ok] disable servers you aren't actively using (enabled: false)

troubleshooting:
  - server not connecting? test command manually in shell
  - tools not showing? check enabled: true and restart
  - permission denied? check /permissions setting and allowed paths
  - logs: ~/.kollab/projects/*/logs/kollab.log
  - requires node.js 18+: check with node --version
