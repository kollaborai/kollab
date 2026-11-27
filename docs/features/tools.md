---
title: "Tool Calling"
created: 2026-02-24
modified: 2026-11-27
status: active
---
# Tool Calling

Tool calling (also called function calling) allows the LLM to request actions that
the system executes on its behalf. The LLM doesn't run commands directly -- it
outputs structured requests that Kollabor executes, then returns results back to
the LLM.

## Sections

- [Overview](tools/overview.md)
- [Execution Flow](tools/execution-flow.md)
- [Built-in Tools](tools/built-in-tools.md)
- [Tool Discovery and Registration](tools/tool-discovery-and-registration.md)
- [On-demand Tool Loading](tools/on-demand-tool-loading.md)
- [Large Tool Results and Context Budgets](tools/large-tool-results-and-context-budgets.md)
- [Tool Calling Methods](tools/tool-calling-methods.md)
- [Adding Custom Tools via Plugins](tools/adding-custom-tools-via-plugins.md)
- [Tool Output Formatting](tools/tool-output-formatting.md)
- [Safety: Permission System](tools/safety-permission-system.md)
- [Implementation](tools/implementation.md)
- [Troubleshooting](tools/troubleshooting.md)
