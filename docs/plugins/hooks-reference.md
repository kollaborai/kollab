---
title: "Hooks Reference"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Hooks Reference

Complete reference for all event types, priorities, and hook execution in
Kollab.

## Hook Definition

Hooks are defined using the `Hook` model:

```python
from kollabor_events import Hook, EventType, HookPriority

hook = Hook(
    name="my_hook",              # Unique hook name
    plugin_name="myplugin",      # Your plugin name
    event_type=EventType.USER_INPUT,  # Event to listen for
    priority=100,                # Execution order (higher first)
    callback=self._my_handler,   # Async callback function
    enabled=True,                # Can be toggled at runtime
    timeout=30,                  # Max execution time (seconds)
    retry_attempts=3,            # Retries on failure
    error_action="continue"      # "continue" or "stop" on error
)
```

## HookPriority Levels

Priority controls execution order - higher numbers execute first.

```python
from kollabor_events import HookPriority

HookPriority.SYSTEM      # 1000 - Core system hooks
HookPriority.SECURITY    # 900  - Permission/security checks
HookPriority.PREPROCESSING  # 500 - Input/output preprocessing
HookPriority.LLM         # 100 - LLM-related operations
HookPriority.POSTPROCESSING # 50 - Post-processing
HookPriority.DISPLAY     # 10  - UI rendering
```

Custom priority values can be any integer. Negative values are allowed but
discouraged.

## Event Types

### User Input Events

```python
EventType.USER_INPUT_PRE      # Before input processing
EventType.USER_INPUT          # During input processing (main)
EventType.USER_INPUT_POST     # After input processing
```

**Data fields:**
* `text` (str) - Raw user input text
* `processed` (bool) - Whether input was preprocessed
* `original_input` (str) - Original unmodified input

**Use cases:** Custom aliases, input validation, command expansion

### Key Press Events

```python
EventType.KEY_PRESS_PRE       # Before key handling
EventType.KEY_PRESS           # During key handling (main)
EventType.KEY_PRESS_POST      # After key handling
```

**Data fields:**
* `key` (str) - Key name (e.g., "enter", "ctrl+c")
* `char` (str) - Character if printable
* `modifiers` (list) - Active modifiers ("ctrl", "shift", "alt")

**Use cases:** Custom keybindings, keyboard shortcuts

### Paste Events

```python
EventType.PASTE_DETECTED      # User pasted content
```

**Data fields:**
* `pasted_content` (str) - Pasted text
* `length` (int) - Character count

**Use cases:** Paste sanitization, format conversion

### LLM Request Events

```python
EventType.LLM_REQUEST_PRE     # Before API call
EventType.LLM_REQUEST         # During request building (main)
EventType.LLM_REQUEST_POST    # After API response received
EventType.CANCEL_REQUEST      # User cancelled request
```

**Data fields (PRE/REQUEST):**
* `messages` (list) - Message array to send
* `model` (str) - Model identifier
* `temperature` (float) - Sampling temperature
* `headers` (dict) - HTTP headers
* `stream` (bool) - Whether streaming

**Data fields (POST):**
* `response` (dict) - API response data
* `status_code` (int) - HTTP status
* `latency_ms` (int) - Request duration

**Use cases:** Add custom headers, modify messages, logging

### LLM Response Events

```python
EventType.LLM_RESPONSE_PRE    # Before response processing
EventType.LLM_RESPONSE         # During response processing (main)
EventType.LLM_RESPONSE_POST    # After response displayed
EventType.LLM_THINKING         # LLM is "thinking"
```

**Data fields:**
* `response_text` (str) - Raw LLM response (pre-parser)
* `clean_response` (str) - Parsed/cleaned response (tags stripped)
* `thinking_duration` (float) - Time spent waiting for response
* `tool_results` (None at this point) - Tool results come later

**Note:** LLM_RESPONSE is now observation-only. Plugin XML tags are
extracted and stripped by the response_parser before this event fires.
To register custom XML tags, use `register_plugin_tag()` and
`register_plugin_handler()` (see Plugin Development Guide). Hooks on
LLM_RESPONSE should only read data for logging/metrics, not parse or
strip tags.

**Use cases:** Response logging, metrics collection, bridge relay,
read-only monitoring

### Tool Events

```python
EventType.TOOL_CALL_PRE        # Before tool execution
EventType.TOOL_CALL            # During tool execution (main)
EventType.TOOL_CALL_POST       # After tool completes
```

