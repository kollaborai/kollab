---
title: "Safety: Permission System"
created: 2026-02-24
modified: 2026-11-27
status: active
---
## Safety: Permission System

All tool execution goes through the permission system. See [permissions.md](../permissions.md)
for complete documentation.

### Risk Levels

- LOW - Read operations, safe commands (`ls`, `cat`)
- MEDIUM - Package installs, git operations
- HIGH - Destructive commands (`rm`, file writes)
- UNKNOWN - New tools

### Approval Prompt

```
[tool] terminal: git commit -m "fix"
[risk] MEDIUM

approve? [o]nce/[s]ession/[p]roject/[d]eny/[ESC] cancel
```

### Key Bindings

- `o` - Approve once
- `s` - Approve for session
- `p` - Approve for project (persistent)
- `d` - Deny
- `ESC` - Cancel entire response
- `A` - Always approve file edits (context-specific)
- `t` - Trust root command (context-specific)

### Blocked Tools

Dangerous tools can be blocked in config:

```json
{
  "kollabor": {
    "permissions": {
      "blocked_tools": ["rm", "dd", "mkfs"]
    }
  }
}
```

