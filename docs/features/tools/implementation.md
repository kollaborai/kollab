---
title: "Implementation"
created: 2026-02-24
modified: 2026-11-27
status: active
---
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
      "approval_mode": "trust_all",
      "blocked_tools": [],
      "trusted_tools": ["ls", "cat", "grep"]
    }
  }
}
```