**Data fields:**
* `tool_name` (str) - Tool being called
* `arguments` (dict) - Tool arguments
* `result` (dict) - Tool result (POST only)
* `error` (str) - Error message if failed

**Use cases:** Tool logging, permission checks, result caching

### MCP Events

```python
EventType.MCP_SERVER_CONNECT       # Server connection initiated
EventType.MCP_SERVER_CONNECTED     # Server successfully connected
EventType.MCP_SERVER_DISCONNECT    # Server disconnected
EventType.MCP_SERVER_ERROR         # Server connection error
EventType.MCP_SERVER_DISCOVER      # Discovery started
EventType.MCP_SERVER_DISCOVERED    # Discovery completed
EventType.MCP_TOOL_REGISTER        # Tool registered
EventType.MCP_TOOL_UNREGISTER      # Tool unregistered
EventType.MCP_TOOL_CALL_PRE        # MCP tool execution started
EventType.MCP_TOOL_CALL_POST       # MCP tool execution completed
```

**Data fields (CONNECT/CONNECTED):**
* `server_name` (str) - MCP server name
* `config` (dict) - Server configuration

**Data fields (TOOL_CALL):**
* `server_name` (str) - Source server
* `tool_name` (str) - Tool name
* `arguments` (dict) - Tool arguments

**Use cases:** MCP monitoring, custom tool handling

### Permission Events

```python
EventType.PERMISSION_CHECK        # Permission being checked
EventType.PERMISSION_GRANTED      # Permission granted
EventType.PERMISSION_DENIED       # Permission denied
EventType.PERMISSION_CONFIRMATION # Confirmation requested
```

**Data fields:**
* `tool_name` (str) - Tool requiring permission
* `risk_level` (str) - Assessed risk level
* `decision` (str) - Grant/deny decision
* `reason` (str) - Reason for decision

**Use cases:** Custom permission logic, audit logging

### System Events

```python
EventType.SYSTEM_STARTUP          # Application starting
EventType.SYSTEM_READY            # Application ready for use
EventType.SYSTEM_SHUTDOWN         # Application shutting down
EventType.RENDER_FRAME            # Each render frame
```

**Data fields (RENDER_FRAME):**
* `delta_time` (float) - Time since last frame
* `frame_count` (int) - Total frames rendered

**Use cases:** Startup tasks, cleanup, custom animations

### Input Rendering Events

```python
EventType.INPUT_RENDER_PRE        # Before input line render
EventType.INPUT_RENDER            # During input render (main)
EventType.INPUT_RENDER_POST       # After input line render
```

**Data fields:**
* `input_text` (str) - Current input text
* `cursor_pos` (int) - Cursor position
* `render_width` (int) - Available width

**Use cases:** Custom input styling, validation display

### Command Menu Events

```python
EventType.COMMAND_MENU_SHOW       # Menu opened
EventType.COMMAND_MENU_NAVIGATE   # User navigating menu
EventType.COMMAND_MENU_SELECT     # User selected item
EventType.COMMAND_MENU_HIDE       # Menu closed
EventType.COMMAND_MENU_RENDER     # Menu rendering
EventType.COMMAND_MENU_FILTER     # Menu filtering
EventType.COMMAND_MENU_ACTIVATE   # Menu activation
```

**Data fields:**
* `items` (list) - Menu items
* `selected_index` (int) - Current selection
* `filter_text` (str) - Current filter

**Use cases:** Custom menu items, filtering logic

### Status Display Events

```python
EventType.STATUS_VIEW_CHANGED     # Status view mode changed
EventType.STATUS_CONTENT_UPDATE   # Status content update
EventType.STATUS_BLOCK_RESIZE     # Status block resized
```

**Data fields:**
* `area` (str) - "A", "B", or "C"
* `width` (int) - Area width
* `content` (list) - Status lines

**Use cases:** Custom status widgets, dynamic updates

### Slash Command Events

```python
EventType.SLASH_COMMAND_DETECTED  # Slash command entered
EventType.SLASH_COMMAND_EXECUTE   # Command executing
EventType.SLASH_COMMAND_COMPLETE  # Command completed
EventType.SLASH_COMMAND_ERROR     # Command error
```

**Data fields:**
* `command` (str) - Command name
* `args` (list) - Command arguments
* `raw_input` (str) - Full input string

