---
title: "Tool Permission System"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Tool Permission System

The permission system controls which tools the LLM can execute, with configurable approval modes and risk-based assessment.

## Overview

When the LLM wants to execute a tool (run a shell command, write a file, call an MCP tool), the permission system:
1. Assesses the risk level of the operation
2. Checks existing approvals
3. Prompts for confirmation if needed
4. Records the approval decision

## Approval Modes

Configure via `/permissions` or `config.json`:

| Mode | Behavior |
|------|----------|
| `confirm_all` | Confirm every tool execution |
| `default` | Confirm HIGH/UNKNOWN risk only |
| `auto_approve_edits` | Auto-approve file edits, confirm shell |
| `trust_all` | Auto-approve everything (dangerous) |

note: the code uses ApprovalMode enum with values:
  CONFIRM_ALL, DEFAULT, AUTO_APPROVE_EDITS, TRUST_ALL

### Setting Modes

```bash
/permissions default      # Use default mode
/permissions confirm_all  # Confirm everything
/permissions trust        # Trust all (not recommended)
```

Or in `config.json`:

```json
{
  "kollabor": {
    "permissions": {
      "approval_mode": "default"
    }
  }
}
```

## Risk Levels

| Level | Description | Examples |
|-------|-------------|----------|
| `LOW` | Safe operations | `ls`, `cat`, `grep`, file reads |
| `MEDIUM` | Needs attention | Package installs, git operations |
| `HIGH` | Dangerous | `rm`, `dd`, file writes to sensitive paths |
| `UNKNOWN` | Unclassified | New tools, unusual operations |

note: the code uses ToolRiskLevel enum with values:
  LOW, MEDIUM, HIGH, UNKNOWN

## Confirmation Prompt

When confirmation is needed, an inline prompt appears:

```
[tool] terminal: git commit -m "fix"
[risk] MEDIUM

approve? [o]nce/[s]ession/[p]roject/[d]eny/[ESC] cancel
```

### Response Keys

| Key | Action | Scope |
|-----|--------|-------|
| `o` | Approve once | Single execution |
| `s` | Approve session | Rest of session |
| `p` | Approve project | All future sessions in project |
| `d` | Deny | Block this execution |
| `ESC` | Cancel | Cancel entire LLM response |

### Context-Specific Keys

For file operations:
- `A` - Always approve file edits

For shell commands:
- `t` - Trust this root command

## Approval Scopes

### Once (o)
Approves only this specific execution. Next time requires re-approval.

### Session (s)
Approves for the current session only. Reset when Kollabor restarts.

### Project (p)
Persists approval to project config (`.kollab/projects/<path>/permission_approvals.json`).

Uses smart matching:
- `file_read:*` - All file reads (except gitignored)
- `terminal:ls` - Specific root command (no chains)

#### Wildcard Protection

Smart approvals have safety checks:
- Chained commands (`cmd1 && cmd2`) blocked from wildcard matching
- Gitignored files blocked from `file_read:*` matching

```bash
# These require explicit approval even with `terminal:ls` approved
ls && rm -rf /  # Chained - blocked
cat .env        # Gitignored - blocked if `file_read:*` only
```

## Risk Assessment

Risk is determined by:
1. Tool type (terminal, file_write, mcp_tool)
2. Command patterns (regex)
3. Configuration rules

### Blocked Tools

Certain tools can be blocked entirely via config:

```json
{
  "kollabor": {
    "permissions": {
      "blocked_tools": ["rm", "dd", "mkfs"]
    }
  }
}
```

### Trusted Tools

Tools that auto-approve without confirmation:

```json
{
  "kollabor": {
    "permissions": {
      "trusted_tools": ["ls", "cat", "grep", "find"]
    }
  }
}
```

### Risk Patterns

Commands matching these patterns are flagged:

```json
{
  "kollabor": {
    "permissions": {
      "high_risk_patterns": ["rm\\s+-rf", "dd\\s+", ">\\s*/dev/"],
      "medium_risk_patterns": ["apt-get", "pip install", "npm install"]
    }
  }
}
```

## Permission States

The system tracks:
- `session_approvals` - In-memory, cleared on exit
- `project_approvals` - Persistent in project directory

## CLI Commands

| Command | Description |
|---------|-------------|
| `/permissions` | Show current mode and stats |
| `/permissions default` | Set default mode |
| `/permissions strict` | Confirm everything |
| `/permissions trust` | Trust all (dangerous) |
| `/permissions stats` | Show approval statistics |
| `/permissions clear` | Clear session approvals |

## Events

The permission system emits events:
- `PERMISSION_CHECK` - Permission check started
- `PERMISSION_CONFIRMATION` - User confirmation requested
- `PERMISSION_GRANTED` - Permission approved
- `PERMISSION_DENIED` - Permission denied

## Implementation

Located in `packages/kollabor-agent/src/kollabor_agent/permissions/`:

- `manager.py` - Central permission manager
- `risk_assessor.py` - Risk-based assessment
- `response_handler.py` - User response processing

Integration via event bus at SECURITY priority (900).
