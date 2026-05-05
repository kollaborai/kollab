"""Event processor for handling pre/post event processing logic."""

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Union

from .executor import HookExecutor
from .models import Event, EventType

if TYPE_CHECKING:
    from .registry import HookRegistry

logger = logging.getLogger(__name__)


class EventProcessor:
    """Handles pre/post event processing with clean separation of concerns.

    This class manages the complex logic of processing events through
    their pre, main, and post phases while maintaining data flow integrity.
    """

    def __init__(self, hook_registry: "HookRegistry", hook_executor: HookExecutor):
        """Initialize the event processor.

        Args:
            hook_registry: Registry for managing hooks.
            hook_executor: Executor for individual hooks.
        """
        self.hook_registry = hook_registry
        self.hook_executor = hook_executor

        # Pre/post event type mappings
        self.pre_post_map = {
            EventType.USER_INPUT: (EventType.USER_INPUT_PRE, EventType.USER_INPUT_POST),
            EventType.KEY_PRESS: (EventType.KEY_PRESS_PRE, EventType.KEY_PRESS_POST),
            EventType.LLM_REQUEST: (
                EventType.LLM_REQUEST_PRE,
                EventType.LLM_REQUEST_POST,
            ),
            EventType.LLM_RESPONSE: (
                EventType.LLM_RESPONSE_PRE,
                EventType.LLM_RESPONSE_POST,
            ),
            EventType.TOOL_CALL: (EventType.TOOL_CALL_PRE, EventType.TOOL_CALL_POST),
        }

        logger.debug("EventProcessor initialized")

    async def process_event_with_phases(
        self, event_type: Union[EventType, str], data: Dict[str, Any], source: str
    ) -> Dict[str, Any]:
        """Process an event through pre, main, and post phases.

        For string event types (plugin custom events), only the MAIN phase is processed.
        For EventType enum events, pre/post phases are also processed if mapped.

        Args:
            event_type: Type of event to process (EventType enum or str).
            data: Event data.
            source: Source of the event.

        Returns:
            Results from all phases of processing.
        """
        event_type_str = (
            event_type.value if isinstance(event_type, EventType) else event_type
        )

        results = {
            "pre": {},
            "main": {},
            "post": {},
            "cancelled": False,
            "source": source,
            "event_type": event_type_str,
        }

        event_data = data.copy()

        # Only EventType enums have pre/post mappings
        pre_event_type: Optional[EventType] = None
        post_event_type: Optional[EventType] = None
        if isinstance(event_type, EventType):
            mapping = self.pre_post_map.get(event_type)
            if mapping is not None:
                pre_event_type, post_event_type = mapping

        # Phase 1: PRE hooks (only for EventType enums)
        if pre_event_type:
            pre_results = await self._process_phase(
                pre_event_type, event_data, source, "PRE"
            )
            results["pre"] = pre_results

            if pre_results.get("cancelled", False):
                results["cancelled"] = True
                logger.info(f"Event {event_type_str} cancelled during PRE phase")
                return results

            # Update data from PRE phase
            event_data = pre_results.get("final_data", event_data)

        # Phase 2: MAIN event (both EventType and str)
        main_results = await self._process_phase(event_type, event_data, source, "MAIN")
        results["main"] = main_results

        if main_results.get("cancelled", False):
            results["cancelled"] = True
            logger.info(f"Event {event_type_str} cancelled during MAIN phase")
            return results

        # Update data from MAIN phase
        event_data = main_results.get("final_data", event_data)

        # Phase 3: POST hooks (only for EventType enums)
        if post_event_type and not main_results.get("cancelled", False):
            post_results = await self._process_phase(
                post_event_type, event_data, source, "POST"
            )
            results["post"] = post_results

            if post_results.get("cancelled", False):
                results["cancelled"] = True
                logger.info(f"Event {event_type_str} cancelled during POST phase")

        return results

    async def _process_phase(
        self,
        event_type: Union[EventType, str],
        data: Dict[str, Any],
        source: str,
        phase_name: str,
    ) -> Dict[str, Any]:
        """Process a single phase (PRE, MAIN, or POST) of an event.

        Args:
            event_type: Type of event for this phase (EventType enum or str).
            data: Event data.
            source: Source of the event.
            phase_name: Name of the phase for logging.

        Returns:
            Results from processing this phase.
        """
        event_type_str = (
            event_type.value if isinstance(event_type, EventType) else event_type
        )

        phase_results: Dict[str, Any] = {
            "phase": phase_name,
            "event_type": event_type_str,
            "hook_results": [],
            "cancelled": False,
            "final_data": data.copy(),
            "stats": {},
        }

        # Get hooks for this event type
        hooks = self.hook_registry.get_hooks_for_event(event_type)

        if not hooks:
            logger.debug(
                f"No hooks registered for {event_type_str} ({phase_name} phase)"
            )
            phase_results["stats"] = {"total_hooks": 0}
            return phase_results

        # Create event object
        event = Event(type=event_type, data=data.copy(), source=source)
        # Execute hooks in priority order
        for hook in hooks:
            if event.cancelled:
                logger.debug("Skipping remaining hooks due to event cancellation")
                break

            hook_result = await self.hook_executor.execute_hook(hook, event)
            phase_results["hook_results"].append(hook_result)

            # Update hook status in registry
            hook_key = hook_result["hook_key"]
            if hook_result["success"]:
                self.hook_registry.update_hook_status(hook_key, "completed")
            elif hook_result.get("error") == "timeout":
                self.hook_registry.update_hook_status(hook_key, "timeout")
            elif hook_result.get("error"):
                self.hook_registry.update_hook_status(hook_key, "failed")

        # Update final results
        phase_results["cancelled"] = event.cancelled
        phase_results["final_data"] = event.data
        phase_results["stats"] = self.hook_executor.get_execution_stats(
            phase_results["hook_results"]
        )

        return phase_results

    def add_event_type_mapping(
        self, main_event: EventType, pre_event: EventType, post_event: EventType
    ) -> None:
        """Add a new event type mapping for pre/post processing.

        Args:
            main_event: The main event type.
            pre_event: The pre-processing event type.
            post_event: The post-processing event type.
        """
        self.pre_post_map[main_event] = (pre_event, post_event)
        logger.info(
            f"Added event type mapping: {main_event.value} -> {pre_event.value}/{post_event.value}"
        )

    def get_supported_event_types(
        self,
    ) -> Dict[EventType, Tuple[Optional[EventType], Optional[EventType]]]:
        """Get all supported event types and their pre/post mappings.

        Returns:
            Dictionary mapping main events to their pre/post event types.
        """
        return dict(self.pre_post_map)  # type: ignore[return-value]
