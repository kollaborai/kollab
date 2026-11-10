# Plugins, hooks and core architecture

Part of the agent guide. `CLAUDE.md` holds the rules for every change and links here;
this page holds the reference for one area. Read it before you change that area.

## Plugin Architecture
- Plugin discovery from `plugins/` directory
- Dynamic instantiation with dependency injection
- Hook registration for event interception
- Configuration merging from plugin configs

## Core Architecture Patterns

### Event-Driven Design
Event bus (`kollabor_events.bus`) coordinates:
- **HookRegistry** (`kollabor_events.registry`) - Hook registration and lookup
- **HookExecutor** (`kollabor_events.executor`) - Hook execution with error handling
- **EventProcessor** (`kollabor_events.processor`) - Sequential event processing
- **EventBus** (`kollabor_events.bus`) - Central coordinator

### Plugin Lifecycle
1. **Discovery** - `PluginDiscovery` scans `plugins/`
2. **Registry** - `PluginRegistry` maintains metadata
3. **Factory** - `PluginFactory` instantiates with dependency injection
4. **Initialization** - Plugins call `initialize()` and `register_hooks()`
5. **Execution** - Events trigger registered hooks with priority ordering
6. **Cleanup** - Plugins call `shutdown()`

### LLM Coordinator Architecture
`LLMCoordinator` (`kollabor/llm/llm_coordinator.py`) orchestrates:
- **APICommunicationService** (`kollabor_ai`) - HTTP client with rate limiting
- **KollaborConversationLogger** (`kollabor_ai`) - Persistent conversation history
- **MessageDisplayService** (`kollabor_tui`) - Response formatting and streaming
- **StreamingHandler** (`kollabor/llm/`) - Stream processing
- **MessageHandler** (`kollabor/llm/`) - Message flow
- **LLMHookSystem** (`kollabor/llm/`) - Request/response interception

### Plugin System Details
**Discovery locations (in order):**
1. `<package_root>/plugins/` (pip install)
2. `./plugins/` (development mode)

**Each plugin can:**
- Register hooks at any priority (CRITICAL, HIGH, NORMAL, LOW)
- Access shared services via dependency injection
- Contribute status line items to areas A, B, or C
- Merge custom configuration into global config
- Register slash commands via CommandRegistry

## Hook System

Plugins can:
- Intercept user input (`pre_user_input`)
- Transform LLM requests (`pre_api_request`)
- Process responses (`post_api_response`)
- Add custom status indicators (`get_status_line()`)
- Create new terminal UI elements

### Config Hooks (JSON, no Python needed)

Claude Code-compatible JSON hooks. Create `.kollab/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "file_create|file_edit",
        "hooks": [{ "type": "command", "command": "python guard.py" }]
      }
    ]
  }
}
```

Commands receive event data on stdin (JSON), respond via exit code + stdout JSON.
Exit 0 = proceed, exit 2 = block. Supports Claude Code event names (`PreToolUse`,
`UserPromptSubmit`, etc.) plus Kollabor-native names (`LlmRequestPre`, `McpToolCallPre`, etc.).

**Key file:** `kollabor/config_hooks.py` (ConfigHookLoader)

See `docs/features/config-hooks.md` for full reference.

## Plugin Development

Plugins should:
1. Inherit from base classes in `kollabor_plugins`
2. Register hooks in `register_hooks()` using `EventType` enum
3. Provide status info via `get_status_line()`
4. Implement `initialize()` and `shutdown()`
5. Use `async def` for all hook handlers
6. Optionally implement `get_config_widgets()` to expose settings in the `/config` modal

### Plugin Config Widgets

Plugins can expose configurable settings in the `/config` modal by implementing a static `get_config_widgets()` method. Widgets are auto-discovered from the `plugins/` directory (no manual registration needed).

```python
@staticmethod
def get_config_widgets() -> Dict[str, Any]:
    return {
        "title": "My Plugin",
        "widgets": [
            {"type": "checkbox", "label": "Enabled", "config_path": "plugins.my_plugin.enabled", "help": "Enable plugin"},
            {"type": "slider", "label": "Timeout", "config_path": "plugins.my_plugin.timeout", "min_value": 1, "max_value": 60, "step": 1, "help": "Timeout in seconds"},
        ],
    }
```

Supported widget types: `checkbox`, `slider`, `dropdown`, `text_input`, `spinbox`. Each widget requires `type`, `label`, `config_path`, and `help`. Additional keys depend on the widget type (e.g., `min_value`/`max_value`/`step` for slider, `options` for dropdown, plus an optional `option_labels` mapping each option to the text people read when the options are ids; the option is still what is saved).

**Important:** every `config_path` in a widget must have a matching entry in `get_default_config()`. If the default is missing, the widget will display an empty or incorrect value regardless of what the user sets. This is the single most common bug in config widgets — always verify the `config_path` key appears in both `get_config_widgets()` and `get_default_config()`.
