"""Tool registry — holds all ToolDefinitions.

Singleton pattern. Initialized at startup by importing everything
from tool_definitions/ (which triggers each module's register_all()
call).
"""

import logging
from typing import Dict, List, Optional

from .tool_definition import ToolDefinition

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of all ToolDefinitions.

    Use get_global() for the shared instance.
    """

    _instance: Optional["ToolRegistry"] = None

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    @classmethod
    def get_global(cls) -> "ToolRegistry":
        """Get the shared global registry."""
        if cls._instance is None:
            cls._instance = cls()
            cls._load_definitions(cls._instance)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the global singleton. Useful for testing."""
        cls._instance = None

    @staticmethod
    def _load_definitions(registry: "ToolRegistry") -> None:
        """Import and register all tool_definitions/ modules.

        Modules auto-register on first import. On subsequent calls
        (after reset), we need to explicitly call register_all()
        since Python caches module imports.
        """
        from .tool_definitions import (
            context,
            file_ops,
            git,
            hub,
            scratchpad,
            task,
            terminal,
            wait,
        )

        # Always explicitly register — handles post-reset reinit
        file_ops.register_all()
        terminal.register_all()
        git.register_all()
        hub.register_all()
        context.register_all()
        scratchpad.register_all()
        task.register_all()
        wait.register_all()

    def register(self, tool_def: ToolDefinition, replace: bool = False) -> None:
        """Register a tool definition.

        Args:
            tool_def: The ToolDefinition to register.
            replace: If True, silently replace existing. If False,
                     raise ValueError on duplicate.

        Raises:
            ValueError: If a tool with the same name already exists
                        and replace is False.
        """
        if tool_def.name in self._tools and not replace:
            # Already registered — skip silently (idempotent for re-import)
            return
        self._tools[tool_def.name] = tool_def
        logger.debug(f"Registered tool: {tool_def.name} ({tool_def.category})")

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool by name, or None if not registered."""
        return self._tools.get(name)

    def get_by_native_name(self, native_name: str) -> Optional[ToolDefinition]:
        """Get a tool by its native (underscore) name."""
        for tool in self._tools.values():
            if tool.native_name == native_name:
                return tool
        return None

    def get_by_xml_tag(self, tag: str) -> Optional[ToolDefinition]:
        """Get a tool by its XML tag name."""
        for tool in self._tools.values():
            if tool.xml_tag_name == tag:
                return tool
        return None

    def list(self) -> List[ToolDefinition]:
        """List all registered tools, sorted by name."""
        return sorted(self._tools.values(), key=lambda t: t.name)

    def list_by_category(self, category: str) -> List[ToolDefinition]:
        """List tools in a category."""
        return [t for t in self.list() if t.category == category]

    def get_for_bundle(self, allowed_names: List[str]) -> List[ToolDefinition]:
        """Return tools available to an agent bundle.

        Args:
            allowed_names: Tool names from bundle's agent.json "tools" field.

        Returns:
            List of ToolDefinition instances. Unknown names logged as warnings.
        """
        result = []
        for name in allowed_names:
            tool = self.get(name)
            if tool is None:
                logger.warning(
                    f"Bundle requests unknown tool '{name}'. Skipping."
                )
                continue
            result.append(tool)
        return result

    def all_categories(self) -> List[str]:
        """List all categories in use."""
        return sorted({t.category for t in self._tools.values()})

    def names(self) -> List[str]:
        """List all registered tool names."""
        return sorted(self._tools.keys())


def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return ToolRegistry.get_global()
