"""Deep Thought Plugin - transparent multi-instance parallel reasoning.

Hooks into USER_INPUT_PRE to intercept messages, spawns parallel
thinkers, and injects synthesized context as a system message
before the main model responds. The model never knows.
"""

import logging
import os
import time
from typing import Any, Dict, Optional, cast

from kollabor_config import ConfigSchemaBuilder, PluginConfigSchema
from kollabor_events import EventType, Hook, HookPriority
from kollabor_events.data_models import ConversationMessage
from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    SubcommandInfo,
)
from kollabor_plugins import BasePlugin

from .child_reporter import ChildReporter
from .orchestrator import ThoughtOrchestrator

logger = logging.getLogger(__name__)


class DeepThoughtPlugin(BasePlugin):
    """Transparent parallel reasoning engine.

    Intercepts user messages, spawns parallel pipe-mode instances
    that ponder the question from different methodological angles,
    synthesizes the results, and injects them as context.

    The main LLM never sees a tool call, never knows this happened.
    It just responds with richer, more considered answers.
    """

    def __init__(
        self,
        name: str = "deep_thought",
        event_bus=None,
        renderer=None,
        config=None,
    ):
        self.name = name
        self.version = "0.1.0"
        self.description = "Transparent multi-perspective reasoning engine"
        self.enabled = True

        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config
        self.command_registry = None

        self._orchestrator: Optional[ThoughtOrchestrator] = None
        self._child_reporter: Optional[ChildReporter] = None
        self._is_pondering = False
        self._enabled = False  # runtime state, not config-dependent
        self._always_on = False
        self._total_ponders = 0
        self._total_time = 0.0
        self._last_synthesis_len = 0
        self._last_methodologies: list = []
        self._last_pondered_message: str = ""

        self.logger = logger

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            "plugins": {
                "deep_thought": {
                    "enabled": False,
                    "instance_count": 3,
                    "timeout": 45,
                    "min_message_length": 30,
                    "skip_slash_commands": True,
                    "always_on": False,
                    "trigger_keywords": [
                        "think about this",
                        "analyze this",
                        "what do you think about",
                        "how should we approach",
                        "what's the best way to",
                        "what are the tradeoffs",
                        "what are the trade-offs",
                        "pros and cons of",
                        "compare and contrast",
                        "evaluate whether",
                        "design a system",
                        "architect a solution",
                        "what strategy should",
                        "help me decide",
                        "weigh the options",
                    ],
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "Deep Thought Engine",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.deep_thought.enabled",
                    "help": "Enable transparent multi-perspective reasoning",
                },
                {
                    "type": "spinbox",
                    "label": "Instance Count",
                    "config_path": "plugins.deep_thought.instance_count",
                    "min_value": 2,
                    "max_value": 7,
                    "step": 1,
                    "help": "Number of parallel thinkers to spawn",
                },
                {
                    "type": "slider",
                    "label": "Timeout (s)",
                    "config_path": "plugins.deep_thought.timeout",
                    "min_value": 15,
                    "max_value": 120,
                    "step": 5,
                    "help": "Max seconds to wait for parallel thinkers",
                },
                {
                    "type": "checkbox",
                    "label": "Always On",
                    "config_path": "plugins.deep_thought.always_on",
                    "help": "Ponder every message (vs keyword-triggered)",
                },
                {
                    "type": "slider",
                    "label": "Min Message Length",
                    "config_path": "plugins.deep_thought.min_message_length",
                    "min_value": 10,
                    "max_value": 100,
                    "step": 5,
                    "help": "Skip messages shorter than this",
                },
            ],
        }

    @staticmethod
    def get_config_schema() -> PluginConfigSchema:
        return (
            ConfigSchemaBuilder("deep_thought")
            .add_checkbox(
                "enabled",
                label="Enabled",
                default=False,
                help_text="Enable deep thought engine",
            )
            .add_slider(
                "instance_count",
                label="Instance Count",
                default=3,
                min_value=2,
                max_value=7,
                help_text="Parallel thinker count",
            )
            .add_slider(
                "timeout",
                label="Timeout",
                default=45,
                min_value=15,
                max_value=120,
                help_text="Timeout per pondering session",
            )
            .add_checkbox(
                "always_on",
                label="Always On",
                default=False,
                help_text="Ponder every message",
            )
            .build()
        )

    async def initialize(self, **kwargs):
        """Initialize the deep thought engine."""
        # Pick up command_registry from app kwargs
        if "command_registry" in kwargs:
            self.command_registry = kwargs["command_registry"]
        if "event_bus" in kwargs:
            self.event_bus = kwargs["event_bus"]
        if "config" in kwargs:
            self.config = kwargs["config"]
        if "renderer" in kwargs:
            self.renderer = kwargs["renderer"]

        if os.environ.get("KOLLAB_DEEP_THOUGHT_CHILD") == "1":
            self._child_reporter = ChildReporter(self.event_bus)
            await self._child_reporter.initialize()
            logger.info("Deep Thought child reporter initialized")
        else:
            # Load initial state from config
            dt_config = self._get_dt_config()
            self._enabled = dt_config.get("enabled", False)
            self._always_on = dt_config.get("always_on", False)

            self._orchestrator = ThoughtOrchestrator(config=self.config)
            self._register_status_widget()
            logger.info(
                f"Deep Thought Engine initialized "
                f"(enabled={self._enabled}, always_on={self._always_on})"
            )

    async def register_hooks(self):
        """Register hooks based on parent/child role."""
        if self._child_reporter and self._child_reporter.is_child:
            await self._child_reporter.register_hooks()
            return

        hooks = [
            Hook(
                name="deep_thought_intercept",
                plugin_name=self.name,
                event_type=EventType.USER_INPUT_PRE,
                priority=HookPriority.PREPROCESSING.value,
                callback=self._intercept_user_input,
            ),
        ]

        for hook in hooks:
            await self.event_bus.register_hook(hook)

        # Register slash command
        self._register_command()

        logger.info("Deep Thought hooks registered")

    def _register_command(self):
        """Register the /deepthought slash command."""
        if not self.command_registry:
            logger.warning(
                "No command_registry available, skipping command registration"
            )
            return

        command_def = CommandDefinition(
            name="deepthought",
            description="Deep Thought Engine control",
            handler=self._handle_command,
            plugin_name=self.name,
            aliases=["dt", "ponder"],
            mode=CommandMode.INSTANT,
            category=CommandCategory.SYSTEM,
            subcommands=[
                SubcommandInfo("on", "", "Enable deep thought"),
                SubcommandInfo("off", "", "Disable deep thought"),
                SubcommandInfo("status", "", "Show deep thought stats"),
                SubcommandInfo("always", "", "Toggle always-on mode"),
                SubcommandInfo("ponder", "<question>", "Manually trigger pondering"),
            ],
        )
        self.command_registry.register_command(command_def)

    async def _handle_command(self, command, **kwargs):
        """Handle /deepthought command."""
        from kollabor_events.models import CommandResult

        # command is a SlashCommand object with .args list and .raw_input
        args = command.args if hasattr(command, "args") else []
        subcmd = args[0].lower() if args else "status"

        if subcmd == "on":
            self._set_config("enabled", True)
            return CommandResult(
                success=True,
                message="Deep Thought Engine enabled",
                display_type="success",
            )

        elif subcmd == "off":
            self._set_config("enabled", False)
            return CommandResult(
                success=True,
                message="Deep Thought Engine disabled",
                display_type="info",
            )

        elif subcmd == "always":
            current = self._get_dt_config().get("always_on", False)
            self._set_config("always_on", not current)
            state = "on" if not current else "off"
            return CommandResult(
                success=True,
                message=f"Always-on mode: {state}",
                display_type="info",
            )

        elif subcmd == "ponder" and len(args) > 1:
            question = " ".join(args[1:])
            # Manually trigger pondering
            if self._is_pondering:
                return CommandResult(
                    success=False,
                    message="Already pondering...",
                    display_type="warning",
                )
            # Fire it off - the synthesis will be injected into history
            await self._do_ponder(question)
            return CommandResult(
                success=True,
                message=(
                    f"Pondered with {len(self._last_methodologies)} "
                    f"perspectives ({self._last_synthesis_len} chars)"
                ),
                display_type="success",
            )

        else:
            # Status
            dt_config = self._get_dt_config()
            enabled = dt_config.get("enabled", False)
            always = dt_config.get("always_on", False)
            count = dt_config.get("instance_count", 3)

            lines = []
            lines.append(f"enabled: {'yes' if enabled else 'no'}")
            lines.append(f"always-on: {'yes' if always else 'no'}")
            lines.append(f"instance count: {count}")
            lines.append(f"total ponders: {self._total_ponders}")
            if self._total_ponders > 0:
                avg = self._total_time / self._total_ponders
                lines.append(f"avg time: {avg:.1f}s")
                lines.append(f"total time: {self._total_time:.1f}s")
            if self._is_pondering:
                lines.append("status: pondering...")
            return CommandResult(
                success=True,
                message="\n".join(lines),
                display_type="info",
            )

    async def _intercept_user_input(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Intercept user input, ponder if appropriate, inject context."""
        # Don't recurse
        if os.environ.get("KOLLAB_DEEP_THOUGHT_CHILD") == "1":
            return data

        if not self._is_enabled():
            return data

        if self._is_pondering:
            return data

        message = data.get("message", "")
        if not message or not message.strip():
            return data

        if not self._should_ponder(message):
            return data

        # Pipe mode check
        if self.renderer and getattr(self.renderer, "pipe_mode", False):
            return data

        # Dedup: don't ponder the same message twice in a row
        if message.strip() == self._last_pondered_message:
            return data
        self._last_pondered_message = message.strip()

        await self._do_ponder(message)
        return data

    async def _do_ponder(self, message: str):
        """Execute pondering for a message."""
        logger.info(f"Deep Thought triggered for: {message[:80]}...")
        self._is_pondering = True
        start_time = time.time()
        mds = self._get_message_display_service()

        try:
            dt_config = self._get_dt_config()
            instance_count = dt_config.get("instance_count", 3)
            timeout = dt_config.get("timeout", 45)

            context = self._get_recent_context()
            profile = self._get_active_profile_name()

            # Show pondering in the thinking bar (spinner + timer)
            if self.renderer and hasattr(self.renderer, "update_thinking"):
                self.renderer.update_thinking(True, "Pondering...")

            # Also show a system message
            if mds:
                mds.display_system_message(
                    f"Deep Thought: pondering with {instance_count} perspectives..."
                )

            def on_status(status_msg: str):
                logger.info(f"Deep Thought: {status_msg}")

            assert self._orchestrator is not None
            synthesis = await self._orchestrator.ponder(
                question=message,
                conversation_context=context,
                count=instance_count,
                timeout=timeout,
                profile_name=profile,
                on_status=on_status,
            )

            elapsed = time.time() - start_time
            self._total_ponders += 1
            self._total_time += elapsed

            if synthesis:
                self._last_synthesis_len = len(synthesis)
                self._inject_synthesis(synthesis)
                if mds:
                    mds.display_system_message(
                        f"Deep Thought: synthesized {instance_count} perspectives in {elapsed:.0f}s"
                    )
            else:
                self._last_synthesis_len = 0
                logger.info(f"Deep Thought produced no results in {elapsed:.1f}s")

        except Exception as e:
            logger.error(f"Deep Thought failed: {e}")
        finally:
            self._is_pondering = False
            # Stop the thinking animation
            if self.renderer and hasattr(self.renderer, "update_thinking"):
                self.renderer.update_thinking(False, "")

    def _get_message_display_service(self):
        """Get the message display service for showing status messages."""
        if not self.event_bus:
            return None
        llm_service = self.event_bus.get_service("llm_service")
        if llm_service and hasattr(llm_service, "message_display_service"):
            return llm_service.message_display_service
        return None

    def _is_enabled(self) -> bool:
        """Check if deep thought is enabled."""
        return self._enabled

    def _get_dt_config(self) -> Dict[str, Any]:
        """Get deep thought config section."""
        if self.config:
            return cast(Dict[str, Any], self.config.get("plugins.deep_thought", {}))
        return {}

    def _set_config(self, key: str, value: Any):
        """Set a deep thought config value (runtime + persistent)."""
        # Runtime state
        if key == "enabled":
            self._enabled = value
        elif key == "always_on":
            self._always_on = value
        # Also persist to config
        if self.config:
            self.config.save_key(f"plugins.deep_thought.{key}", value)

    def _should_ponder(self, message: str) -> bool:
        """Determine if a message warrants deep thinking."""
        dt_config = self._get_dt_config()
        message_stripped = message.strip()

        # Skip slash commands
        if dt_config.get("skip_slash_commands", True) and message_stripped.startswith(
            "/"
        ):
            return False

        # Skip very short messages
        min_length = dt_config.get("min_message_length", 30)
        if len(message_stripped) < min_length:
            return False

        # Always-on mode (check runtime flag first, then config)
        if self._always_on or dt_config.get("always_on", False):
            return True

        # Keyword trigger mode - use phrase matching (not single words)
        keywords = dt_config.get("trigger_keywords", [])
        message_lower = message_stripped.lower()
        return any(kw in message_lower for kw in keywords)

    def _get_recent_context(self) -> str:
        """Extract recent conversation context for child instances."""
        if not self.event_bus:
            return ""

        llm_service = self.event_bus.get_service("llm_service")
        if not llm_service:
            return ""

        history = getattr(llm_service, "conversation_history", [])
        if not history:
            return ""

        # Take last 6 messages for context (3 exchanges)
        recent = history[-6:]
        parts = []
        for msg in recent:
            role = msg.role if hasattr(msg, "role") else "unknown"
            content = msg.content if hasattr(msg, "content") else str(msg)
            if len(content) > 500:
                content = content[:500] + "..."
            parts.append(f"[{role}]: {content}")

        return "\n\n".join(parts)

    def _get_active_profile_name(self) -> Optional[str]:
        """Get the currently active LLM profile name for child inheritance."""
        if not self.event_bus:
            return None

        llm_service = self.event_bus.get_service("llm_service")
        if not llm_service:
            return None

        pm = getattr(llm_service, "profile_manager", None)
        if pm and hasattr(pm, "active_profile_name"):
            return cast(Optional[str], pm.active_profile_name)

        return None

    def _inject_synthesis(self, synthesis: str):
        """Inject synthesized reasoning into conversation history."""
        if not self.event_bus:
            return

        llm_service = self.event_bus.get_service("llm_service")
        if not llm_service:
            return

        history = getattr(llm_service, "conversation_history", None)
        if history is None:
            return

        msg = ConversationMessage(role="user", content=synthesis)
        history.append(msg)
        logger.info("Injected deep thought synthesis as user message")

    def _register_status_widget(self):
        """Register the Deep Thought status widget."""
        try:
            if not self.event_bus:
                logger.warning("No event_bus for deep thought widget registration")
                return
            widget_api = self.event_bus.get_service("widget_api")
            if not widget_api:
                logger.warning("Widget API not available for deep thought widget")
                return
            # Register via registry directly to get interactive toggle support
            widget_api._registry.register(
                id="deep-thought",
                name="Deep Thought",
                description="Multi-perspective reasoning engine status",
                render_fn=self._render_status_widget,
                default_width="auto",
                min_width=8,
                interactive=True,
                interaction_type="action",
                on_activate=self._on_widget_activate,
            )
            logger.info("Registered deep-thought status widget")

            # Add to active layout row 4 if not already there
            layout_mgr = self.event_bus.get_service("layout_manager")
            if layout_mgr and hasattr(layout_mgr, "_layout") and layout_mgr._layout:
                layout = layout_mgr._layout
                for row in layout.rows:
                    if row.id == 4:
                        widget_ids = [w.id for w in row.widgets]
                        if "deep-thought" not in widget_ids:
                            from kollabor_tui.status.layout_manager import (
                                WidgetConfig,
                                WidgetWidth,
                            )

                            row.widgets.insert(
                                len(row.widgets) - 2,  # before sysmon widgets
                                WidgetConfig(
                                    id="deep-thought",
                                    width=WidgetWidth.auto(),
                                ),
                            )
                            logger.info("Added deep-thought widget to row 4")
                        break
        except Exception as e:
            logger.error(f"Failed to register deep thought widget: {e}")

    async def _on_widget_activate(self, widget_id: str, context) -> dict:
        """Handle Enter press on the dt widget - toggle on/off."""

        if widget_id != "deep-thought":
            logger.debug(f"Ignoring activation on non-DT widget: {widget_id}")
            return {}
        enabled = self._is_enabled()
        self._set_config("enabled", not enabled)
        new_state = "on" if not enabled else "off"
        logger.info(f"Deep Thought toggled via widget: {new_state}")

        mds = self._get_message_display_service()
        if mds:
            label = "enabled" if new_state == "on" else "disabled"
            mds.display_system_message(f"Deep Thought Engine {label}")

        return {"new_state": new_state}

    def _render_status_widget(self, width: int, context) -> str:
        """Render the deep thought status widget."""
        from kollabor_tui.design_system import T

        def _color(c):
            """Unwrap gradient lists to a single (r,g,b) tuple."""
            return c[0] if isinstance(c, list) else c

        def _fg(text: str, color) -> str:
            r, g, b = _color(color)
            return f"\033[38;2;{r};{g};{b}m{text}\033[39m"

        if not self._is_enabled():
            return _fg("dt:off", T().text_dim)

        if self._is_pondering:
            return _fg("dt:pondering...", T().warning)

        if self._total_ponders > 0:
            avg = self._total_time / self._total_ponders
            return (
                _fg("dt:", T().text_dim)
                + _fg(str(self._total_ponders), T().success)
                + _fg(f" avg:{avg:.0f}s", T().text_dim)
            )

        return _fg("dt:on", T().success)

    def get_status_line(self) -> str:
        """Status line contribution (legacy)."""
        if self._is_pondering:
            return "pondering..."
        if not self._is_enabled():
            return ""
        if self._total_ponders > 0:
            avg = self._total_time / self._total_ponders
            return f"dt:{self._total_ponders} avg:{avg:.1f}s"
        return "dt:ready"

    async def shutdown(self):
        """Clean up."""
        if self._child_reporter:
            await self._child_reporter.shutdown()
        if self._orchestrator:
            await self._orchestrator._cleanup_children()
        logger.info(
            f"Deep Thought shutdown. Total ponders: {self._total_ponders}, "
            f"Total time: {self._total_time:.1f}s"
        )
