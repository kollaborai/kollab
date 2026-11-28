---
title: "On-demand Tool Loading"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## On-demand Tool Loading

Kollab registers two built-in discovery tools:

- `tool-search` searches the built-in and MCP catalogs by keyword and returns a
  compact name/source/description list. Its query is optional; without one it
  returns the available catalog.
- `tool-load` loads the full schema for a selected built-in or MCP tool into the
  current session. An MCP tool must come from the requested server and that
  server must be online.

This keeps every tool schema out of every request. The normal flow is:

```text
tool-search(query="browser") -> choose a result -> tool-load(name="...") -> call it
```

Agents can still be restricted by their `agent.json` `tools` field. `tools:
["*"]` exposes every registered tool to the bundle; a narrower list exposes
only named tools. Permission approvals remain a separate runtime gate.

