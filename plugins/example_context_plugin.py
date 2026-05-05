"""Example plugin showing context service extensibility.

This plugin demonstrates how to:
1. Register custom trigger keywords
2. Register custom context loaders
3. Load context from different sources
"""

import logging

from kollabor_events.models import EventType
from kollabor_plugins import BasePlugin

logger = logging.getLogger(__name__)


class ExampleContextPlugin(BasePlugin):
    """Example plugin that extends the context service."""

    async def initialize(
        self,
        args,
        event_bus,
        config,
        command_registry,

        renderer,
        llm_service,
        conversation_logger,
        conversation_manager,
    ):
        """Initialize the plugin."""
        self.event_bus = event_bus
        self.llm_service = llm_service
        self.config = config

        logger.info("Example Context Plugin initializing")

        # Register hook for CONTEXT_SERVICE_READY event
        # This fires when ContextService is ready for plugin registrations
        await event_bus.register_hook(
            event_type=EventType.CONTEXT_SERVICE_READY,
            callback=self._on_context_service_ready,
            priority=100,  # Normal priority
        )

        logger.info("Example Context Plugin initialized")

    async def _on_context_service_ready(self, data: dict, event):
        """Called when ContextService is ready.

        This is where plugins can register their own triggers and loaders.
        """
        context_service = data.get("service")
        if not context_service:
            logger.warning("ContextService not found in event data")
            return

        logger.info("ContextService ready, registering plugin extensions")

        # 1. Register custom trigger keywords
        context_service.register_trigger(keyword="github", context_id="plugin:github")

        context_service.register_trigger(keyword="api", context_id="plugin:api-docs")

        # 2. Register custom context loader
        context_service.register_loader(
            context_type="plugin", loader_func=self._load_plugin_context
        )

        logger.info("Registered custom triggers and loaders")

    async def _load_plugin_context(self, context_id: str) -> str:
        """Custom loader for plugin-specific contexts.

        This demonstrates loading context from a different source
        (e.g., plugin data files, APIs, in-memory data).

        Args:
            context_id: Context ID (e.g., "plugin:github")

        Returns:
            Context content string
        """
        # Extract the specific context from context_id
        if ":" in context_id:
            context_name = context_id.split(":", 1)[1]
        else:
            context_name = context_id

        # Different contexts loaded from different sources
        contexts = {
            "github": """
## GitHub Context

- Primary repo: kollaborai/kollab
- Branch: main
- Stars: [varies - check actual count]
- License: MIT

## Common Commands

```bash
git checkout main
git pull origin main
git checkout -b feature/your-feature
git push origin feature/your-feature
```

## Pull Request Guidelines

- Write clear descriptions
- Link to related issues
- Run tests before submitting
""",
            "api-docs": """
## Kollab API Documentation

### Core Components

**LLMService** (`core/llm/llm_service.py`)
- Main LLM orchestration
- Conversation history management
- Tool execution coordination

**EventBus** (`core/events/bus.py`)
- Central event dispatcher
- Hook registration and execution
- Plugin communication hub

**ContextService** (`core/llm/context_service.py`)
- Auto-context loading based on triggers
- Extensible loader system
- Plugin API for custom triggers/loaders

### Plugin API

**Register triggers:**
```python
context_service.register_trigger(keyword, context_id)
```

**Register loaders:**
```python
context_service.register_loader(context_type, loader_func)
```

**Hook into ContextService:**
```python
await event_bus.register_hook(
    event_type=EventType.CONTEXT_SERVICE_READY,
    callback=on_context_service_ready
)
```
""",
        }

        return contexts.get(
            context_name, f"## Plugin Context: {context_name}\n\nNo content found."
        )

    async def register_hooks(self):
        """Register plugin hooks."""
        # Already registered in initialize()
        pass

    def get_status_line(self):
        """Status line contribution."""
        return "CtxPlugin: Active"

    async def shutdown(self):
        """Cleanup on shutdown."""
        logger.info("Example Context Plugin shutting down")
