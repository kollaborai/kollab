"""Minimal Kollab Python plugin lifecycle example."""
import logging

from kollabor_plugins import BasePlugin

logger = logging.getLogger(__name__)


class HelloPlugin(BasePlugin):
    async def initialize(self, args=None, **kwargs):
        logger.info("Hello plugin initialized")

    async def shutdown(self):
        logger.info("Hello plugin stopped")
