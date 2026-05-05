---
title: "Event System Architecture"
doc_type: architecture-reference
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Event System Architecture

The event system (kollabor-events) is the central nervous system of Kollabor.
Every action emits events that plugins can hook into at any priority level.

## Core Components

### EventBus (Central Coordinator)

```python
from kollabor_events import EventBus

event_bus = EventBus(config={"hook_defaults": {...}})
```

Coordinates:
- HookRegistry: Hook registration and lookup
- HookExecutor: Hook execution with error handling
- EventProcessor: Sequential event processing

Service registration (for DI):
```python
event_bus.register_service("llm_service", llm_service)
event_bus.register_service("permission_manager", perm_manager)
llm = event_bus.get_service("llm_service")
```

### EventType Enum

All available events in the system.

```python
from kollabor_events import EventType

# User input
EventType.USER_INPUT_PRE
EventType.USER_INPUT
EventType.USER_INPUT_POST

# LLM
EventType.LLM_REQUEST_PRE
EventType.LLM_REQUEST
EventType.LLM_REQUEST_POST
EventType.LLM_RESPONSE_PRE
EventType.LLM_RESPONSE
EventType.LLM_RESPONSE_POST

# Tools
EventType.TOOL_CALL_PRE
EventType.TOOL_CALL
EventType.TOOL_CALL_POST

# MCP
EventType.MCP_SERVER_CONNECT
EventType.MCP_SERVER_CONNECTED
EventType.MCP_TOOL_CALL_PRE
EventType.MCP_TOOL_CALL_POST

# Permissions
EventType.PERMISSION_CHECK
EventType.PERMISSION_GRANTED
EventType.PERMISSION_DENIED

# System
EventType.SYSTEM_STARTUP
EventType.SYSTEM_READY
EventType.RENDER_FRAME
```

### Hook Model

Defines a hook registration.

```python
from kollabor_events import Hook, HookPriority

hook = Hook(
    plugin_name="my_plugin",
    name="transform_input",
    event_type=EventType.USER_INPUT_PRE,
    callback=lambda data, event: data,
    priority=HookPriority.PREPROCESSING.value,  # 500
)

await event_bus.register_hook(hook)
```

### HookPriority

Execution order: higher priority runs first.

```python
SYSTEM = 1000      # Core framework (rarely used by plugins)
SECURITY = 900     # Permission checks
PREPROCESSING = 500 # Input transformation
LLM = 100          # LLM request/response
POSTPROCESSING = 50 # Response processing
DISPLAY = 10       # UI rendering
```

## Event Flow

```
component emits event
    ↓
event_bus.emit(EventType.USER_INPUT, context)
    ↓
processor.process_event()
    ↓
registry.get_hooks(event_type)
    ↓
executor.execute_hooks(hooks, context)
    ↓
for hook in sorted_hooks_by_priority:
    result = await hook.handler(context)
    if result is not None:
        context = result  # Transform context
    ↓
return final context
```

## Hook Registration Pattern

Plugins register hooks in their register_hooks() method:

```python
from kollabor_plugins import BasePlugin
from kollabor_events import EventType, Hook, HookPriority

class MyPlugin(BasePlugin):
    async def register_hooks(self):
        hook = Hook(
            plugin_name=self.name,
            name="log_request",
            event_type=EventType.LLM_REQUEST_PRE,
            callback=self._log_request,
            priority=HookPriority.LLM.value,  # Use .value for int priority
        )
        await self.event_bus.register_hook(hook)

    async def _log_request(self, data, event):
        """Hook callback receives (event_data, event_object)."""
        content = data.get("content", "")
        logger.info(f"Request: {content}")
        return data  # Always return data for transformation
```

## Context Transformation

Hooks transform data by returning modified dict:

```python
async def censor_input(self, data, event):
    """Hook callback receives (event_data, event_object)."""
    content = data.get("content", "")
    data["content"] = content.replace("secret", "***")
    return data  # Transformed
```

Return None to block transformation.

## Error Handling

Hook error_action controls behavior:

```python
hook = Hook(
    ...
    error_action="continue"  # Log error, continue to next hook
    # or "stop" to stop processing
)
```

Default is "continue". Errors are logged but don't stop execution.

## Config Hooks (JSON)

Plugins can be configured via .kollab/hooks.json:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "file_create|file_edit",
        "hooks": [
          {
            "type": "command",
            "command": "python guard.py"
          }
        ]
      }
    ]
  }
}
```

See docs/features/config-hooks.md for full reference.

## Service Locator Pattern

Components register services for cross-component communication:

```python
# Register
event_bus.register_service("tool_executor", tool_executor)

# Lookup (from anywhere)
tool_executor = event_bus.get_service("tool_executor")

# Check existence
if event_bus.has_service("tool_executor"):
    ...
```

## Key Imports

```python
from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_events.models import CommandResult, ConversationMessage
from kollabor_events.registry import HookRegistry
from kollabor_events.executor import HookExecutor
from kollabor_events.processor import EventProcessor
```

## Event Timing

- USER_INPUT_PRE: Before input is processed
- USER_INPUT: Main input processing
- USER_INPUT_POST: After input processing

- LLM_REQUEST_PRE: Before API call (can modify request)
- LLM_REQUEST: API call in progress
- LLM_REQUEST_POST: After API response received

- TOOL_CALL_PRE: Before tool execution (permission check)
- TOOL_CALL: Tool executing
- TOOL_CALL_POST: After tool completes

- RENDER_FRAME: Every render frame (30 FPS target)

## Best Practices

1. Always return context from hooks (even if unmodified)
2. Use appropriate priority (SECURITY for permissions, LLM for request/response)
3. Don't block in hooks (use asyncio for async operations)
4. Register hooks in register_hooks(), not __init__
5. Handle errors gracefully (log and continue)