**Use cases:** Command interception, custom command handling

### Message Injection Events

```python
EventType.ADD_MESSAGE             # Add message to conversation
EventType.PRE_MESSAGE_INJECT      # Before message injection
EventType.POST_MESSAGE_INJECT     # After message injection
```

**Data fields:**
* `role` (str) - Message role ("user", "assistant", "system")
* `content` (str) - Message content
* `index` (int) - Injection position

**Use cases:** Custom message types, context injection

### LLM Continuation Control

```python
EventType.TRIGGER_LLM_CONTINUE    # Trigger more LLM output
```

**Data fields:**
* `reason` (str) - Why continuation triggered
* `state` (dict) - Current conversation state

**Use cases:** Custom continuation triggers

### Context Service Events

```python
EventType.CONTEXT_SERVICE_READY   # Context service initialized
```

**Data fields:**
* `service` (ContextService) - Service instance

**Use cases:** Register custom context loaders

### Modal Events

```python
EventType.MODAL_TRIGGER           # Modal triggered
EventType.STATUS_MODAL_TRIGGER    # Status modal triggered
EventType.STATUS_MODAL_RENDER     # Status modal render
EventType.LIVE_MODAL_TRIGGER      # Live modal triggered
EventType.MODAL_COMMAND_SELECTED  # Modal command selected
EventType.MODAL_SHOW              # Modal shown
EventType.MODAL_HIDE              # Modal hidden
EventType.MODAL_SAVE              # Modal state saved
```

**Data fields:**
* `modal_type` (str) - Modal type
* `content` (any) - Modal content

**Use cases:** Custom modals, modal state management

### Rendering Control Events

```python
EventType.PAUSE_RENDERING         # Pause render loop
EventType.RESUME_RENDERING        # Resume render loop
EventType.FULLSCREEN_INPUT        # Fullscreen input active
```

**Use cases:** Custom rendering modes, performance control

### Status Takeover Events

```python
EventType.STATUS_TAKEOVER_START   # Status area takeover starts
EventType.STATUS_TAKEOVER_NAVIGATE # Navigate takeover
EventType.STATUS_TAKEOVER_ACTION   # Action in takeover
EventType.STATUS_TAKEOVER_END     # Takeover ends
```

**Data fields:**
* `area` (str) - "A", "B", or "C"
* `ui` (object) - UI component

**Use cases:** Custom status area interfaces

### Help System Events

```python
EventType.SHOW_HELP_OVERLAY       # Show help overlay
EventType.SHOW_FIRST_RUN_HELP     # Show first-run help
```

**Use cases:** Custom help content, onboarding flows

### Shell Command Events

```python
EventType.SHELL_COMMAND_PRE       # Before shell command
EventType.SHELL_COMMAND_POST      # After shell command
EventType.SHELL_COMMAND_ERROR     # Shell command error
EventType.SHELL_COMMAND_CANCEL    # Shell command cancelled
```

**Data fields:**
* `command` (str) - Shell command
* `cwd` (str) - Working directory
* `result` (str) - Command output (POST)
* `exit_code` (int) - Exit code (POST)

**Use cases:** Shell command logging, security checks

### Widget Interaction Events

```python
EventType.WIDGET_SELECTED         # Widget selected
EventType.WIDGET_ACTIVATED        # Widget activated
EventType.WIDGET_ACTION_EXECUTED  # Widget action executed
EventType.WIDGET_DEACTIVATED      # Widget deactivated
EventType.WIDGET_COMMAND_EXECUTE  # Widget triggered command
```

**Data fields:**
* `widget_id` (str) - Widget identifier
* `widget_type` (str) - Widget type
* `action` (str) - Action performed
* `value` (any) - Action value

**Use cases:** Widget behavior customization

### Command Output Display

```python
EventType.COMMAND_OUTPUT_DISPLAY  # Command output displayed
```

**Data fields:**
* `output` (str) - Output content
* `command` (str) - Source command

**Use cases:** Output formatting, capture

## Hook Registration

Register hooks in your `register_hooks()` method:

```python
async def register_hooks(self):
    # Single hook
    await self.event_bus.register_hook(
        Hook(
            name="log_requests",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_PRE,
            priority=HookPriority.PREPROCESSING.value,
            callback=self._log_request
        )
    )

    # Multiple hooks can be registered
    await self.event_bus.register_hook(
        Hook(
            name="log_responses",
            plugin_name=self.name,
            event_type=EventType.LLM_RESPONSE_POST,
            priority=100,
            callback=self._log_response
        )
    )
```

