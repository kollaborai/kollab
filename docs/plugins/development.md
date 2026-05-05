---
title: "Plugin Development Guide"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Plugin Development Guide

Step-by-step guide to creating Kollab plugins.

## Step 1: Create Plugin File

Create a file in `plugins/` ending with `_plugin.py`:

```bash
touch plugins/my_awesome_plugin.py
```

Minimum viable plugin:

```python
from kollabor_plugins import BasePlugin

class MyAwesomePlugin(BasePlugin):
    """My awesome plugin description."""

    @staticmethod
    def get_default_config():
        return {"plugins": {"my_awesome": {"enabled": True}}}
```

## Step 2: Implement the Constructor

Your plugin receives injected dependencies:

```python
class MyAwesomePlugin(BasePlugin):
    def __init__(self, name: str, event_bus, renderer, config):
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config_manager = config

        # Custom state
        self.call_count = 0
```

The `name` parameter is auto-generated from your class name:
* `MyAwesomePlugin` → `my_awesome`
* `SomeFeaturePlugin` → `some_feature`

## Step 3: Implement initialize()

Async method called during startup. Access additional services:

```python
async def initialize(self, args=None, **kwargs):
    # Core services
    self.config = kwargs.get("config")
    self.event_bus = kwargs.get("event_bus")
    self.command_registry = kwargs.get("command_registry")
    self.renderer = kwargs.get("renderer")

    # Optional services (check if available)
    self.llm_service = kwargs.get("llm_service")
    self.conversation_logger = kwargs.get("conversation_logger")
    self.input_handler = kwargs.get("input_handler")

    # Register slash commands
    if self.command_registry:
        self._register_commands()

    # Register status widgets
    if self.renderer:
        self.renderer.register_status_widget(self.get_status_content, "B")
```

## Step 4: Register Event Hooks

Use `register_hooks()` to attach to events:

```python
from kollabor_events import EventType, Hook, HookPriority

async def register_hooks(self):
    # User input interception
    await self.event_bus.register_hook(
        Hook(
            name="preprocess_input",
            plugin_name=self.name,
            event_type=EventType.USER_INPUT_PRE,
            priority=HookPriority.PREPROCESSING.value,
            callback=self._preprocess_input
        )
    )

    # LLM request modification
    await self.event_bus.register_hook(
        Hook(
            name="modify_request",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST,
            priority=HookPriority.LLM.value,
            callback=self._modify_request
        )
    )
```

## Step 5: Implement Hook Callbacks

Hook callbacks receive `(data, event)`:

```python
async def _preprocess_input(self, data, event):
    """Modify user input before processing."""
    user_text = data.get("text", "")

    # Example: expand custom aliases
    if user_text.startswith("/alias "):
        expanded = user_text.replace("/alias ", "/custom_command ")
        data["text"] = expanded

    # Always return data to continue the flow
    return data
```

## Slash Commands

Register custom slash commands with subcommands:

```python
from kollabor_events.models import (
    CommandDefinition, CommandCategory, CommandMode, SubcommandInfo
)

def _register_commands(self):
    cmd = CommandDefinition(
        name="awesome",
        description="My awesome command",
        handler=self._handle_awesome,
        plugin_name=self.name,
        aliases=["awe", "aw"],
        mode=CommandMode.INLINE_INPUT,
        category=CommandCategory.CUSTOM,
        subcommands=[
            SubcommandInfo("status", "", "Show plugin status"),
            SubcommandInfo("reset", "", "Reset plugin state"),
        ]
    )
    self.command_registry.register_command(cmd)

async def _handle_awesome(self, command):
    args = command.args if hasattr(command, "args") else []

    if not args or args[0] == "status":
        return f"AwesomePlugin: {self.call_count} calls"

    if args[0] == "reset":
        self.call_count = 0
        return "AwesomePlugin: reset"

    return f"Unknown subcommand: {args[0]}"
```

## Status Widgets

Contribute to the three status areas (A, B, C):

