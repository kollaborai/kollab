"""Context triggering service for auto-loading relevant context."""

import logging
import re
from pathlib import Path
from typing import Optional

from kollabor_config.config_utils import resolve_global_path

logger = logging.getLogger(__name__)

# Built-in context: Agent Orchestration Instructions
AGENT_ORCHESTRATION_CONTEXT = """
## Agent Orchestration

You can spawn parallel sub-agents to work on tasks concurrently. Each agent runs as a separate subprocess.

### Spawn Agents

```xml
<agent>
  <agent-name>
    <agent-type>coder</agent-type>      <!-- Optional: use specific agent type -->
    <skill>debugging</skill>             <!-- Optional: load specific skill(s) -->
    <skill>tdd</skill>
    <task>
    objective: What to accomplish

    context:
    - Relevant constraints
    - Background info

    todo:
    [ ] Step 1
    [ ] Step 2

    success: How to verify completion
    </task>
    <files>
      <file>path/to/file.py</file>
    </files>
  </agent-name>
</agent>
```

### Agent Types and Skills

Use `<agent-type>` to specify which agent persona to use (e.g., "coder", "research").
Use `<skill>` tags to load specific skills into the agent's context.

The available agents and skills are shown in the system prompt via `<trender type="agents_list" />`.

### Other Commands

```xml
<message to="agent-name">Send instruction to running agent</message>
<capture>agent-name 200</capture>
<stop>agent-name</stop>
<status></status>
```

### Command Behavior

These commands work like tool calls:
1. You output the XML command
2. System executes and shows TagBox display: `run_subagent(name)` with spinner
3. Immediate result is injected: `[spawned: name1, name2]`
4. You SHOULD acknowledge to the user what you spawned and why
5. Agent runs in background, final result injected when complete (6+ seconds idle)

Example flow:
- You: `<agent><bug-finder><agent-type>coder</agent-type><skill>debugging</skill><task>...</task></bug-finder></agent>`
- System: TagBox display `run_subagent(bug-finder)` with running spinner
- System: `[spawned: bug-finder]`
- You: "I've spawned a bug-finder agent (using the coder agent with debugging skill) to investigate the codebase."
- [Later, when agent completes]
- System: `[done: bug-finder @ 2m34s]\\n{output}`
- You: [Respond to the agent's findings]

Files in `<files>` are auto-attached to agent context.
Agents complete when idle for 6 seconds (MD5 hash unchanged).
""".strip()


