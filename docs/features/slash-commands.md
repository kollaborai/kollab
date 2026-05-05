---
title: "Slash Commands"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Slash Commands

Slash commands provide a way to interact with Kollab features and execute operations directly from the chat interface.

## Command Menu

Type `/` to open the command menu. Commands are filtered as you type:

```
/input>
  /

  Available Commands:
  ├─ help           Show available commands
  ├─ profile        Manage LLM profiles
  ├─ permissions    Manage permission settings
  ├─ mcp            Manage MCP servers
  └─ save           Save conversation
```

Use arrow keys to navigate, Enter to execute.

## Built-in Commands

### Core Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `?` | Show available commands |
| `/version` | - | Display version information |
| `/save` | - | Save conversation (transcript, markdown, jsonl, clipboard) |
| `/profile` | `/prof`, `/llm` | Manage LLM profiles (list, set, create) |
| `/permissions` | `/perms`, `/security` | Manage permission modes |
| `/mcp` | - | Manage MCP servers (show, add, remove) |
| `/resume` | - | Resume previous conversation |
| `/terminal` | `/tmux`, `/t` | Manage tmux sessions |
| `/login` | - | OAuth login for providers |
| `/matrix` | - | Matrix rain effect |
| `/widgets` | `showcase`, `widget-showcase`, `storybook` | Interactive widget gallery |

### Subcommands

Many commands support subcommands:

```
/profile          → Shows profile list
/profile list     → Lists available profiles
/profile set claude → Switches to claude profile
/profile create   → Create new profile

/mcp              → Shows server status
/mcp show         → Shows server details
/mcp add          → Add new server
/mcp remove       → Remove server
```

## Command Categories

Commands are organized by category:

- `SYSTEM` - Core system operations
- `LLM` - LLM and profile management
- `UI` - Interface and display settings
- `AGENT` - Agent and skill management
- `PERMISSION` - Security settings
- `MCP` - MCP server management
- `CUSTOM` - Plugin-provided commands

## CLI Invocation

Commands can be invoked from the command line:

```bash
# Execute command and exit
kollab --profile list

# Execute command, then enter interactive mode
kollab --profile list --stay

# Command with arguments
kollab --mcp show --stay
```

## Implementation

### Registry

`kollabor/commands/registry.py` - Central command registry

```python
from kollabor_events.models import CommandDefinition

command_def = CommandDefinition(
    name="mycommand",
    description="My custom command",
    category=CommandCategory.CUSTOM,
    aliases=["mc", "mycmd"],
    handler=my_handler_function,
    subcommands=[
        SubcommandInfo("action1", "", "Execute action 1"),
        SubcommandInfo("action2", "<arg>", "Execute action 2"),
    ]
)
command_registry.register_command(command_def)
```

### Executor

`kollabor/commands/executor.py` - Handles command execution, error handling, and event bus integration.

### Parser

`kollabor/commands/parser.py` - Parses slash command input into structured commands.

## Plugin Commands

Plugins can register custom commands:

```python
from kollabor_events.models import CommandDefinition, CommandCategory

class MyPlugin(BasePlugin):
    def register_hooks(self, event_bus):
        cmd = CommandDefinition(
            name="mycommand",
            description="Plugin command",
            category=CommandCategory.CUSTOM,
            plugin_name="my_plugin",
            handler=self._handle_command,
        )
        self.command_registry.register_command(cmd)
```

## Reserved Commands

These commands cannot be overridden by plugins (from registry.py):
- `help`, `version`, `config`, `status`
- `permissions`, `profile`, `agent`, `skill`
- `model`, `cd`

note: `/config` and `/status` are listed as reserved but not implemented.
      `/agent` and `/skill` are reserved but not implemented.

## Command Result Display

Commands return `CommandResult` objects:

```python
from kollabor_events.models import CommandResult

return CommandResult(
    success=True,
    message="Operation completed",
    display_type="success",  # info, warning, error
    data={"key": "value"},
)
```

## Events

Commands emit events:
- `SLASH_COMMAND_DETECTED` - Command recognized
- `SLASH_COMMAND_EXECUTE` - Execution started
- `SLASH_COMMAND_COMPLETE` - Execution finished
- `SLASH_COMMAND_ERROR` - Execution failed