```python
from kollabor_tui.visual_effects import AgnosterSegment

def get_status_content(self):
    """Return list of strings for status display."""
    seg = AgnosterSegment()
    seg.add_lime("Awesome", "dark")
    seg.add_cyan(str(self.call_count), "mid")
    return [seg.render()]

def get_status_line(self):
    """Alternative: return dict with area-specific content."""
    return {
        "A": [],  # Left area - usually plugins
        "B": [f"Awesome: {self.call_count}"],  # Middle area
        "C": []   # Right area - usually model/status
    }
```

## Configuration Merging

Define defaults that merge into `~/.kollab/config.json`:

```python
@staticmethod
def get_default_config():
    return {
        "plugins": {
            "my_awesome": {
                "enabled": True,
                "log_level": "INFO",
                "max_calls": 100,
                "feature_flags": {
                    "experimental": False
                }
            }
        }
    }
```

Access config at runtime:

```python
# In any method
enabled = self.config_manager.get("plugins.my_awesome.enabled", True)
max_calls = self.config_manager.get("plugins.my_awesome.max_calls", 100)
```

## Context Service Integration

Register custom context loaders:

```python
async def register_hooks(self):
    # Wait for context service to be ready
    await self.event_bus.register_hook(
        Hook(
            name="register_context",
            plugin_name=self.name,
            event_type=EventType.CONTEXT_SERVICE_READY,
            priority=100,
            callback=self._on_context_ready
        )
    )

async def _on_context_ready(self, data, event):
    context_service = data.get("service")
    if context_service:
        # Register trigger keywords
        context_service.register_trigger("awesome", "plugin:awesome")

        # Register loader function
        context_service.register_loader(
            "awesome",
            self._load_awesome_context
        )

async def _load_awesome_context(self, context_id):
    return """
## Awesome Plugin Context

This context is loaded when user mentions 'awesome'.
"""
```

## Message Injection

Inject messages into conversations:

```python
async def register_hooks(self):
    await self.event_bus.register_hook(
        Hook(
            name="inject_note",
            plugin_name=self.name,
            event_type=EventType.POST_MESSAGE_INJECT,
            priority=100,
            callback=self._inject_note
        )
    )

async def _inject_note(self, data, event):
    # Inject a note after LLM response
    if data.get("role") == "assistant":
        return {
            "role": "system",
            "content": f"[AwesomePlugin: Logged {self.call_count} interactions]"
        }
    return None
```

## Registering Plugin Tools (XML Tag Pipeline)

Plugins can register custom XML tags that the LLM emits as structured
tools. The unified tool pipeline handles parsing, stripping, execution,
and display automatically -- no LLM_RESPONSE_POST hooks needed.

### Two-step registration

Step 1: Register your tag pattern with the response parser so it gets
extracted and stripped from display text.

Step 2: Register a handler with the tool executor so the extracted tool
gets routed to your code.

### API reference

```python
# In your initialize() method:
response_parser = kwargs.get("response_parser")
tool_executor = kwargs.get("tool_executor")

# Step 1: Register tag pattern
response_parser.register_plugin_tag(
    tag_name="my_tag",       # Human-readable name (for logging)
    pattern=re.compile(
        r"<my_tag>(.*?)</my_tag>",
        re.DOTALL,
    ),
    tool_type="my_tag",      # Must match handler registration below
    extract_fn=lambda m: {"content": m.group(1).strip()},
    # extract_fn receives the regex match, returns a dict of tool data
)

# Step 2: Register handler
tool_executor.register_plugin_handler(
    "my_tag",                # Must match tool_type above
    self._handle_my_tag,     # async def (tool_data) -> ToolExecutionResult
)
```

### Handler signature

```python
from kollabor_agent.tool_executor import ToolExecutionResult

async def _handle_my_tag(self, tool_data: dict) -> ToolExecutionResult:
    """Execute a plugin tool extracted from LLM response."""
    content = tool_data.get("content", "")

    # Your logic here
    result = do_something(content)

    return ToolExecutionResult(
        tool_id=tool_data.get("id", "unknown"),
        tool_type="my_tag",
        success=True,
        output=str(result),
    )
```

