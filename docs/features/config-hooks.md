---
title: "Config Hooks (JSON Hook System)"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Config Hooks (JSON Hook System)

## Overview

Config hooks let you run shell commands on Kollab events using a simple
JSON file. No Python plugin required. The format is compatible with Claude
Code's `hooks.json`, so hooks written for Claude Code work in Kollab
out of the box - plus you get access to 60+ additional Kollab-specific
events.

## Quick Start

Create `.kollab/hooks.json` in your project root:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "file_create|file_edit",
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/check_write.py"
          }
        ]
      }
    ]
  }
}
```

Restart Kollab. Now every time a `file_create` or `file_edit` tool call is about to
execute, your script runs. It receives event data on stdin as JSON and
can block, modify, or just observe the event.

## File Locations

Hooks are loaded from two locations and merged (project wins on conflict):

| Location | Scope |
|---|---|
| `.kollab/hooks.json` | Project-specific (highest priority) |
| `~/.kollab/hooks.json` | Global (base layer) |

When both files define hooks for the same event, project hooks are
appended after global hooks.

## JSON Format

```json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "optional_regex",
        "hooks": [
          {
            "type": "command",
            "command": "your-command --arg",
            "timeout": 30,
            "async": false,
            "priority": "POSTPROCESSING"
          }
        ]
      }
    ]
  }
}
```

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | `"command"` | Only `"command"` is supported |
| `command` | string | required | Shell command to run |
| `timeout` | int | `30` | Seconds before killing the process |
| `async` | bool | `false` | Fire-and-forget (don't wait for result) |
| `priority` | string/int | `"POSTPROCESSING"` | Hook execution priority |
| `matcher` | string | `""` | Regex filter (empty = match all) |

### Priority Values

| Name | Value | Description |
|---|---|---|
| `SYSTEM` | 1000 | Internal system hooks |
| `SECURITY` | 900 | Permission/security checks |
| `PREPROCESSING` | 500 | Before main processing |
| `LLM` | 100 | LLM request/response processing |
| `POSTPROCESSING` | 50 | After main processing (default) |
| `DISPLAY` | 10 | UI/display hooks |

You can also pass an integer directly for fine-grained control.

## Event Names

### Claude Code Compatible

These event names match Claude Code's hook system:

| Name | Kollab Event | Fires When |
|---|---|---|
| `SessionStart` | SYSTEM_STARTUP | App starts up |
| `SessionEnd` | SYSTEM_SHUTDOWN | App shuts down |
| `UserPromptSubmit` | USER_INPUT_PRE | User submits a message |
| `PreToolUse` | TOOL_CALL_PRE | Before a tool executes |
| `PostToolUse` | TOOL_CALL_POST | After a tool executes |
| `PostToolUseFailure` | TOOL_CALL_POST | After a tool fails (success=false filter) |
| `PermissionRequest` | PERMISSION_CHECK | Permission check triggered |
| `Stop` | LLM_RESPONSE_POST | LLM response completed |

### Kollab Native

Additional events only available in Kollab:

| Name | Kollab Event | Fires When |
|---|---|---|
| `LlmRequestPre` | LLM_REQUEST_PRE | Before LLM API call |
| `LlmRequestPost` | LLM_REQUEST_POST | After LLM API call |
| `LlmThinking` | LLM_THINKING | LLM thinking/reasoning |
| `McpServerConnect` | MCP_SERVER_CONNECT | MCP server connecting |
| `McpServerDisconnect` | MCP_SERVER_DISCONNECT | MCP server disconnected |
| `McpToolCallPre` | MCP_TOOL_CALL_PRE | Before MCP tool call |
| `McpToolCallPost` | MCP_TOOL_CALL_POST | After MCP tool call |
| `SlashCommandExecute` | SLASH_COMMAND_EXECUTE | Slash command executing |
| `SlashCommandComplete` | SLASH_COMMAND_COMPLETE | Slash command finished |
| `ShellCommandPre` | SHELL_COMMAND_PRE | Before shell command |
| `ShellCommandPost` | SHELL_COMMAND_POST | After shell command |
| `KeyPress` | KEY_PRESS | Key press detected |
| `PasteDetected` | PASTE_DETECTED | Paste detected |
| `CancelRequest` | CANCEL_REQUEST | User cancelled action |
| `RenderFrame` | RENDER_FRAME | Render frame update |
| `ModalShow` | MODAL_SHOW | Modal opened |
| `ModalHide` | MODAL_HIDE | Modal closed |

You can also use the raw enum values (`user_input_pre`, `tool_call_pre`)
or the uppercase names (`USER_INPUT_PRE`, `TOOL_CALL_PRE`).

## Stdin Payload

Your command receives JSON on stdin with these common fields:

```json
{
  "session_id": "abc123",
  "cwd": "/path/to/project",
  "hook_event_name": "PreToolUse",
  "timestamp": 1739800000.0
}
```

Plus all event-specific fields. For example, `PreToolUse` for a file
operation includes:

```json
{
  "tool_name": "file_create",
  "type": "file_create",
  "file": "foo.py",
  "content": "..."
}
```

## Stdout Response

Your command communicates back via exit code and stdout JSON.

### Exit Codes

| Code | Behavior |
|---|---|
| `0` | Success - parse stdout for response |
| `2` | Block the event - stderr becomes the reason |
| Other | Log warning, continue normally |

### Response JSON (exit 0)

```json
{
  "continue": true,
  "decision": "allow",
  "reason": "explanation shown to user on deny",
  "data": { "key": "merged into event data" },
  "systemMessage": "logged as warning"
}
```

All fields are optional:

| Field | Type | Description |
|---|---|---|
| `continue` | bool | `false` to block the event |
| `decision` | string | `"deny"` to block the event |
| `reason` | string | Shown to user when blocked |
| `data` | object | Merged into event data (transforms the event) |
| `systemMessage` | string | Logged as a warning message |

Exit 0 with no stdout or non-JSON stdout: event proceeds normally.

## Matchers

Matchers are regex patterns checked against a field that depends on the
event type:

| Event | Field Checked |
|---|---|
| `PreToolUse` / `PostToolUse` | `tool_name` |
| `McpToolCallPre` / `McpToolCallPost` | `{server_name}__{tool_name}` |
| `SlashCommandExecute` / `SlashCommandComplete` | `command` |
| `ShellCommandPre` / `ShellCommandPost` | `command` |
| `SessionStart` | `source` |
| `PermissionRequest` | `tool_name` |
| `KeyPress` | `key` |

No matcher (or empty string): hook fires on all events of that type.

## Examples

### Log all tool calls

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python -c \"import sys,json,datetime; d=json.load(sys.stdin); open('/tmp/tool.log','a').write(f'{datetime.datetime.now()} {d.get(\\\"tool_name\\\",\\\"?\\\")}\\n')\"",
            "async": true
          }
        ]
      }
    ]
  }
}
```

