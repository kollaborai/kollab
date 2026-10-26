# Tool Permission System

Comprehensive approval and permission system for controlling tool execution in Kollab.

## Overview

The permission system provides fine-grained control over LLM tool execution, allowing users to approve or deny operations based on risk level and approval mode preferences.

## Components

### Core Modules

- **`manager.py`** - Central permission manager
  - Manages approval modes (DEFAULT, CONFIRM_ALL, AUTO_APPROVE_EDITS, TRUST_ALL)
  - Tracks session-scoped approvals
  - Coordinates with risk assessor and UI callbacks
  - Statistics tracking (total checks, approved, denied, blocked)

- **`risk_assessor.py`** - Risk assessment engine
  - Pattern-based detection for dangerous shell commands
  - Tool type classification (terminal, file_create, file_edit, mcp_tool)
  - Configurable trusted and blocked tool lists
  - Risk levels: HIGH, MEDIUM, LOW, UNKNOWN

- **`hook.py`** - Event bus integration
  - Registers at SECURITY priority (900) on TOOL_CALL_PRE event
  - Intercepts tool execution before it happens
  - Sets `event.cancelled = True` if permission denied
  - Adds `permission_decision` to event data for downstream hooks

- **`models.py`** - Data structures
  - `ApprovalMode` - Enum for approval modes
  - `ToolRiskLevel` - Enum for risk classification
  - `PermissionDecision` - Result of permission check
  - `ApprovalRecord` - Session approval tracking
  - `RiskAssessmentRules` - Pattern and tool type mappings
  - `RiskAssessmentResult` - Risk assessment output

- **`config.py`** - Configuration defaults
  - Default approval mode: `trust_all`
  - Risk assessment patterns and rules
  - UI settings (timeout, default response)
  - Audit logging configuration

- **`response_handler.py`** - User response processing
  - Converts `ConfirmationResponse` to `PermissionDecision`
  - Handles session approval recording
  - Switches approval modes based on user choice

### UI Component

- **`core/io/status/permission_status_view.py`** - Inline permission prompt
  - Renders in thinking/executing area (no modal interruption)
  - Single keypress responses: a/s/d/c/t/A/ESC
  - Color-coded risk levels (HIGH=red, MEDIUM=yellow, LOW=green)
  - Uses Box() design system style with solid blocks

## Approval Modes

### TRUST_ALL (Default)
Auto-approve **everything**. Dangerous - use with caution.

```bash
/permissions trust
```

### CONFIRM_ALL
Require confirmation for **all** tool executions. Most secure mode.

```bash
/permissions strict
```

### DEFAULT
Confirm only **HIGH** risk tools. Auto-approve LOW and MEDIUM risk operations.

```bash
/permissions default
```

### AUTO_APPROVE_EDITS
Auto-approve file operations (file_write, file_edit, file_create). Confirm shell commands.

```bash
# Not directly accessible via command
# Activated by "always approve edits" response in prompt
```

## Risk Levels

### HIGH Risk
Requires confirmation in DEFAULT mode. Always blocked if matched as dangerous pattern.

**Examples:**
- Shell commands: `rm -rf /`, `curl | bash`, `chmod 777`
- Dangerous patterns: fork bombs, device writes, system modifications

### MEDIUM Risk
Auto-approved in DEFAULT mode. Requires confirmation in CONFIRM_ALL mode.

**Examples:**
- Terminal commands (without dangerous patterns)
- File write/create operations
- MCP tool executions

### LOW Risk
Auto-approved in all modes except CONFIRM_ALL.

**Examples:**
- File read operations
- Search operations
- List directory operations

### UNKNOWN Risk
Treated as HIGH risk until assessed. Requires confirmation in DEFAULT mode.

**Examples:**
- New/unrecognized tool types
- Tools not in risk assessment rules

## Permission Flow

```
1. LLM requests tool execution
   ↓
2. tool_executor.py emits TOOL_CALL_PRE event
   ↓
3. PermissionHook intercepts at SECURITY priority
   ↓
4. RiskAssessor evaluates tool risk
   ↓
5. PermissionManager checks approval rules
   ↓
6. If confirmation needed → show inline UI prompt
   ↓
7. User responds with keypress
   ↓
8. ResponseHandler converts to PermissionDecision
   ↓
9. If denied → event.cancelled = True
   ↓
10. tool_executor checks cancelled flag
    ↓
11. Execute or abort based on permission
```

## User Interface

### Permission Prompt