### Full example: notification plugin

```python
import re
import logging
from kollabor_plugins import BasePlugin
from kollabor_agent.tool_executor import ToolExecutionResult

logger = logging.getLogger(__name__)

class NotifyPlugin(BasePlugin):
    """Plugin that lets the LLM send notifications to the user."""

    async def initialize(self, args=None, **kwargs):
        response_parser = kwargs.get("response_parser")
        tool_executor = kwargs.get("tool_executor")

        if response_parser and tool_executor:
            # Register <notify priority="high">message</notify>
            notify_pat = re.compile(
                r'<notify\s+priority="(\w+)">(.*?)</notify>',
                re.DOTALL | re.IGNORECASE,
            )

            def _extract_notify(m):
                return {
                    "priority": m.group(1).lower(),
                    "message": m.group(2).strip(),
                }

            response_parser.register_plugin_tag(
                "notify", notify_pat, "notify", _extract_notify
            )
            tool_executor.register_plugin_handler(
                "notify", self._handle_notify
            )

    async def _handle_notify(self, tool_data: dict) -> ToolExecutionResult:
        priority = tool_data.get("priority", "normal")
        message = tool_data.get("message", "")

        # Actually show the notification
        logger.info(f"[NOTIFY:{priority}] {message}")

        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="notify",
            success=True,
            output=f"Notified user ({priority}): {message[:50]}",
        )
```

### How it works

The unified pipeline processes all LLM responses through a single path:

1. Native tool calls extracted from API response (if any)
2. response_parser.parse_response() ALWAYS runs -- extracts core tools
   (terminal, file ops, MCP) and plugin-registered tools
3. LLM_RESPONSE event fires (observation-only, no tag stripping needed)
4. Clean text displayed (all tags already stripped by parser)
5. Native tools execute (batch)
6. XML/plugin tools execute (incremental with question gate)
7. Tool results logged and added to conversation history

Your plugin tags are stripped from display text automatically. They appear
as executed tools in the UI, same as terminal commands and file operations.

### Multiple tags per response

The parser handles multiple tags of any type in a single response. Tags
execute in document order (sorted by their position in the response text).

### Mixing with native tool calls

Plugin XML tags work alongside native API function calling. If the LLM
returns native tool calls AND plugin XML tags, both execute. Native tools
run first (batch), then plugin/XML tools run incrementally.

### Tips

- Use re.DOTALL for tags that may contain multiline content
- extract_fn should return a flat dict -- it becomes tool_data in your handler
- Return ToolExecutionResult from your handler -- the pipeline handles display
- Register tags in initialize(), not __init__() -- services aren't available yet
- Tag patterns should be specific enough to avoid false positives
- Keep handler logic fast -- it blocks the tool execution pipeline

## Shutdown

Clean up async resources:

```python
async def shutdown(self):
    # Cancel background tasks
    if hasattr(self, '_monitor_task'):
        self._monitor_task.cancel()

    # Close connections
    if hasattr(self, '_client'):
        await self._client.close()

    # Save state
    self._save_state()
```

## Full Working Example

