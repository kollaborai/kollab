"""Context Service Plugin — registers ContextService on the event bus.

Lightweight plugin wrapper that bootstraps the ContextService
and exposes its config widgets to the /config UI.
"""

import logging
from typing import Any, Dict, Optional

from kollabor_plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class ContextServicePlugin(BasePlugin):
    """Plugin wrapper for ContextService lifecycle and config."""

    def __init__(self, name: str, event_bus: Any, renderer: Any, config: Any) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self._service = None

    async def initialize(self, args: Optional[Any] = None, **kwargs) -> None:
        """Bootstrap ContextService if not already registered."""
        existing = self.event_bus.get_service("context_service")
        if existing is not None:
            logger.info("ContextService already registered, skipping bootstrap")
            self._service = existing
            return

        from kollabor_ai.context_service.service import ContextService

        heavy_kb = 8
        curate_kb = 300

        if self.config:
            try:
                heavy_kb = self.config.get(
                    "plugins.context_service.heavy_threshold_kb", heavy_kb
                )
                curate_kb = self.config.get(
                    "plugins.context_service.curate_threshold_kb", curate_kb
                )
            except Exception:
                pass

        self._service = ContextService(
            heavy_threshold_kb=heavy_kb,
            curate_threshold_kb=curate_kb,
        )
        self._service.set_event_bus(self.event_bus)
        self.event_bus.register_service("context_service", self._service)
        logger.info(
            f"ContextService bootstrapped "
            f"(heavy={heavy_kb}KB, curate={curate_kb}KB)"
        )

    async def shutdown(self) -> None:
        """Clean up ContextService."""
        if self._service:
            logger.info("ContextService plugin shutting down")
            self._service = None

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Return default config values for all ContextService widgets."""
        return {
            "plugins": {
                "context_service": {
                    "enabled": True,
                    "heavy_threshold_kb": 8,
                    "curate_threshold_kb": 300,
                    "curator_throttle_turns": 2,
                    "file_dedup_mode": "stale_hit",
                    "default_decision": "summary",
                    "hub_broadcast_enabled": False,
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        """Expose ContextService config widgets to /config UI."""
        from kollabor_ai.context_service.service import ContextService

        return ContextService.get_config_widgets()
