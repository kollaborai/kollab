"""Comprehensive hook system for LLM core service.

Provides complete hook coverage for all LLM operations including
pre/post processing, tool calls, and intelligence features.
"""

import logging
from typing import Any, Dict

from kollabor_events import EventType, Hook, HookPriority

logger = logging.getLogger(__name__)


class LLMHookSystem:
    """Comprehensive hook system for LLM operations.

    Manages registration and execution of hooks for all LLM events,
    providing extensive customization points for the core service.
    """

    def __init__(self, event_bus):
        """Initialize the LLM hook system.

        Args:
            event_bus: Event bus for hook registration
        """
        self.event_bus = event_bus
        self.hooks = []
        self._create_hooks()

        logger.info("LLM Hook System initialized")

    def _create_hooks(self):
        """Create all LLM hooks."""
        # Pre-processing hooks
        self.hooks.extend(
            [
                Hook(
                    name="llm_pre_user_input",
                    plugin_name="llm_core",
                    event_type=EventType.USER_INPUT_PRE,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_pre_user_input,
                ),
                Hook(
                    name="llm_pre_request",
                    plugin_name="llm_core",
                    event_type=EventType.LLM_REQUEST_PRE,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_pre_llm_request,
                ),
                Hook(
                    name="llm_pre_tool_call",
                    plugin_name="llm_core",
                    event_type=EventType.TOOL_CALL_PRE,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_pre_tool_call,
                ),
            ]
        )

        # Processing hooks
        self.hooks.extend(
            [
                Hook(
                    name="llm_request",
                    plugin_name="llm_core",
                    event_type=EventType.LLM_REQUEST,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_llm_request,
                ),
                Hook(
                    name="llm_tool_call",
                    plugin_name="llm_core",
                    event_type=EventType.TOOL_CALL,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_tool_call,
                ),
            ]
        )

        # Post-processing hooks
        self.hooks.extend(
            [
                Hook(
                    name="llm_post_response",
                    plugin_name="llm_core",
                    event_type=EventType.LLM_RESPONSE_POST,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_post_llm_response,
                ),
                Hook(
                    name="llm_post_tool_call",
                    plugin_name="llm_core",
                    event_type=EventType.TOOL_CALL_POST,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_post_tool_call,
                ),
                Hook(
                    name="llm_post_user_response",
                    plugin_name="llm_core",
                    event_type=EventType.USER_INPUT_POST,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_post_user_response,
                ),
            ]
        )

        # System hooks
        self.hooks.extend(
            [
                Hook(
                    name="llm_startup",
                    plugin_name="llm_core",
                    event_type=EventType.SYSTEM_STARTUP,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_conversation_start,
                ),
                Hook(
                    name="llm_shutdown",
                    plugin_name="llm_core",
                    event_type=EventType.SYSTEM_SHUTDOWN,
                    priority=HookPriority.SYSTEM.value,
                    callback=self._handle_conversation_end,
                ),
            ]
        )

        # Thinking process hook
        self.hooks.append(
            Hook(
                name="llm_thinking",
                plugin_name="llm_core",
                event_type=EventType.LLM_THINKING,
                priority=HookPriority.SYSTEM.value,
                callback=self._handle_thinking,
            )
        )

        logger.debug(f"Created {len(self.hooks)} LLM hooks")

    async def register_hooks(self):
        """Register all hooks with the event bus."""
        for hook in self.hooks:
            success = await self.event_bus.register_hook(hook)
            if success:
                logger.debug(f"Registered hook: {hook.name}")
            else:
                logger.error(f"Failed to register hook: {hook.name}")

        logger.info(f"Registered {len(self.hooks)} LLM hooks")

    async def _handle_pre_user_input(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle pre-user-input events.

        This hook runs before user input is processed, allowing for
        message enhancement, validation, or filtering.
        """
        message = data.get("message", "")

        # Context enrichment
        enriched_data = {
            "original_message": message,
            "message": message,
            "timestamp": event.timestamp if hasattr(event, "timestamp") else None,
            "session_context": self._get_session_context(),
        }

        logger.debug(f"Pre-processing user input: {message[:50]}...")
        return enriched_data

    async def _handle_pre_llm_request(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle pre-LLM-request events.

        This hook runs before sending requests to the LLM API,
        allowing for prompt engineering and context optimization.
        """
        messages = data.get("messages", [])

        # Prompt optimization could happen here
        optimized_data = {
            "messages": messages,
            "optimization_applied": False,
            "context_window": len(str(messages)),
        }

        logger.debug(f"Pre-processing LLM request with {len(messages)} messages")
        return optimized_data

    async def _handle_pre_tool_call(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle pre-tool-call events.

        This hook runs before executing tool calls,
        allowing for validation and security checks.
        """
        command = data.get("command", "")

        # Security validation
        validated_data = {
            "command": command,
            "validated": True,
            "risk_level": self._assess_command_risk(command),
        }

        logger.debug(f"Pre-processing tool call: {command[:50]}...")
        return validated_data

    async def _handle_llm_request(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle LLM request events.

        This hook runs during LLM API calls,
        allowing for monitoring and metrics collection.
        """
        request_data = {
            "status": "processing",
            "request_id": event.uuid if hasattr(event, "uuid") else None,
            "model": data.get("model", "unknown"),
        }

        logger.debug("Processing LLM request")
        return request_data

    async def _handle_tool_call(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle tool call events.

        This hook runs during tool execution,
        allowing for monitoring and result processing.
        """
        tool_data = {
            "command": data.get("command", ""),
            "status": "executing",
            "tool_id": event.uuid if hasattr(event, "uuid") else None,
        }

        logger.debug(f"Executing tool call: {tool_data['command'][:50]}...")
        return tool_data

    async def _handle_post_llm_response(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle post-LLM-response events.

        This hook runs after receiving LLM responses,
        allowing for response processing and analysis.
        """
        response = data.get("response", "")

        # Response analysis
        analysis_data = {
            "response_length": len(response),
            "has_code": "```" in response,
            "has_thinking": "<think>" in response,
            "quality_score": self._assess_response_quality(response),
        }

        logger.debug(f"Post-processing LLM response: {len(response)} chars")
        return analysis_data

    async def _handle_post_tool_call(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle post-tool-call events.

        This hook runs after tool execution completes,
        allowing for result processing and logging.
        """
        output = data.get("output", "")

        # Result processing
        result_data = {
            "output_length": len(output),
            "success": data.get("success", True),
            "execution_time": data.get("execution_time", 0),
        }

        logger.debug(f"Post-processing tool call output: {len(output)} chars")
        return result_data

    async def _handle_post_user_response(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle post-user-response events.

        This hook runs after user input has been fully processed,
        allowing for conversation flow management.
        """
        processed_data = {
            "message_processed": True,
            "conversation_state": "active",
            "follow_up_needed": self._check_follow_up_needed(data),
        }

        logger.debug("Post-processing user response complete")
        return processed_data

    async def _handle_conversation_start(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle conversation start events.

        This hook runs when a conversation begins,
        allowing for initialization and context setup.
        """
        start_data = {
            "conversation_started": True,
            "initial_context": self._build_initial_context(),
            "welcome_message": "LLM Core Service ready",
        }

        logger.info("Conversation started")
        return start_data

    async def _handle_conversation_end(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Handle conversation end events.

        This hook runs when a conversation ends,
        allowing for cleanup and summary generation.
        """
        end_data = {
            "conversation_ended": True,
            "summary": self._generate_conversation_summary(data),
            "cleanup_complete": True,
        }

        logger.info("Conversation ended")
        return end_data

    async def _handle_thinking(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Handle thinking process events.

        This hook runs during LLM thinking phases,
        allowing for thinking visualization and analysis.
        """
        thinking_data = {
            "thinking_content": data.get("thinking", ""),
            "phase": data.get("phase", "processing"),
            "visualization": "enabled",
        }

        logger.debug("Processing thinking phase")
        return thinking_data

    def _get_session_context(self) -> Dict[str, Any]:
        """Get current session context."""
        return {"active": True, "hooks_enabled": len(self.hooks), "system_ready": True}

    # Risk assessment patterns for tool call validation
    HIGH_RISK_PATTERNS = ("rm -rf", "sudo", "chmod 777", "mkfs", "dd if=")
    MEDIUM_RISK_PATTERNS = ("rm", "mv", "cp -r", "git reset --hard")

    # Response quality scoring weights
    QUALITY_BASE_SCORE = 0.5
    QUALITY_LENGTH_BONUS = 0.1  # response > 100 chars
    QUALITY_CODE_BONUS = 0.1  # contains code blocks
    QUALITY_REASONING_BONUS = 0.1  # contains reasoning words
    QUALITY_SHORT_PENALTY = -0.2  # response < 10 chars

    def _assess_command_risk(self, command: str) -> str:
        """Assess risk level of a command."""

        command_lower = command.lower()

        for pattern in self.HIGH_RISK_PATTERNS:
            if pattern in command_lower:
                return "high"

        for pattern in self.MEDIUM_RISK_PATTERNS:
            if pattern in command_lower:
                return "medium"

        return "low"

    def _assess_response_quality(self, response: str) -> float:
        """Assess quality of LLM response."""
        score = self.QUALITY_BASE_SCORE

        if len(response) > 100:
            score += self.QUALITY_LENGTH_BONUS
        if "```" in response:
            score += self.QUALITY_CODE_BONUS
        if any(
            word in response.lower() for word in ["because", "therefore", "however"]
        ):
            score += self.QUALITY_REASONING_BONUS

        if len(response) < 10:
            score += self.QUALITY_SHORT_PENALTY

        return min(max(score, 0.0), 1.0)

    def _check_follow_up_needed(self, data: Dict[str, Any]) -> bool:
        """Check if follow-up is needed."""
        response = data.get("response", "")

        # Check for indicators that follow-up is needed
        follow_up_indicators = [
            "?",  # Question asked
            "let me know",
            "would you like",
            "should I",
            "shall I",
        ]

        response_lower = response.lower()
        return any(indicator in response_lower for indicator in follow_up_indicators)

    def _build_initial_context(self) -> Dict[str, Any]:
        """Build initial conversation context."""
        return {
            "project_loaded": True,
            "hooks_active": len(self.hooks),
            "intelligence_enabled": True,
        }

    def _generate_conversation_summary(self, data: Dict[str, Any]) -> str:
        """Generate conversation summary."""
        messages = data.get("message_count", 0)
        duration = data.get("duration", 0)

        return (
            f"Conversation completed: {messages} messages over {duration:.1f} seconds"
        )
