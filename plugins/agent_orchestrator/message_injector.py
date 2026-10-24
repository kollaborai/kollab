"""Inject messages back into conversation as user role."""

import logging
from typing import Optional

from kollabor_events.models import EventType

logger = logging.getLogger(__name__)


class MessageInjector:
    """Inject messages back into conversation as user role.

    Formats messages with delimiters and adds them to the conversation
    history. Can optionally trigger the LLM to continue generating.
    """

    def __init__(self, event_bus, conversation_manager):
        """Initialize message injector.

        Args:
            event_bus: Event bus for emitting events
            conversation_manager: Conversation manager for adding messages
        """
        self.event_bus = event_bus
        self.conversation_manager = conversation_manager

    async def inject(
        self,
        source: str,
        content: str,
        trigger_llm: bool = True,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Inject message into conversation.

        Args:
            source: Source identifier (e.g., agent name)
            content: Message content
            trigger_llm: Whether to trigger LLM to continue
            metadata: Optional metadata dict

        Returns:
            True if injection was successful
        """
        # Format with XML tags (hidden from user display)
        formatted = f"""<sys_msg>
{content}
</sys_msg>"""

        # Emit pre-injection event (plugins can modify/block)
        context = {
            "source": source,
            "content": formatted,
            "original_content": content,
            "trigger_llm": trigger_llm,
            "metadata": metadata or {},
            "blocked": False,
        }

        try:
            # Emit pre-injection event (plugins can modify/block)
            context = await self.event_bus.emit_with_hooks(
                EventType.PRE_MESSAGE_INJECT, context, "message_injector"
            )
        except Exception as e:
            logger.debug(f"Pre-injection event error: {e}")

        if context.get("blocked"):
            logger.info(f"Message injection blocked for source: {source}")
            return False

        # Add to conversation as user message
        try:
            self.conversation_manager.add_message(
                role="user", content=context["content"]
            )
            logger.info(f"Injected message from: {source}")
        except Exception as e:
            logger.error(f"Failed to inject message: {e}")
            return False

        # Emit post-injection event
        try:
            await self.event_bus.emit_with_hooks(
                EventType.POST_MESSAGE_INJECT, context, "message_injector"
            )
        except Exception as e:
            logger.debug(f"Post-injection event error: {e}")

        # Trigger LLM to continue processing
        if context.get("trigger_llm", True):
            try:
                await self.event_bus.emit_with_hooks(
                    EventType.TRIGGER_LLM_CONTINUE,
                    {"source": source, "content": content},
                    "message_injector",
                )
                logger.info(f"Triggered LLM continue for source: {source}")
            except Exception as e:
                logger.error(f"Failed to trigger LLM continue: {e}")

        return True

    async def inject_raw(
        self,
        content: str,
        trigger_llm: bool = False,
    ) -> bool:
        """Inject raw content without formatting.

        Args:
            content: Raw content to inject
            trigger_llm: Whether to trigger LLM to continue

        Returns:
            True if injection was successful
        """
        try:
            self.conversation_manager.add_message(role="user", content=content)
            logger.info("Injected raw message")

            if trigger_llm:
                try:
                    await self.event_bus.emit_with_hooks(
                        EventType.TRIGGER_LLM_CONTINUE,
                        {"source": "raw_inject"},
                        "message_injector",
                    )
                    logger.info("Triggered LLM continue for raw inject")
                except Exception as e:
                    logger.error(f"Failed to trigger LLM continue: {e}")

            return True
        except Exception as e:
            logger.error(f"Failed to inject raw message: {e}")
            return False
