"""Child Reporter - hooks into child instances to stream results back to parent.

When a kollabor instance detects it's a Deep Thought child (via env vars),
this module connects to the parent's socket server and streams the LLM
response back as it's generated.
"""

import logging
import os
from typing import Any, Dict, Optional

from kollabor_events import EventType, Hook, HookPriority

from .socket_server import ThoughtClient

logger = logging.getLogger(__name__)


class ChildReporter:
    """Reports child instance reasoning back to parent via unix socket.

    Hooks into LLM_RESPONSE_POST to capture the full response and
    send it to the parent's ThoughtServer.
    """

    def __init__(self, event_bus):
        self.event_bus = event_bus
        self._client: Optional[ThoughtClient] = None
        self._connected = False
        self._socket_path = os.environ.get("KOLLAB_DEEP_THOUGHT_SOCKET", "")
        self._instance_id = os.environ.get("KOLLAB_DEEP_THOUGHT_INSTANCE", "unknown")
        self._methodology = os.environ.get(
            "KOLLAB_DEEP_THOUGHT_METHODOLOGY", "unknown"
        )

    @property
    def is_child(self) -> bool:
        """Check if this instance is a Deep Thought child."""
        return os.environ.get("KOLLAB_DEEP_THOUGHT_CHILD") == "1"

    async def initialize(self):
        """Connect to parent if we're a child instance."""
        if not self.is_child or not self._socket_path:
            return

        self._client = ThoughtClient(
            socket_path=self._socket_path,
            instance_id=self._instance_id,
            methodology=self._methodology,
        )

        try:
            self._connected = await self._client.connect()
            if self._connected:
                logger.info(
                    f"Child reporter connected to parent "
                    f"(instance: {self._instance_id})"
                )
            else:
                logger.warning("Child reporter failed to connect to parent")
        except Exception as e:
            logger.error(f"Child reporter connection error: {e}")

    async def register_hooks(self):
        """Register hooks to capture LLM responses."""
        if not self.is_child:
            return

        hook = Hook(
            name="deep_thought_child_report",
            plugin_name="deep_thought_child",
            event_type=EventType.LLM_RESPONSE_POST,
            priority=HookPriority.POSTPROCESSING.value,
            callback=self._on_response,
        )
        await self.event_bus.register_hook(hook)
        logger.info("Child reporter hooks registered")

    async def _on_response(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Capture the LLM response and send it to parent."""
        if not self._connected or not self._client:
            return data

        response_text = data.get("response_text", "")
        clean_response = data.get("clean_response", response_text)

        try:
            await self._client.send_done(
                content=clean_response,
                summary="",
            )
            logger.info(f"Sent response to parent: {len(clean_response)} chars")
        except Exception as e:
            logger.error(f"Failed to send response to parent: {e}")
            try:
                await self._client.send_error(str(e))
            except Exception:
                pass

        return data

    async def shutdown(self):
        """Close the connection to parent."""
        if self._client:
            await self._client.close()
