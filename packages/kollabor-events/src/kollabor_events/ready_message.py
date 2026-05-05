"""Ready message collection system for startup stats."""

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReadyMessageItem:
    """A single ready message stat item.

    Attributes:
        category: Category name (e.g., "system prompt", "hooks", "plugins")
        count: Numeric count or None
        label: Description label (e.g., "modules loaded", "active")
        priority: Display priority (higher = shown first)
        source: Plugin or component name that contributed this
    """

    category: str
    count: Optional[int]
    label: str
    priority: int = 100
    source: str = "core"

    def format(self) -> str:
        """Format the ready message item for display.

        Returns:
            Formatted string like "26 system prompt modules loaded"
        """
        if self.count is not None:
            return f"{self.count} {self.category} {self.label}"
        else:
            return f"{self.category} {self.label}"


class ReadyMessageCollector:
    """Collects ready message contributions from plugins and core components."""

    def __init__(self):
        """Initialize the ready message collector."""
        self.items: List[ReadyMessageItem] = []
        logger.debug("ReadyMessageCollector initialized")

    def add(
        self,
        category: str,
        count: Optional[int],
        label: str,
        priority: int = 100,
        source: str = "core",
    ) -> None:
        """Add a ready message item.

        Args:
            category: Category name (e.g., "system prompt", "hooks")
            count: Numeric count or None for non-numeric items
            label: Description label (e.g., "modules loaded", "active")
            priority: Display priority (higher = shown first)
            source: Plugin or component name
        """
        item = ReadyMessageItem(
            category=category,
            count=count,
            label=label,
            priority=priority,
            source=source,
        )
        self.items.append(item)
        logger.debug(f"Added ready message: {item.format()} (from {source})")

    def get_formatted_messages(self) -> List[str]:
        """Get all ready messages formatted and sorted by priority.

        Returns:
            List of formatted message strings
        """
        # Sort by priority (highest first)
        sorted_items = sorted(self.items, key=lambda x: x.priority, reverse=True)
        return [item.format() for item in sorted_items]

    def format_for_display(self, max_items: int = 5) -> str:
        """Format ready messages for display with limit.

        Args:
            max_items: Maximum number of items to display

        Returns:
            Formatted string with all items, comma-separated
        """
        messages = self.get_formatted_messages()

        if not messages:
            return ""

        # Take top N items by priority
        display_items = messages[:max_items]

        # Join with commas
        result = ", ".join(display_items)

        # Add "and X more" if we truncated
        remaining = len(messages) - max_items
        if remaining > 0:
            result += f", and {remaining} more"

        return result

    def clear(self) -> None:
        """Clear all collected ready messages."""
        self.items.clear()
        logger.debug("Ready messages cleared")

    def get_count(self) -> int:
        """Get total number of ready message items.

        Returns:
            Number of items collected
        """
        return len(self.items)
