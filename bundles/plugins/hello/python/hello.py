"""Minimal Kollab Python plugin lifecycle example."""

import logging

from kollabor_events.models import CommandCategory, CommandDefinition, CommandMode, CommandResult
from kollabor_plugins import BasePlugin

logger = logging.getLogger(__name__)


class HelloPlugin(BasePlugin):
    async def initialize(self, event_bus=None, config=None, **kwargs):
        registry = kwargs.get("command_registry")
        if registry is not None:
            registry.register_command(
                CommandDefinition(
                    name="hello",
                    description="Say hello from the example plugin",
                    handler=self._hello,
                    plugin_name=self.name,
                    mode=CommandMode.INSTANT,
                    category=CommandCategory.SYSTEM,
                )
            )
        logger.info("Hello plugin initialized")

    async def _hello(self, command, **kwargs):
        return CommandResult(success=True, message="Hello from the plugin!", display_type="info")

    async def shutdown(self):
        logger.info("Hello plugin stopped")