```
Terminal command (HIGH risk):
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
   PERMISSION REQUIRED                                    HIGH
   Bash(rm -rf ./node_modules)
   a approve   s session   p this cmd   d deny   c cancel
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀

File edit (MEDIUM risk):
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
   PERMISSION REQUIRED                                    MEDIUM
   Edit(src/config.py)
   a approve   s session   p project   A always edits   d deny
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀

MCP tool (MEDIUM risk):
▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
   PERMISSION REQUIRED                                    MEDIUM
   MCP(puppeteer/puppeteer_navigate)
   a approve   s session   p project   t trust tool   d deny
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
```

Tool format: `Bash(command)`, `Edit(path)`, `Write(path)`, `Read(path)`, `MCP(server/tool)`

### Response Options

**Always available:**
- **`a`** - Approve once (this execution only)
- **`s`** - Approve for session (remember for this session)
- **`p`** - Approve for project (label varies by tool type)
- **`d`** - Deny (block this execution)
- **`ESC`** - Cancel (same as deny)

**Context-specific:**
- **`A`** - Always approve edits (file write/edit only - switches to AUTO_APPROVE_EDITS mode)
- **`t`** - Trust this tool (MCP tools only - whitelist this specific tool)
- **`c`** - Cancel operation (terminal commands only - abort entire operation)

**Project approval labels by tool type:**
- `file_read` → "all reads" (approves all file reads, gitignored files still protected)
- `terminal` → "this cmd" (approves this specific command)
- other → "project"

