---
name: kollab-internal-tools
description: Discover and use Kollab's own agent tools, conditional plugin tools, and runtime-supplied MCP tools when working in a Kollab session.
---

# Kollab tools

Tool availability depends on the active agent's scope, provider mode, enabled
plugins and services, and current permissions. A name in these references is
not proof that it is callable in this session. The runtime's schema and actual
result take precedence; never claim an action succeeded without its result.

- Read [local tools](references/local-tools.md) for files, terminal, web,
  workspace, tool discovery, MCP reload, and voice.
- Read [Hub tools](references/hub-tools.md) for messaging, agents, work,
  coordination, vaults, and recurring messages.
- Read [session and plugin tools](references/session-tools.md) for context,
  notifications, goal reporting, orchestrator tags, and plugin SDK services.

The references name the actual effect and permission metadata. Permissions,
hooks, voice restrictions, user authorization and service-specific checks still
apply; loading or seeing a tool does not authorize its side effects. CLI flags
and slash commands are separate surfaces, described in the references.

External MCP tools are supplied at runtime; there is no complete bundled list.
Use the session's connected server/tool list or `tool_search`, then `tool_load`
with the returned `mcp:server:tool` name when needed. Inspect the returned
schema, server identity, permissions, and side effects before calling. A search
result does not establish that a server is connected or a call is permitted.
If the returned schema is empty or incomplete, inspect the server's own current
definition instead of guessing its arguments.
External calls may be native tool calls or XML `<tool name="...">` calls,
depending on the active provider and session; use the discovered server schema.
Third-party plugins can also supply runtime tools or handlers. Consult the
active session and plugin documentation; `tool_search` does not enumerate all
plugin-only handlers, and the bundled catalog cannot predict arbitrary plugins.
`/mcp tools [server]` and `/mcp status` are user-facing alternatives; reloading
connections has effects and requires its own authorization.