### Block writing to specific files

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "file_create|file_create_overwrite|file_edit",
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/guard_writes.py"
          }
        ]
      }
    ]
  }
}
```

Where `guard_writes.py`:

```python
import sys, json, re

data = json.load(sys.stdin)
path = data.get("file", "") or data.get("path", "") or data.get("arguments", {}).get("path", "")

blocked = [r"\.env", r"secrets\.json", r"\.ssh/"]
for pattern in blocked:
    if re.search(pattern, path):
        print(f"Blocked write to sensitive file: {path}", file=sys.stderr)
        sys.exit(2)
```

### Add context before every LLM request

```json
{
  "hooks": {
    "LlmRequestPre": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .kollab/inject_context.py"
          }
        ]
      }
    ]
  }
}
```

### Run linter after shell commands

```json
{
  "hooks": {
    "ShellCommandPost": [
      {
        "matcher": "git commit",
        "hooks": [
          {
            "type": "command",
            "command": "python -m flake8 --max-line-length=88 kollabor/",
            "async": true,
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

## Architecture

The system is implemented in `kollabor/config_hooks.py` as a single
class (`ConfigHookLoader`) that:

1. Reads and merges hooks.json from project + global locations
2. Resolves event names through the alias maps
3. Creates async callbacks that spawn subprocesses
4. Registers Hook objects on the existing event bus

No changes to the event system. Config hooks are regular hooks that
happen to run external processes instead of Python code.

Subprocesses use `asyncio.create_subprocess_exec` (never
`asyncio.to_thread` + `subprocess.run`, which causes SIGTTIN in
terminal apps). Each process runs in its own session group
(`start_new_session=True`) for clean timeout cleanup.

Config hooks load during `_deferred_startup()`, after plugins, so they
can observe or override plugin behavior.

## Limitations

- Only `type: "command"` is supported (no `"prompt"` or `"agent"` types)
- Changes to hooks.json require restarting Kollab (no hot reload yet)
- Hooks that read from stdin must consume the full payload before writing
  to stdout