**Stop and timeout:** a Stop (ESC, the web UI's Stop, a voice talk-over) ends
the turn at once and parks every open prompt (`PermissionManager.park_confirmations`):
the tool comes back "Not run yet" and the card stays open. A prompt nobody
answers parks after 290 s (`PARK_AFTER_SECONDS`, under the hook's 5 min): the
agent writes that it needs the OK, and the card stays. A prompt asked while its
turn is stopping shows as a card and parks at once. Allow on a parked card runs
that call once and the agent goes on (an agent HUD note plus
`TRIGGER_LLM_CONTINUE`); Deny drops it quietly. The user's typed yes runs it too
and closes the card. Terminals close a parked prompt without answering
(`permission_parked`), since its keys would answer it mid-message. ACP's Stop
still answers its prompts cancelled (`cancel_confirmations`).

**Voice turns ask instead of waiting:** a voice turn never waits on a prompt.
Under Trust All it asks for nothing: it runs its tools as a typed turn does,
with no voice list (`ToolExecutor.trust_all`, which the PermissionManager keeps
in step with the approval mode). In the other modes, a call it may not run unasked (the voice guard's `confirmation_required`, or a
prompt in a Confirm mode) comes back "Not run yet" and the agent asks for the OK
in its reply; no card opens. The user's next message, spoken or typed, decides
(`kollabor_agent/yes_approval.py`): a short yes lets each call the agent's last
finished reply asked for run once (step 6 answers it "User approved (yes)"), a
short no drops them, and anything else is a normal turn that drops them too. A
yes said before that reply was finished, or over it by voice, answers nothing;
a voice ask stops counting after 5 minutes. The manager marks its own asks
(`PermissionDecision.needs_yes`), so a tool whose text reads like one plants
nothing, and only a person answering live answers (`YES_SOURCES`: the
terminal, an attached window, the web composer, voice): another agent's
message, a person's `kollab --hub msg`, the CLI's first message, a pipe or a
`/agent` or `/skill` generation prompt never does. By voice a
yes runs only the guard's voice list (read-only commands, git, file changes
inside the project, the project's own `make` and `npm` scripts, read-only `gh`
and `kollab --hub`); anything else comes
back `not_by_voice` and the agent offers to run it from a typed message. A
voice `file_create` or `file_edit` comes back `command_blocked` with the same
list, so the agent makes a file with the terminal's `touch` and asks.

## Commands

### `/permissions` (aliases: `/perms`, `/security`)

```bash
# Show current settings
/permissions

# Switch to strict mode (confirm everything)
/permissions strict

# Switch to default mode (HIGH risk only)
/permissions default

# Switch to trust mode (approve everything - DANGEROUS)
/permissions trust

# View statistics
/permissions stats

# Clear session approvals
/permissions clear
```

## Configuration

```json
{
  "kollabor.permissions": {
    "enabled": true,
    "approval_mode": "trust_all",
    "audit_log_enabled": true,
    "audit_log_path": "~/.kollab/logs/permissions.log",
    "risk_assessment": {
      "high_risk_patterns": [],
      "medium_risk_patterns": [],
      "trusted_tools": [
        "read_file",
        "list_directory",
        "search_file_content",
        "glob"
      ],
      "blocked_tools": [],
      "trust_mcp_servers": false,
      "trusted_mcp_servers": []
    },
    "ui": {
      "show_risk_level": true,
      "show_matched_pattern": true,
      "confirmation_timeout": 0,
      "timeout_response": "deny"
    }
  }
}
```

## Integration Points

### Application Initialization

```python
# kollabor/application.py
from kollabor.llm.permissions import (
    PermissionManager,
    RiskAssessor,
    PermissionHook,
    PERMISSION_CONFIG_DEFAULTS,
)

# Merge config defaults
config = deep_merge(PERMISSION_CONFIG_DEFAULTS, config)

# Initialize components
risk_assessor = RiskAssessor(rules, config)
permission_manager = PermissionManager(config, risk_assessor, event_bus)

# Wire UI callback
permission_manager.set_confirmation_callback(
    layout_manager.show_permission_prompt
)

# Register hook (async during startup)
permission_hook = PermissionHook(permission_manager)
await permission_hook.register(event_bus)
```

### Tool Executor

```python
# kollabor/llm/tool_executor.py

# Emit pre-execution event
pre_call_results = await event_bus.emit_with_hooks(
    EventType.TOOL_CALL_PRE,
    {"tool_data": tool_data},
    "tool_executor"
)

# Check if cancelled by permission system
if pre_call_results.get("cancelled", False):
    permission_decision = pre_call_results["main"]["final_data"]["permission_decision"]
    return ToolExecutionResult(
        success=False,
        error=permission_decision["reason"],
        metadata={"permission_denied": True}
    )

# Execute tool if approved
result = await execute_tool(tool_data)
```

## Statistics

The permission manager tracks:
- **Total checks** - Number of permission checks performed
- **Auto-approved** - Tools approved automatically (based on mode/risk)
- **User approved** - Tools approved by user via prompt
- **Denied** - Tools denied by user
- **Blocked** - Tools blocked by risk assessment (dangerous patterns)

View with: `/permissions stats`

## Session Approvals

Session approvals are stored in memory and cleared when:
- User runs `/permissions clear`
- Application exits
- User switches to TRUST_ALL mode

Session approvals remember:
- Terminal commands by root command (e.g., all `git` commands)
- File operations by type
- MCP tools by server and tool name

## Security Features

### Pattern Matching

High-risk patterns blocked automatically:
- `rm -rf /` or `rm -rf ~`
- `mkfs.*` (filesystem creation)
- `dd ... of=/dev/...` (device writes)
- `curl ... | bash` (download and execute)
- `chmod 777` (overly permissive)
- Fork bombs and malicious scripts

### Tool Type Defaults

Default risk levels by tool type:
- `terminal` - MEDIUM
- `file_create` - MEDIUM
- `file_write` - MEDIUM
- `file_edit` - LOW
- `file_read` - LOW
- `mcp_tool` - MEDIUM
- `search` - LOW

### Trusted Tools

Auto-approved regardless of mode:
- `read_file`
- `list_directory`
- `search_file_content`
- `glob`

## Development

### Adding Custom Risk Patterns

```python
# In config or at runtime
config["kollabor.permissions.risk_assessment.high_risk_patterns"].append(
    r"your_pattern_here"
)
```

### Custom Tool Types

```python
# In models.py RiskAssessmentRules
tool_type_risks: Dict[str, ToolRiskLevel] = field(default_factory=lambda: {
    "my_custom_tool": ToolRiskLevel.MEDIUM,
    # ...
})
```

## Testing

```bash
# Test permission system initialization
python3 -c "
from kollabor.llm.permissions import PermissionManager, RiskAssessor
from kollabor.llm.permissions.models import RiskAssessmentRules
from kollabor_events.bus import EventBus

config = {'kollabor.permissions': {'enabled': True, 'approval_mode': 'trust_all'}}
event_bus = EventBus(config)
risk_rules = RiskAssessmentRules()
risk_assessor = RiskAssessor(risk_rules, config)
permission_manager = PermissionManager(config, risk_assessor, event_bus)

print('Approval mode:', permission_manager.approval_mode)
print('Stats:', permission_manager.get_stats())
"
```

## Documentation

- **Spec**: `docs/specs/tool-permission-system-spec.md` - Complete specification
- **Features**: `FEATURES.md` - User-facing feature documentation
- **Changelog**: `CHANGELOG.md` - Implementation notes and changes
- **CLAUDE.md**: Integration and architecture notes

## Files

```
kollabor/llm/permissions/
├── __init__.py              Exports and documentation
├── models.py                Data structures and enums (241 lines)
├── risk_assessor.py         Risk assessment engine (111 lines)
├── manager.py               Permission manager (311 lines)
├── hook.py                  Event bus integration (101 lines)
├── config.py                Configuration defaults (58 lines)
├── response_handler.py      User response handling (102 lines)
└── README.md                This file

kollabor/io/status/
└── permission_status_view.py Inline UI component (161 lines)
```

Total: **1,085 lines** of implementation code
