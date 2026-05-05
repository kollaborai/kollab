---
title: "Plugins Overview"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Plugins Overview

Kollab's plugin system enables extending functionality through event-driven
hooks, slash commands, status widgets, and config merging. Everything in
Kollabor has hooks - plugins can intercept and modify nearly any behavior.

## What Plugins Can Do

* intercept user input before it reaches the LLM (e.g., custom aliases,
  preprocessing)
* transform LLM requests/responses (e.g., add custom headers, filter content)
* execute code when tools are called (e.g., logging, security checks)
* add custom slash commands with subcommands
* contribute status line items to the terminal UI (areas A, B, C)
* inject messages into conversations
* register custom context loaders for the context service
* create fullscreen modal interfaces
* merge custom configuration into the global config

## Discovery

Plugins are discovered automatically from two locations (in order):

1. `./plugins/` - Development mode (local to project)
2. `<package_root>/plugins/` - After `pip install` (installed location)

Discovery scans for:
* Files ending in `*_plugin.py` (e.g., `my_plugin.py`, `feature_plugin.py`)
* Directories containing `__init__.py` (e.g., `plugins/myplugin/`)

All discovered plugins are loaded with security validation (path sanitization,
module verification, permission checks on Unix).

## Lifecycle

```
discovery → instantiation → initialize() → register_hooks()
                                                    ↓
                                            running (hooks fire)
                                                    ↓
                                              shutdown()
```

1. Discovery: Plugin files scanned, modules loaded
2. Instantiation: `PluginFactory` creates instances with dependency injection
3. Initialize: `await plugin.initialize()` called with services
4. Register Hooks: `await plugin.register_hooks()` registers event callbacks
5. Running: Hooks execute when their events fire
6. Shutdown: `await plugin.shutdown()` cleans up resources

## Base Class

All plugins inherit from `kollabor_plugins.BasePlugin`:

```python
from kollabor_plugins import BasePlugin
from kollabor_events import EventType, Hook, HookPriority

class MyPlugin(BasePlugin):
    async def initialize(self, args=None, **kwargs):
        # Access injected services
        self.event_bus = kwargs.get("event_bus")
        self.config = kwargs.get("config")
        self.command_registry = kwargs.get("command_registry")
        self.renderer = kwargs.get("renderer")
        self.llm_service = kwargs.get("llm_service")

    async def register_hooks(self):
        await self.event_bus.register_hook(
            Hook(
                name="log_requests",
                plugin_name="myplugin",
                event_type=EventType.LLM_REQUEST_PRE,
                priority=HookPriority.PREPROCESSING.value,
                callback=self._log_request
            )
        )

    async def _log_request(self, data, event):
        # Handle the event
        pass

    @staticmethod
    def get_default_config():
        return {"plugins": {"myplugin": {"enabled": True}}}

    async def shutdown(self):
        # Cleanup
        pass
```

## Dependency Injection

The `PluginFactory` injects these dependencies into `__init__`:

* `event_bus` - EventBus for hook registration and event emission
* `renderer` - TerminalRenderer for UI operations
* `config` - ConfigManager for accessing configuration

Additional services are passed via `**kwargs` in `initialize()`:
* `command_registry` - Register slash commands
* `input_handler` - Raw mode input handling
* `llm_service` - LLM orchestration and conversation history
* `conversation_logger` - Conversation persistence
* `conversation_manager` - Conversation state management

## Quick Example

```python
# plugins/echo_plugin.py
from kollabor_plugins import BasePlugin
from kollabor_events import EventType, Hook, HookPriority

class EchoPlugin(BasePlugin):
    async def register_hooks(self):
        await self.event_bus.register_hook(
            Hook(
                name="echo_input",
                plugin_name="echo",
                event_type=EventType.USER_INPUT,
                priority=100,
                callback=self._echo_input
            )
        )

    async def _echo_input(self, data, event):
        user_text = data.get("text", "")
        print(f"[ECHO] {user_text}")
        return data  # Always return data to continue flow
```

## Plugin Package Structure

```
kollabor_plugins/
├── __init__.py           # Public exports
├── base.py               # BasePlugin class
├── discovery.py          # File scanning and module loading
├── factory.py            # Instantiation with DI
├── registry.py           # Plugin metadata tracking
├── collector.py          # Plugin collection manager
└── plugin_sdk.py         # SDK for plugin creation
```

## Event Bus Integration

Plugins communicate through the event bus (`kollabor_events`):

```python
# Register a hook
await event_bus.register_hook(hook)

# Emit an event (usually called by core, not plugins)
result = await event_bus.emit_with_hooks(
    EventType.CUSTOM_EVENT,
    {"key": "value"},
    "my_plugin"
)

# Get registered services
llm = event_bus.get_service("llm_service")
```

## Further Reading

* `development.md` - Step-by-step plugin creation guide
* `hooks-reference.md` - Complete EventType and hook documentation