```python
# plugins/word_counter_plugin.py
"""
Word counter plugin - tracks word usage in conversations.
"""
import logging
from collections import Counter
from kollabor_plugins import BasePlugin
from kollabor_events import EventType, Hook, HookPriority, CommandDefinition
from kollabor_tui.visual_effects import AgnosterSegment

logger = logging.getLogger(__name__)

class WordCounterPlugin(BasePlugin):
    """Count and track word usage in conversations."""

    def __init__(self, name: str, event_bus, renderer, config):
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config_manager = config
        self.word_counts = Counter()
        self.message_count = 0

    async def initialize(self, args=None, **kwargs):
        self.config = kwargs.get("config")
        self.command_registry = kwargs.get("command_registry")

        if self.command_registry:
            self._register_commands()

    def _register_commands(self):
        cmd = CommandDefinition(
            name="words",
            description="Word count statistics",
            handler=self._handle_words,
            plugin_name=self.name,
            aliases=["wc", "stats"],
        )
        self.command_registry.register_command(cmd)

    async def register_hooks(self):
        # Count words in user input
        await self.event_bus.register_hook(
            Hook(
                name="count_words",
                plugin_name=self.name,
                event_type=EventType.USER_INPUT,
                priority=100,
                callback=self._count_words
            )
        )

    async def _count_words(self, data, event):
        text = data.get("text", "")
        words = text.lower().split()
        self.word_counts.update(words)
        self.message_count += 1
        return data

    async def _handle_words(self, command):
        total_words = sum(self.word_counts.values())
        top_words = self.word_counts.most_common(5)

        lines = [
            f"Word Counter (messages: {self.message_count})",
            f"Total words: {total_words}",
            "",
            "Top words:"
        ]
        for word, count in top_words:
            lines.append(f"  {word}: {count}")

        return "\n".join(lines)

    def get_status_content(self):
        seg = AgnosterSegment()
        seg.add_neutral(f"Words: {sum(self.word_counts.values())}", "mid")
        return [seg.render()]

    @staticmethod
    def get_default_config():
        return {
            "plugins": {
                "word_counter": {
                    "enabled": True,
                    "min_word_length": 3
                }
            }
        }

    async def shutdown(self):
        logger.info(f"WordCounter tracked {self.message_count} messages")
```

## Debugging Your Plugin

Enable logging in your config:

```json
{
  "logging": {
    "level": "DEBUG",
    "loggers": {
      "plugins.word_counter": "DEBUG"
    }
  }
}
```

Use the hook monitoring plugin to see events firing:

```bash
# Install and enable hook monitoring plugin
kollab
> /hooks status
```

## Testing Your Plugin

Create a test file:

```python
# tests/test_word_counter_plugin.py
import unittest
from plugins.word_counter_plugin import WordCounterPlugin

class TestWordCounterPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = WordCounterPlugin(
            name="word_counter",
            event_bus=None,
            renderer=None,
            config=None
        )

    def test_default_config(self):
        config = self.plugin.get_default_config()
        self.assertIn("plugins", config)
        self.assertIn("word_counter", config["plugins"])
        self.assertTrue(config["plugins"]["word_counter"]["enabled"])

    async def test_word_counting(self):
        # Mock event data
        data = {"text": "hello world hello"}
        result = await self.plugin._count_words(data, None)

        self.assertEqual(result["text"], "hello world hello")
        self.assertEqual(self.plugin.word_counts["hello"], 2)
        self.assertEqual(self.plugin.word_counts["world"], 1)
```

## Common Patterns

### Modify LLM Requests
```python
async def _add_custom_header(self, data, event):
    headers = data.get("headers", {})
    headers["X-Custom-Header"] = "value"
    data["headers"] = headers
    return data
```

### Log Tool Calls
```python
async def _log_tool_call(self, data, event):
    tool_name = data.get("tool_name")
    logger.info(f"Tool called: {tool_name}")
    return data
```

### Suppress Specific Events
```python
async def _filter_sensitive(self, data, event):
    if "secret" in data.get("text", ""):
        return None  # Cancels the event
    return data
```

### Trigger Background Tasks
```python
async def initialize(self, args=None, **kwargs):
    self._monitor_task = asyncio.create_task(self._monitor())

async def _monitor(self):
    while True:
        await asyncio.sleep(60)
        # Do periodic work
```

## Troubleshooting

* Plugin not loading? Check filename ends in `_plugin.py`
* Hooks not firing? Verify `register_hooks()` is async and awaits `register_hook()`
* Config not merging? Ensure `get_default_config()` is `@staticmethod`
* Can't access services? Check kwargs in `initialize()`, not `__init__()`
* Import errors? Verify imports use `kollabor_events`, `kollabor_plugins`