## Callback Signature

All hook callbacks receive `(data, event)`:

```python
async def my_hook_callback(self, data, event):
    """
    Args:
        data: Event-specific data dictionary
        event: Event object with type, source, timestamp
    """
    # Process data
    modified_data = process(data)

    # Return data to continue flow
    # Return None to cancel (for cancellable events)
    return modified_data
```

## Context Objects

The `event` parameter contains:

```python
event.type         # EventType or str - Event type
event.data         # dict - Event data (same as first param)
event.source       # str - What triggered the event
event.timestamp    # datetime - When event was created
event.processed    # bool - Whether event was processed
event.cancelled    # bool - Whether event was cancelled
event.result       # dict - Results from hooks
```

## Execution Flow

Events with `PRE`/`POST` suffixes execute in phases:

```
USER_INPUT_PRE → USER_INPUT → USER_INPUT_POST
```

1. PRE phase - All PRE hooks execute by priority
2. MAIN phase - Main event hooks execute
3. POST phase - All POST hooks execute by priority

Each hook receives the modified data from previous hooks.

## Unregistering Hooks

```python
await self.event_bus.unregister_hook(
    plugin_name=self.name,
    hook_name="my_hook"
)
```

## Enabling/Disabling Hooks

```python
# Disable (don't execute, but keep registered)
self.event_bus.disable_hook(self.name, "my_hook")

# Re-enable
self.event_bus.enable_hook(self.name, "my_hook")
```

## Custom Event Types

Plugins can define custom event types:

```python
# Emit custom event
await self.event_bus.emit_with_hooks(
    "my_plugin:custom_event",
    {"custom": "data"},
    self.name
)

# Listen for custom event
await self.event_bus.register_hook(
    Hook(
        name="handle_custom",
        plugin_name=self.name,
        event_type="my_plugin:custom_event",  # String, not EventType
        priority=100,
        callback=self._handle_custom
    )
)
```

## Event Phase Mapping

Add custom pre/post mappings:

```python
self.event_bus.add_event_type_mapping(
    main_event=EventType.MY_EVENT,
    pre_event=EventType.MY_EVENT_PRE,
    post_event=EventType.MY_EVENT_POST
)
```

## Hook Status Monitoring

Get status of all hooks:

```python
status = self.event_bus.get_hook_status()
# Returns: {"total_hooks": N, "status_counts": {...}, "hook_details": {...}}

stats = self.event_bus.get_registry_stats()
# Returns: comprehensive registry statistics
```

## Best Practices

1. Return data unchanged if not modifying
2. Use appropriate priority (SECURITY for checks, LLM for LLM ops)
3. Keep hooks fast - they're in hot paths
4. Handle errors gracefully - don't crash other hooks
5. Use async properly - await async operations
6. Log at INFO level or higher - DEBUG can be noisy
7. Cancel events with None return only when intentional

## Example: Multiple Hooks

```python
class MultiHookPlugin(BasePlugin):
    async def register_hooks(self):
        # Security - high priority
        await self.event_bus.register_hook(
            Hook(
                name="security_check",
                plugin_name=self.name,
                event_type=EventType.TOOL_CALL_PRE,
                priority=HookPriority.SECURITY.value,
                callback=self._security_check
            )
        )

        # Logging - normal priority
        await self.event_bus.register_hook(
            Hook(
                name="log_tool",
                plugin_name=self.name,
                event_type=EventType.TOOL_CALL_POST,
                priority=100,
                callback=self._log_tool
            )
        )

        # Display - low priority
        await self.event_bus.register_hook(
            Hook(
                name="format_result",
                plugin_name=self.name,
                event_type=EventType.TOOL_CALL_POST,
                priority=HookPriority.DISPLAY.value,
                callback=self._format_result
            )
        )

    async def _security_check(self, data, event):
        # Runs first
        if self._is_dangerous(data.get("tool_name")):
            return None  # Cancel the tool call
        return data

    async def _log_tool(self, data, event):
        # Runs in middle
        logger.info(f"Tool: {data.get('tool_name')}")
        return data

    async def _format_result(self, data, event):
        # Runs last
        return data
```
