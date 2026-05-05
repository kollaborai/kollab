"""Env Queue Plugin — registers the agent notification EnvQueue.

Thin plugin wrapper that instantiates EnvQueue and registers it on
the event bus as ``env_queue`` so producers across the codebase can
push via ``event_bus.get_service("env_queue")``.

See docs/architecture/rfcs/RFC-2026-04-11-agent-notification-system.md.
"""

import logging
from typing import Any, Optional

from kollabor_plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class EnvQueuePlugin(BasePlugin):
    """Plugin wrapper for the EnvQueue lifecycle."""

    def __init__(self, name: str, event_bus: Any, renderer: Any, config: Any) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self._queue = None

    async def initialize(self, args: Optional[Any] = None, **kwargs) -> None:
        """Bootstrap EnvQueue if not already registered."""
        existing = self.event_bus.get_service("env_queue")
        if existing is not None:
            logger.info("env_queue already registered, skipping bootstrap")
            self._queue = existing
            return

        from kollabor_ai.notifications import EnvQueue

        max_size = 50
        if self.config:
            try:
                max_size = int(
                    self.config.get("plugins.env_queue.max_size", max_size)
                )
            except Exception:
                pass

        self._queue = EnvQueue(max_size=max_size)
        self.event_bus.register_service("env_queue", self._queue)
        logger.info("EnvQueue bootstrapped (max_size=%d)", max_size)

        self._register_notification_tags()

    def _register_notification_tags(self) -> None:
        """Register <notifications/> and <notifications clear/> XML tags."""
        import re as _re

        response_parser = None
        tool_executor = None
        try:
            response_parser = self.event_bus.get_service("response_parser")
            tool_executor = self.event_bus.get_service("tool_executor")
        except Exception:
            return
        if response_parser is None or tool_executor is None:
            return
        if not hasattr(response_parser, "register_plugin_tag"):
            return
        if not hasattr(tool_executor, "register_plugin_handler"):
            return

        # Clear pattern must be checked before the query pattern since
        # /<notifications\s*/>/ also matches "<notifications clear/>".
        clear_pat = _re.compile(
            r"<notifications\s+clear\s*/>", _re.IGNORECASE
        )
        query_pat = _re.compile(r"<notifications\s*/>", _re.IGNORECASE)

        def _extract_clear(_m):
            return {"action": "clear"}

        def _extract_query(_m):
            return {"action": "query"}

        response_parser.register_plugin_tag(
            "notifications_clear", clear_pat, "notifications", _extract_clear
        )
        response_parser.register_plugin_tag(
            "notifications", query_pat, "notifications", _extract_query
        )
        tool_executor.register_plugin_handler(
            "notifications_clear", self._handle_notifications_tool
        )
        tool_executor.register_plugin_handler(
            "notifications", self._handle_notifications_tool
        )
        logger.info("Registered <notifications/> and <notifications clear/> tags")

    async def _handle_notifications_tool(self, tool_data: dict):
        """Handle <notifications/> (peek) and <notifications clear/>."""
        from kollabor_agent.tool_executor import ToolExecutionResult
        from kollabor_ai.notifications import render_env_block

        action = tool_data.get("action", "query")
        if self._queue is None:
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="notifications",
                success=True,
                output="[env] queue unavailable",
            )

        if action == "clear":
            count = self._queue.clear()
            return ToolExecutionResult(
                tool_id=tool_data.get("id", "unknown"),
                tool_type="notifications",
                success=True,
                output=f"[env] cleared {count} event(s)",
            )

        events = self._queue.peek()
        out = render_env_block(events) if events else "[env] empty"
        return ToolExecutionResult(
            tool_id=tool_data.get("id", "unknown"),
            tool_type="notifications",
            success=True,
            output=out,
        )

    async def shutdown(self) -> None:
        self._queue = None