class ContextService:
    """Context triggering service for auto-loading relevant context.

    Extensible architecture:
    - Plugins can register triggers via register_trigger()
    - Plugins can register context loaders via register_loader()
    - Config file defines default triggers and paths
    """

    def __init__(self, config, conversation_manager, event_bus):
        self.config = config
        self.conversation_manager = conversation_manager
        self.event_bus = event_bus
        self.active_contexts = {}
        self.last_triggers = []

        # Extensible trigger and loader registries
        self.trigger_map = {}
        self.context_loaders = {}

        # Register built-in loaders
        self._register_builtin_loaders()

        # Load default triggers from config
        self._load_configured_triggers()

        # Emit event so plugins can register triggers/loaders
        # (Event-based extensibility)
        from kollabor_events import EventType

        async def _emit_ready():
            await event_bus.emit_with_hooks(
                EventType.CONTEXT_SERVICE_READY,
                {
                    "service": self,
                    "message": "ContextService ready for plugin registrations",
                },
                "context_service",
            )

        # Schedule event emission (don't block init)
        if hasattr(event_bus, "loop") and event_bus.loop:
            event_bus.loop.create_task(_emit_ready())
        else:
            # No event loop available - skip event emission
            # The service is still functional, plugins just won't get the ready event
            logger.debug(
                "No event loop available, skipping CONTEXT_SERVICE_READY event"
            )

    def register_trigger(self, keyword: str, context_id: str):
        """Register a trigger keyword for auto-loading context.

        Plugins can call this to add their own triggers.

        Args:
            keyword: Word/phrase that triggers this context
            context_id: Unique ID for the context (used by loaders)
        """
        keyword = keyword.lower().strip()
        self.trigger_map[keyword] = context_id
        logger.info(f"Registered trigger: '{keyword}' -> {context_id}")

    def register_loader(self, context_type: str, loader_func):
        """Register a context loader function.

        Plugins can register custom loaders for different context types.

        Args:
            context_type: Context ID prefix (e.g., "book", "project", "file")
            loader_func: Async function(context_id) -> str (content)
        """
        if context_type not in self.context_loaders:
            self.context_loaders[context_type] = loader_func
            logger.info(f"Registered context loader for type: {context_type}")
        else:
            logger.warning(f"Context loader for '{context_type}' already registered")

    def _register_builtin_loaders(self):
        """Register built-in context loaders."""
        # Bookmarks-based loader (default)
        self.register_loader("bookmarks", self._load_from_bookmarks)

        # Built-in in-memory contexts
        self.register_loader("builtin", self._load_builtin_context)

    async def _load_builtin_context(self, context_id: str) -> Optional[str]:
        """Load built-in context content.

        Built-in contexts are embedded in the app and don't require external files.

        Args:
            context_id: Context ID (e.g., "builtin:agent-orchestration")

        Returns:
            Context content string or None if not found
        """
        # Extract the specific context from context_id
        if ":" in context_id:
            context_name = context_id.split(":", 1)[1]
        else:
            context_name = context_id

        # Built-in contexts registry
        built_in_contexts = {
            "agent-orchestration": AGENT_ORCHESTRATION_CONTEXT,
            "agent_orchestration": AGENT_ORCHESTRATION_CONTEXT,
            "sub-agent": AGENT_ORCHESTRATION_CONTEXT,
            "subagent": AGENT_ORCHESTRATION_CONTEXT,
        }

        return built_in_contexts.get(context_name)

    def _load_configured_triggers(self):
        """Load triggers from configuration file."""
        # Get base bookmarks path from config
        self.bookmarks_path = Path(
            self.config.get(
                "context.bookmarks_path", resolve_global_path("BOOKMARKS.md")
            )
        )

        # Get triggers from config
        config_triggers = self.config.get("context_triggers", {})

        # Default triggers (can be overridden by config)
        default_triggers = {
            "test": "test:the-test",
        }

        # Merge: config overrides defaults
        merged_triggers = {**default_triggers, **config_triggers}

        # Register all triggers
        for keyword, context_id in merged_triggers.items():
            self.register_trigger(keyword, context_id)

    async def _load_from_bookmarks(self, context_id: str) -> Optional[str]:
        """Load context from BOOKMARKS.md file.

        Built-in loader that extracts sections from bookmarks file.

        Args:
            context_id: Context ID to load (e.g., "book:the-roots")

        Returns:
            Context content string or None
        """
        if not self.bookmarks_path.exists():
            logger.warning(f"Bookmarks file not found: {self.bookmarks_path}")
            return None

        try:
            content = self.bookmarks_path.read_text()
            # Extract the context_id part (e.g., "the-roots" from "book:the-roots")
            if ":" in context_id:
                section_name = context_id.split(":", 1)[1]
                # Replace hyphens with spaces for matching
                section_pattern = section_name.replace("-", " ")
            else:
                section_pattern = context_id

            pattern = rf"###[\s]+.*{section_pattern}"
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                start = match.start()
                next_section = re.search(r"\n#{1,3}\s+", content[start + 1 :])
                if next_section:
                    end = start + 1 + next_section.start()
                    extracted = content[start:end]
                else:
                    extracted = content[start:]
                return f"## Context Loaded: {context_id}\n\n{extracted}\n"
            else:
                logger.debug(f"Section not found in bookmarks: {context_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to load from bookmarks: {e}")
            return None

    # Removed _load_user_triggers - now handled by _load_configured_triggers()

    def scan_for_triggers(self, user_input):
        triggered = []
        input_lower = user_input.lower()
        for keyword, context_id in self.trigger_map.items():
            if keyword in input_lower:
                if context_id not in triggered:
                    triggered.append(context_id)
                    logger.debug(f"Context trigger: '{keyword}' -> {context_id}")
        return triggered

    async def trigger_context_injection(self, user_input):
        triggered_contexts = self.scan_for_triggers(user_input)
        if not triggered_contexts:
            return False
        new_contexts = [c for c in triggered_contexts if c not in self.active_contexts]
        if not new_contexts:
            return False
        injected = False
        for context_id in new_contexts:
            content = await self.load_context(context_id)
            if content:
                formatted = f'<context_inject type="{context_id}">\n{content}\n</context_inject>'
                try:
                    self.conversation_manager.add_message(
                        role="system", content=formatted
                    )
                    self.active_contexts[context_id] = content
                    self.last_triggers.append(context_id)
                    injected = True
                    logger.info(f"Injected context: {context_id}")
                except Exception as e:
                    logger.error(f"Failed to inject context {context_id}: {e}")
        return injected

    async def load_context(self, context_id: str) -> Optional[str]:
        """Load context content using registered loaders.

        Tries loaders in order:
        1. Type-specific loader (if context_id has "type:value" format)
        2. Default bookmarks loader

        Args:
            context_id: Context ID (e.g., "book:the-roots", "project:kollab")

        Returns:
            Context content string or None if not found
        """
        # Try type-specific loader first
        if ":" in context_id:
            context_type = context_id.split(":", 1)[0]

            # Check if plugin registered loader for this type
            if context_type in self.context_loaders:
                logger.debug(f"Using loader for type: {context_type}")
                result = await self.context_loaders[context_type](context_id)
                return str(result) if result is not None else None

        # Fallback: try bookmarks loader (for backward compatibility)
        if "bookmarks" in self.context_loaders:
            result = await self.context_loaders["bookmarks"](context_id)
            return str(result) if result is not None else None

        # No loader found - return generic message
        logger.warning(f"No loader found for context: {context_id}")
        return f"## Context: {context_id}\n\nContext loaded based on trigger."

    async def _extract_from_bookmarks(self, bookmarks_path, context_id):
        try:
            content = bookmarks_path.read_text()
            pattern = rf"###[\s]+.*{context_id.replace('-', ' ')}"
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                start = match.start()
                next_section = re.search(r"\n#{1,3}\s+", content[start + 1 :])
                if next_section:
                    end = start + 1 + next_section.start()
                    extracted = content[start:end]
                else:
                    extracted = content[start:]
                return f"## Context Loaded: {context_id}\n\n{extracted}\n"
        except Exception as e:
            logger.error(f"Failed to extract from bookmarks: {e}")
        return None

    def clear_active_contexts(self):
        self.active_contexts.clear()
        self.last_triggers.clear()

    def get_active_contexts(self):
        return list(self.active_contexts.keys())
