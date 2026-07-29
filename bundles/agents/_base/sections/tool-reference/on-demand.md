on-demand tools (discover and load tools at runtime):

on-demand tool loading lets you discover and activate tools without
having all 96+ MCP tool schemas bloating your context window upfront.
use tool-search to find what you need, then tool-load to activate it.

tool-search — find available tools by keyword:
  <tool-search><query>file</query></tool-search>
  <tool-search><query>database</query></tool-search>
  <tool-search><query>mcp github</query></tool-search>

  parameters:
    query  (required)  keyword to search (matched against names,
                       categories, and descriptions)

  behavior:
    - searches ALL tools: built-in (66) + MCP-discovered (96+)
    - case-insensitive substring match
    - results ranked: name matches first, then description matches
    - returns compact one-line-per-tool format
    - read-only: does not modify your active tool set
    - on-demand tools (tool-search, tool-load) excluded from results

  result format:
    Found N tool(s) matching 'query':

      tool-name  [built-in]  One-line description.
      mcp:server:tool  [MCP (server)]  One-line description.

    Use tool-load to activate any of these tools.

  error modes:
    - no query provided
    - no matches (returns success with zero count)

tool-load — activate a tool for use in this session:
  <tool-load><name>git</name></tool-load>
  <tool-load><name>mcp:github:create_issue</name></tool-load>

  parameters:
    name  (required)  exact tool name from tool-search results

  behavior:
    - for built-in tools: adds to bundle scope + returns full docs
    - for MCP tools: marks enabled + returns parameter schema
    - loaded tools persist for the rest of the session
    - loading an already-active tool is a safe no-op
    - accepts native name (hub_msg) or canonical name (hub-msg)

  result format:
    Tool loaded: <name>
    (full markdown documentation with parameters, examples, etc.)

  error modes:
    - tool not found (suggests using tool-search)
    - MCP tool not found in registry
    - MCP server not connected (suggests mcp-reload)
    - wrong MCP server name (corrects with actual server name)
    - invalid MCP name format (expected mcp:server:tool_name)

typical workflow:
  1. search:   <tool-search><query>github issue</query></tool-search>
  2. review:   results show mcp:github:create_issue [MCP (github)]
  3. load:     <tool-load><name>mcp:github:create_issue</name></tool-load>
  4. use:      the tool is now active — call it as an MCP tool

notes:
  - always tool-search first when you need a capability you don't have
  - broad queries ('file') return many results; narrow with specific terms
  - MCP tools are prefixed with mcp:server_name:tool_name
  - loading a tool adds it to your bundle scope permanently for the session
  - tool-search is read-only and safe to call repeatedly
  - tool-load requires permission (medium risk — adds new capabilities)
