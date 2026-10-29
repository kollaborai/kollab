"""Context compaction plugin for automatic conversation summarization.

Monitors conversation length and automatically compresses old messages
into a summary when thresholds are reached. Keeps recent interactions
intact and swaps history seamlessly between LLM turns.
"""

import asyncio
import copy
import json
import logging
import math
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from kollabor_ai.fast_model import resolve_fast_model
from kollabor_config.config_utils import resolve_global_path
from kollabor_events import EventType, Hook, HookPriority
from kollabor_events.data_models import ConversationMessage
from kollabor_events.models import CommandCategory, CommandDefinition, SubcommandInfo
from kollabor_plugins import BasePlugin
from kollabor_tui.display_tap import publish_semantic

logger = logging.getLogger(__name__)


# Keep verbatim hub context bounded by both count and payload size.
MAX_PRESERVED_HUB_TASK_MESSAGES = 24
MAX_PRESERVED_HUB_TASK_CHARS = 4000
MAX_PRESERVED_COORDINATION_MESSAGES = 32
MAX_PRESERVED_COORDINATION_CHARS = 16000
MAX_EMERGENCY_SUMMARY_MESSAGE_CHARS = 8000


def _load_model_registry() -> Dict[str, Any]:
    """Load the model registry from bundles/data/models.json.

    Resolution order:
      1. ~/.kollab/models.json (user overrides, merged on top)
      2. bundles/data/models.json (bundled defaults)

    Returns the merged registry dict with "models" and "provider_defaults" keys.
    Falls back to minimal hardcoded defaults if both files are missing.
    """
    registry: Dict[str, Any] = {"models": {}, "provider_defaults": {}}

    # Bundled defaults -- try package root, then common install paths
    bundled_paths = [
        Path(__file__).parent.parent / "bundles" / "data" / "models.json",
        resolve_global_path("bundles", "data", "models.json"),
    ]
    for p in bundled_paths:
        if p.exists():
            try:
                with open(p) as f:
                    registry = json.load(f)
                break
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load bundled model registry {p}: {e}")

    # User overrides (merge on top)
    user_path = resolve_global_path("models.json")
    if user_path.exists():
        try:
            with open(user_path) as f:
                user_data = json.load(f)
            # Merge models (user wins)
            if "models" in user_data:
                registry.setdefault("models", {}).update(user_data["models"])
            if "provider_defaults" in user_data:
                registry.setdefault("provider_defaults", {}).update(
                    user_data["provider_defaults"]
                )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load user model overrides {user_path}: {e}")

    return registry


# Load once at import time
_MODEL_REGISTRY = _load_model_registry()

# Summarization system prompt template
SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a conversation summarizer for a multi-agent AI system. "
    "Compress the following conversation into a concise briefing that "
    "preserves all critical context for the AI assistant to continue "
    "the conversation seamlessly.\n\n"
    "CRITICAL -- ALWAYS PRESERVE VERBATIM:\n"
    "- Any pending task assignments from other agents (messages prefixed "
    "with [hub channel: ...]). Copy the full task description word for word.\n"
    "- Any instructions to report back to another agent (e.g. 'report back "
    "to jarvis', 'tell lapis when done', 'send results to peridot').\n"
    "- Any <hub_msg> syntax instructions or examples.\n"
    "- File paths being actively edited or referenced in pending work.\n"
    "- Exact commands or queries the agent was asked to execute but hasn't "
    "completed yet.\n\n"
    "Preserve (can paraphrase):\n"
    "- Key decisions and conclusions\n"
    "- Technical specifics (function names, config keys, API endpoints)\n"
    "- The user's current goals and working context\n"
    "- Any preferences or patterns established\n\n"
    "Do NOT include:\n"
    "- Greetings or meta-commentary\n"
    "- Tool call details (just mention what tools accomplished)\n"
    "- Duplicate information\n"
    "- Completed tasks that have no bearing on current work\n\n"
    "CRITICAL: Any hub task assignments (messages between agents with "
    "action items, deadlines, or report-back instructions) must be "
    "preserved VERBATIM in your summary. These are contracts between "
    "agents. Look for patterns like '[hub channel:', '<hub_msg', "
    "'report back to', 'task:', 'directive:'. "
    "Do NOT paraphrase these.\n\n"
    "LANGUAGE: ALWAYS write the summary in English regardless of what "
    "languages appear in the conversation. The user and all agents "
    "communicate in English.\n\n"
    "DEDUPLICATION: If the same information appears multiple times in "
    "the conversation (e.g. repeated status updates, scratchpad writes, "
    "review notes), include it ONCE in the summary.\n\n"
    "Format: structured bullet points, dense, under {max_tokens} tokens.\n"
    "If there are active tasks from hub agents, put them in a section "
    'labeled "ACTIVE TASKS:" at the top of your summary.'
)

# Patterns that indicate a message contains a task assignment
_TASK_INDICATORS = [
    "report back",
    "send results to",
    "tell me when",
    "let me know when",
    "when you're done",
    "when done",
    "report to",
    "respond to",
    "reply to",
    "get back to",
    "count ",
    "find ",
    "list ",
    "check ",
    "create ",
    "update ",
    "fix ",
    "implement ",
    "build ",
    "deploy ",
    "run ",
    "execute ",
    "analyze ",
    "review ",
    "investigate ",
]

SUMMARY_INJECTION_PREFIX = (
    "[Previous Context Summary]\n"
    "The following is a summary of our earlier conversation:\n\n"
)
SUMMARY_INJECTION_SUFFIX = "\n\nContinue from where we left off."

# Coordination in flight defers compaction one turn in the normal case (a queued
# HUD entry drains on the next request), but a hub reply that never arrives kept
# koordinator deferring ~12 turns in a row. After this many, compact anyway.
MAX_COORDINATION_DEFERRALS = 3


AUTO_THRESHOLD_CAP = 272_000


class ContextCompactionPlugin(BasePlugin):
    """Automatically compacts conversation history by summarizing old messages."""

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        return {
            "plugins": {
                "context_compaction": {
                    "enabled": True,
                    "token_threshold_k": 0,  # 0 = auto (provider-based)
                    "compaction_ratio": 0.75,  # compact at 75% of context window
                    "min_human_turns": 6,
                    "keep_recent": 8,
                    "summarization_profile": None,
                    "max_summary_tokens": 2000,
                    "log_compaction_events": True,
                    "ask_model_first": True,
                    "summarization_model": None,
                    "max_session_turns": 0,
                    "hard_cap_compaction_attempts": 2,
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "Context Compaction",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.context_compaction.enabled",
                    "help": "Enable automatic context compaction",
                },
                {
                    "type": "slider",
                    "label": "Compaction Ratio",
                    "config_path": "plugins.context_compaction.compaction_ratio",
                    "min_value": 0.50,
                    "max_value": 0.95,
                    "step": 0.05,
                    "help": (
                        "Hard prompt budget is the lower of the provider-window "
                        "percentage and the 272K cost ceiling."
                    ),
                },
                {
                    "type": "slider",
                    "label": "Token Threshold (K) Override",
                    "config_path": "plugins.context_compaction.token_threshold_k",
                    "min_value": 0,
                    "max_value": 2000,
                    "step": 10,
                    "help": (
                        "Manual threshold can lower the budget but cannot exceed "
                        "the provider percentage or 272K ceiling."
                    ),
                },
                {
                    "type": "slider",
                    "label": "Min Human Turns",
                    "config_path": "plugins.context_compaction.min_human_turns",
                    "min_value": 1,
                    "max_value": 20,
                    "step": 1,
                    "help": (
                        "Human messages before compaction can fire. "
                        "Over the threshold, older messages to summarize are enough "
                        "when the provider reports token usage."
                    ),
                },
                {
                    "type": "slider",
                    "label": "Keep Recent",
                    "config_path": "plugins.context_compaction.keep_recent",
                    "min_value": 1,
                    "max_value": 30,
                    "step": 1,
                    "help": (
                        "Recent messages kept verbatim after compaction (more on "
                        "1M-token windows). A round keeps fewer, down to 2, when they "
                        "would fill more than a quarter of the compaction threshold."
                    ),
                },
                {
                    "type": "slider",
                    "label": "Max Summary Tokens",
                    "config_path": "plugins.context_compaction.max_summary_tokens",
                    "min_value": 500,
                    "max_value": 8000,
                    "step": 500,
                    "help": "Maximum tokens for compaction summary",
                },
                {
                    "type": "checkbox",
                    "label": "Log Compaction Events",
                    "config_path": "plugins.context_compaction.log_compaction_events",
                    "help": "Log compaction events to conversation history",
                },
                {
                    "type": "checkbox",
                    "label": "Ask Model First",
                    "config_path": "plugins.context_compaction.ask_model_first",
                    "help": "Ask the model what to keep before compacting; it starts compaction with <compact>",
                },
                {
                    "type": "text_input",
                    "label": "Summarization Model",
                    "config_path": "plugins.context_compaction.summarization_model",
                    "placeholder": "(fast model or session model)",
                    "help": (
                        "Override compaction model; default order is plugin "
                        "override, shared fast model, then session model."
                    ),
                },
                {
                    "type": "slider",
                    "label": "Session Turn Warning Budget",
                    "config_path": "plugins.context_compaction.max_session_turns",
                    "min_value": 0,
                    "max_value": 300,
                    "step": 10,
                    "help": "Warn once when this session reaches the budget; 0 disables the warning.",
                },
                {
                    "type": "slider",
                    "label": "Hard-Cap Compaction Attempts",
                    "config_path": "plugins.context_compaction.hard_cap_compaction_attempts",
                    "min_value": 1,
                    "max_value": 3,
                    "step": 1,
                    "help": "Forced retries before the deterministic recovery-summary rollover.",
                },
            ],
        }

    def __init__(self, name: str, event_bus, renderer, config) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config

        # State
        self._compaction_round: int = 0

        self._compaction_status: str = "idle"
        self._compaction_status_message: str = ""
        self._compaction_reason: str = "threshold"
        self._compaction_in_progress: bool = False
        self._pending_compaction: Optional[List[ConversationMessage]] = None
        self._pre_compaction_len: int = 0
        self._pending_session_id: Optional[str] = None
        self._consecutive_failures: int = 0
        self._disabled_for_session: bool = False
        self._compaction_task: Optional[asyncio.Task] = None
        self._compaction_tasks: set[asyncio.Task] = set()
        # "Can't shrink" stop (see _should_compact): the swap sets just_applied, the
        # next reported prompt is the compacted one, shrink_floor holds it when it
        # was still over the threshold, tokens_at_trigger is what fired the round.
        self._compaction_just_applied: bool = False
        self._shrink_floor: Optional[int] = None
        self._tokens_at_trigger: int = 0
        self._coordination_deferrals: int = 0

        # Model-first compaction: the model is asked what to keep and starts
        # compaction itself with <compact>notes</compact>.
        self._awaiting_model: bool = False
        self._asked_turn: int = 0
        self._turn_count: int = 0

        self._session_turn_budget_warned: bool = False
        self._session_budget_session_id: Optional[str] = None
        self._session_started_monotonic: float = time.monotonic()

        self._session_started_at_epoch: float = time.time()
        self._last_hard_cap_guard_turn: Optional[int] = None
        self._last_hard_cap_guard_signature: Optional[tuple] = None
        self._token_usage_stale_after_compaction: bool = False
        self._token_estimate_scale: Optional[float] = None
        self._last_summarization_model: Optional[str] = None
        self._last_compaction_duration_seconds: Optional[float] = None
        self._model_notes: Optional[str] = None
        self._watch_task: Optional[asyncio.Task] = None

        # References set during initialize()
        self._llm_service = None
        self._conversation_logger = None
        self._conversation_manager = None
        self._profile_manager = None
        self._agent_manager = None
        self._command_registry = None

        logger.info(f"ContextCompactionPlugin initialized: {name}")

    async def initialize(
        self,
        args=None,
        event_bus=None,
        config=None,
        command_registry=None,

        renderer=None,
        llm_service=None,
        conversation_logger=None,
        conversation_manager=None,
        **kwargs,
    ) -> None:
        if event_bus:
            self.event_bus = event_bus
        if config:
            self.config = config
        if renderer:
            self.renderer = renderer
        if command_registry:
            self._command_registry = command_registry

        self._llm_service = llm_service
        self._conversation_logger = conversation_logger
        self._conversation_manager = conversation_manager

        if llm_service and hasattr(llm_service, "profile_manager"):
            self._profile_manager = llm_service.profile_manager

        self._agent_manager = kwargs.get("agent_manager")
        if self._agent_manager is None and self.event_bus:
            try:
                self._agent_manager = self.event_bus.get_service("agent_manager")
            except Exception:
                self._agent_manager = None
        self._session_budget_session_id = None
        self._sync_session_budget()

        # Register /compact command and the model's <compact> tag
        self._register_compact_command()
        self._register_compact_tag()

        # Register status widget if widget_api available
        self._register_status_widget()

        logger.info("Context compaction plugin initialized")

    async def register_hooks(self) -> None:
        if not self.config.get("plugins.context_compaction.enabled", True):
            logger.info("Context compaction disabled, skipping hook registration")
            return

        post_hook = Hook(
            name="context_compaction_post",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_POST,
            priority=HookPriority.POSTPROCESSING.value,
            callback=self._on_llm_turn_complete,
        )
        await self.event_bus.register_hook(post_hook)


        error_hook = Hook(
            name="context_compaction_request_error",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_ERROR,
            priority=HookPriority.POSTPROCESSING.value,
            callback=self._on_llm_request_error,
        )
        await self.event_bus.register_hook(error_hook)

        pre_hook = Hook(
            name="context_compaction_pre",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_PRE,
            priority=HookPriority.PREPROCESSING.value,
            callback=self._on_llm_request_pre,
        )
        await self.event_bus.register_hook(pre_hook)

        tool_boundary_hook = Hook(
            name="context_compaction_tool_boundary",
            plugin_name=self.name,
            event_type=EventType.TOOL_CALL_PRE,
            priority=HookPriority.PREPROCESSING.value,
            callback=self._on_tool_call_pre,
        )
        await self.event_bus.register_hook(tool_boundary_hook)

        logger.info("Context compaction hooks registered")

    async def shutdown(self) -> None:
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
        tasks = tuple(self._compaction_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._compaction_tasks.clear()
        self._compaction_task = None
        self._pending_compaction = None
        self._pending_session_id = None
        logger.info("Context compaction plugin shutdown")

    # ------------------------------------------------------------------
    # Status widget
    # ------------------------------------------------------------------

    def _set_compaction_status(
        self, status: str, message: str, action: str = ""
    ) -> None:
        """Publish persistent compaction state and retain its next action."""
        self._compaction_status = status
        self._compaction_status_message = message
        self._compaction_status_action = action
        try:
            publish_semantic(
                self.renderer,
                "context_compaction_status",
                status=status,
                message=message,
                action=action,
            )
        except Exception:
            # Status display is best-effort and must not break compaction.
            logger.debug("Failed to publish compaction status", exc_info=True)

        should_alert = status in {"failed", "paused", "warning", "rolled_over"} or (
            status == "compacting" and "context budget reached" in message.lower()
        )
        if should_alert:
            self._display_tui_compaction_failure(message, action)

    def _display_tui_compaction_failure(self, message: str, action: str) -> None:
        """Show failed compaction and its recovery action in the TUI."""
        renderer = getattr(self, "renderer", None)
        if renderer is None or getattr(renderer, "pipe_mode", False) is True:
            return

        coordinator = getattr(renderer, "message_coordinator", None)
        display = getattr(coordinator, "display_message_sequence", None)
        if not callable(display):
            return

        content = message.strip()
        if action.strip() and action.strip() not in content:
            content = f"{content}\nNext step: {action.strip()}"
        try:
            display([("system", content, {"display_type": "warning"})])
        except Exception:
            logger.warning(
                "Failed to display compaction recovery action in TUI",
                exc_info=True,
            )


    def _register_status_widget(self) -> None:
        """Register a status widget via the widget_api service."""
        if not self.event_bus:
            return
        widget_api = self.event_bus.get_service("widget_api")
        if not widget_api:
            logger.debug("widget_api service not available, skipping widget")
            return
        try:
            widget_api.register_widget(
                id="context-compaction",
                name="Context",
                description="Context compaction status",
                render_fn=self._render_widget,
                default_width="auto",
                min_width=8,
            )
            logger.info("Registered context-compaction status widget")

            # Ensure widget is in the layout (row 1) if not already placed
            layout_mgr = self.event_bus.get_service("layout_manager")
            if layout_mgr:
                layout = layout_mgr.get_layout()
                already_placed = any(
                    w.id == "context-compaction"
                    for row in layout.rows
                    for w in row.widgets
                )
                if not already_placed:
                    layout_mgr.add_widget_to_row(1, "context-compaction")
                    logger.info("Added context-compaction widget to row 1")

        except Exception as e:
            logger.warning(f"Failed to register status widget: {e}")

    def _render_widget(self, width: int, context) -> str:
        """Render the context compaction status widget."""
        prompt_tokens = 0
        tokens_estimated = False
        remote_state = (
            getattr(context, "remote_state", None) if context is not None else None
        ) or {}

        # In attach mode the daemon is authoritative even when the local
        # shadow service has stale/nonzero counters. During an in-flight turn,
        # use its explicit estimate until the provider reports final usage.
        if remote_state and any(
            key in remote_state
            for key in ("input_tokens", "current_processing_tokens")
        ):
            prompt_tokens = int(remote_state.get("input_tokens", 0) or 0)
            tokens_estimated = bool(remote_state.get("input_tokens_estimated", False))
            if prompt_tokens == 0 and remote_state.get("is_processing"):
                prompt_tokens = int(
                    remote_state.get("current_processing_tokens", 0) or 0
                )
                tokens_estimated = prompt_tokens > 0
        else:
            prompt_tokens = self._effective_prompt_tokens()
            tokens_estimated = (
                self._prompt_tokens_are_estimated()
                or self._token_usage_stale_after_compaction
            )
        threshold = self._get_token_threshold()
        token_prefix = "~" if tokens_estimated and prompt_tokens > 0 else ""
        if not prompt_tokens:
            token_k = "0"
        elif prompt_tokens < 1000:
            token_k = f"{token_prefix}{prompt_tokens}"
        else:
            token_k = f"{token_prefix}{prompt_tokens / 1000:.0f}K"
        thresh_k = f"{threshold / 1000:.0f}K"

        status = getattr(self, "_compaction_status", "idle")
        status_message = getattr(self, "_compaction_status_message", "")
        budget_status = status in {"paused", "failed", "warning", "rolled_over"} or (
            status == "compacting" and "context budget reached" in status_message.lower()
        )
        if status in {"paused", "failed", "rolled_over"} or (
            status == "compacting" and "context budget reached" in status_message.lower()
        ):
            threshold = self._get_hard_cap_threshold()
            thresh_k = f"{threshold / 1000:.0f}K"
        if budget_status:
            limit = self._get_session_turn_budget()
            turn_label = (
                f" {self._turn_count}/{limit}t"
                if limit > 0 and self._turn_count >= limit
                else ""
            )
            agent, _hub_identity = self._get_agent_labels()
            agent_label = f" {agent}" if agent not in {"interactive", "unknown"} else ""
            rendered = f"ctx: {status} {token_k}/{thresh_k}{turn_label}{agent_label}"
            return rendered[:width] if width > 0 else rendered
        if self._disabled_for_session:
            return "ctx: off"
        if self._compaction_in_progress:
            return "ctx: compacting..."
        if status == "ready_to_retry":
            return "ctx: retry ready"
        if self._awaiting_model:
            return "ctx: asking model..."
        if self._compaction_round > 0:
            return f"ctx: r{self._compaction_round} {token_k}/{thresh_k}"

        return f"ctx: {token_k}/{thresh_k}"

    # ------------------------------------------------------------------
    # /compact command
    # ------------------------------------------------------------------

    def _register_compact_command(self) -> None:
        """Register the /compact slash command."""
        if not hasattr(self, "_command_registry") or not self._command_registry:
            return

        cmd = CommandDefinition(
            name="compact",
            description="Ask the model what to keep, then compact",
            plugin_name=self.name,
            category=CommandCategory.SYSTEM,
            handler=self._handle_compact_command,
            subcommands=[
                SubcommandInfo("now", "", "Compact immediately, without asking the model"),
                SubcommandInfo(
                    "status",
                    "",
                    "Show compaction profile, pause state, and next step",
                ),
                SubcommandInfo("preview", "", "Show what a compaction would remove"),
            ],
        )
        self._command_registry.register_command(cmd)

    async def _handle_compact_command(self, command) -> str:
        """Handle /compact.

        /compact          ask the model what to keep, it compacts when ready
        /compact now      compact immediately, no question asked
        /compact status   show the compaction profile
        /compact preview  show what a compaction would remove
        """
        args = getattr(command, "args", []) or []
        sub = str(args[0]).lower() if args else ""

        if sub in ("", "now"):
            remote = self._remote_state()
            if remote is not None:
                # Attach client: history, ledger and the LLM loop live in the
                # daemon, so the request has to run there.
                try:
                    return await remote.compact_command(sub)
                except Exception as e:
                    return f"daemon compact failed: {e}"
            return await self._start_manual_compaction(ask=(sub == ""))

        # Prefer daemon state so /compact reflects the real conversation in
        # attach mode -- the local llm_service is only a client-side shadow
        # with an empty history, which made every number read as zero. Falls
        # back to local state in standalone mode.
        history = await self._fetch_display_history()
        prompt_tokens = await self._fetch_display_tokens()

        if args and str(args[0]).lower() == "preview":
            return self._build_compact_preview(history, prompt_tokens)

        # Resolve provider info
        model = "?"
        provider = "?"
        ctx_window = None
        if self._profile_manager:
            try:
                active = self._profile_manager.get_active_profile()
                if active:
                    model = active.get_model() or "?"
                    provider = active.get_provider() or "?"
            except Exception:
                pass
            ctx_window = self._resolve_context_window()

        # Threshold source
        token_k_override = int(
            self.config.get("plugins.context_compaction.token_threshold_k", 0)
        )
        threshold = self._get_token_threshold()
        if token_k_override > 0:
            source = f"manual ({token_k_override}K)"
        elif ctx_window:
            ratio = float(
                self.config.get(
                    "plugins.context_compaction.compaction_ratio", 0.75
                )
            )
            source = f"auto ({ratio:.0%} of {ctx_window // 1000}K)"
        else:
            source = "fallback (no provider detected)"

        # Current state (prompt_tokens fetched above, daemon-preferred)
        keep_recent_cfg = self.config.get(
            "plugins.context_compaction.keep_recent", 8
        )
        if ctx_window:
            keep_actual = max(keep_recent_cfg, ctx_window // 100_000)
        else:
            keep_actual = keep_recent_cfg
        min_turns = int(
            self.config.get("plugins.context_compaction.min_human_turns", 6)
        )

        # History stats (history fetched above, daemon-preferred)
        msg_count = len(history) if history else 0
        human_turns = 0
        if history:
            human_turns = sum(
                1
                for m in history
                if m.role == "user" and not m.metadata.get("hub_message")
            )

        pct = (
            f" ({prompt_tokens * 100 // threshold}%)"
            if threshold > 0
            else ""
        )
        keep_str = (
            f"{keep_actual} (scaled from {keep_recent_cfg})"
            if keep_actual != keep_recent_cfg
            else str(keep_actual)
        )
        ctx_str = (
            f"{ctx_window // 1000}K" if ctx_window else "unknown"
        )

        lines = [
            "compaction profile:",
            f"  provider:       {provider}",
            f"  model:          {model}",
            f"  context window: {ctx_str}",
            f"  auto trigger:   {threshold // 1000}K [{source}]",
            f"  hard cap:       {self._get_hard_cap_threshold() // 1000}K",
            f"  prompt tokens:  {prompt_tokens // 1000}K{pct}",
            "",
            f"  keep recent:    {keep_str}",
            f"  min turns:      {min_turns}",
            f"  round:          {self._compaction_round}",
            "",
            f"  messages:       {msg_count}",
            f"  human turns:    {human_turns}",
            f"  in progress:    {'yes' if self._compaction_in_progress else 'no'}",
            f"  disabled:       {'yes' if self._disabled_for_session else 'no'}",
        ]
        status = getattr(self, "_compaction_status", "idle")
        if status != "idle":
            lines.extend(
                [
                    "",
                    f"  state:          {status}",
                    f"  status:         {getattr(self, '_compaction_status_message', '')}",
                ]
            )
            action = getattr(self, "_compaction_status_action", "")
            if action:
                lines.append(f"  next step:      {action}")

        return "\n".join(lines)

    def _build_compact_preview(
        self,
        history: Optional[List[ConversationMessage]] = None,
        prompt_tokens: Optional[int] = None,
    ) -> str:
        """Build a non-mutating preview of what compaction would affect.

        ``history`` and ``prompt_tokens`` are supplied by the command handler
        (daemon-sourced in attach mode). When omitted they fall back to local
        state so the method still works standalone.
        """
        if history is None:
            history = self._get_conversation_history() or []
        keep_recent = int(
            self.config.get("plugins.context_compaction.keep_recent", 8)
        )
        split = self._find_split_point(history, keep_recent)
        remove_candidates = history[:split]
        to_keep = history[split:]
        preserved, summarizable = self._extract_preservable_messages(
            remove_candidates
        )

        if prompt_tokens is None:
            prompt_tokens = self._get_prompt_tokens()
        estimated_removed = int(
            prompt_tokens * (len(summarizable) / max(1, len(history)))
        )

        return "\n".join(
            [
                "compact preview:",
                f"  messages:       {len(history)}",
                f"  preserved:      {len(to_keep)} recent",
                f"  removed:        {len(summarizable)} summarizable",
                f"  pinned:         {len(preserved)} hub/task",
                f"  token delta:    ~{estimated_removed // 1000}K removed",
            ]
        )

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _get_prompt_tokens(self) -> int:
        """Get prompt token count from the last API call.

        Reads from session_stats (set by queue_processor after each call),
        which works for all providers including those that don't report
        usage in streaming chunks. Falls back to api_service.last_token_usage.
        """
        if not self._llm_service:
            return 0

        # Primary: session_stats.input_tokens (always set by queue_processor)
        session_stats = getattr(self._llm_service, "session_stats", None)
        if session_stats:
            input_tokens = session_stats.get("input_tokens", 0)
            if isinstance(input_tokens, (int, float)) and input_tokens > 0:
                return int(input_tokens)

        # Fallback: api_service.last_token_usage
        api_service = getattr(self._llm_service, "api_service", None)
        if api_service:
            usage = api_service.get_last_token_usage()
            if usage:
                prompt_tokens = usage.get("prompt_tokens", 0)
                if isinstance(prompt_tokens, (int, float)):
                    return int(prompt_tokens)

        return 0

    def _prompt_tokens_are_estimated(self) -> bool:
        """Return whether the displayed prompt count is a local estimate."""
        if not self._llm_service:
            return False

        session_stats = getattr(self._llm_service, "session_stats", None) or {}
        if "input_tokens_estimated" in session_stats:
            return bool(session_stats.get("input_tokens_estimated"))

        api_service = getattr(self._llm_service, "api_service", None)
        return bool(
            getattr(api_service, "last_token_usage_is_estimated", False) is True
        )

    def _estimate_history_tokens(
        self, history: List[ConversationMessage]
    ) -> int:
        """Estimate tokens from message text (~3 chars/token) when no usage was reported.

        Fallback only. It counts content chars (not tool schemas or tool-call
        args) and overshot the real count 2.3x on 2026-09-30 (101K estimated vs
        44K reported), so never compare it to the threshold when an
        API-reported count exists.
        """
        total_chars = 0
        for msg in history:
            content = getattr(msg, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
            total_chars += len(content)
        return total_chars // 3

    def _resolve_context_window(self) -> Optional[int]:
        """Resolve the context window size from the active model/provider.

        Prefers the live provider config (which carries the model's window with
        any per-profile override), then the model registry
        (bundles/data/models.json, longest-prefix match), then
        provider_defaults. Returns None only if none resolve.
        """
        # The live provider config is authoritative — it holds the window the
        # request is actually bounded by, so a model that isn't in the static
        # registry resolves correctly instead of falling back to a too-small
        # default and never triggering compaction.
        try:
            api_service = getattr(self._llm_service, "api_service", None)
            cfg = getattr(getattr(api_service, "_provider", None), "config", None)
            raw_window = getattr(cfg, "context_window", None)
            if isinstance(raw_window, bool):
                window = None
            elif isinstance(raw_window, (int, float)):
                try:
                    numeric_window = float(raw_window)
                    window = (
                        int(numeric_window)
                        if math.isfinite(numeric_window) and numeric_window > 0
                        else None
                    )
                except (TypeError, ValueError, OverflowError):
                    window = None
            elif isinstance(raw_window, str):
                try:
                    parsed_window = int(raw_window)
                    window = parsed_window if parsed_window > 0 else None
                except (TypeError, ValueError, OverflowError):
                    window = None
            else:
                window = None
            if window is not None:
                return window
        except Exception:
            pass

        if not self._profile_manager:
            return None

        try:
            active = self._profile_manager.get_active_profile()
            if not active:
                return None
        except Exception:
            return None

        model = (active.get_model() or "").lower()
        provider = (active.get_provider() or "").lower()

        models = _MODEL_REGISTRY.get("models", {})
        provider_defaults = _MODEL_REGISTRY.get("provider_defaults", {})

        # Prefix match against registry (longest prefix first)
        if model:
            best_window = None
            best_len = 0
            for name, info in models.items():
                if model.startswith(name) and len(name) > best_len:
                    ctx = info.get("context_window")
                    if ctx:
                        best_window = ctx
                        best_len = len(name)
            if best_window is not None:
                return best_window

        # Fall back to provider default
        if provider and provider in provider_defaults:
            return provider_defaults[provider].get("context_window")

        return None

    def _get_agent_labels(self) -> tuple[str, str]:
        """Return the selected agent and its hub identity for diagnostics."""
        agent_name = None
        manager = getattr(self, "_agent_manager", None)
        event_bus = getattr(self, "event_bus", None)
        if manager is None and event_bus:
            try:
                manager = event_bus.get_service("agent_manager")
            except Exception:
                manager = None

        if manager is not None:
            for attr in ("active_agent_name", "current_agent_name", "selected_agent"):
                value = getattr(manager, attr, None)
                if callable(value):
                    try:
                        value = value()
                    except Exception:
                        value = None
                if isinstance(value, str) and value.strip():
                    agent_name = value.strip()
                    break
                name = getattr(value, "name", None)
                if isinstance(name, str) and name.strip():
                    agent_name = name.strip()
                    break

        hub_identity = None
        if event_bus:
            try:
                hub_plugin = event_bus.get_service("hub_plugin")
                identity = getattr(hub_plugin, "_identity", None)
                hub_identity = getattr(identity, "identity", None)
            except Exception:
                hub_identity = None

        return agent_name or "interactive", hub_identity or "unknown"

    def _get_session_turn_budget(self) -> int:
        """Return a validated per-session warning budget; zero disables it."""
        raw_limit = self.config.get(
            "plugins.context_compaction.max_session_turns", 0
        )
        try:
            return max(0, int(raw_limit))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _get_hard_cap_compaction_attempts(self) -> int:
        """Return the bounded number of forced compaction attempts."""
        raw_attempts = self.config.get(
            "plugins.context_compaction.hard_cap_compaction_attempts", 2
        )
        try:
            attempts = int(raw_attempts)
        except (TypeError, ValueError, OverflowError):
            attempts = 2
        return max(1, min(attempts, 3))


    def _history_signature(self, history: Optional[List[ConversationMessage]] = None) -> tuple:
        """Stable, cheap marker used to avoid redundant compaction in one turn."""
        if history is None:
            history = self._get_conversation_history() or []
        total_chars = 0
        for message in history:
            content = getattr(message, "content", "") or ""
            total_chars += len(content if isinstance(content, str) else str(content))
        return (len(history), total_chars)

    def _estimated_current_prompt_tokens(
        self, history: Optional[List[ConversationMessage]] = None
    ) -> int:
        """Estimate current history after a compaction made provider usage stale."""
        if history is None:
            history = self._get_conversation_history() or []
        estimate = self._estimate_history_tokens(history)
        if self._token_estimate_scale is not None:
            estimate = int(estimate * self._token_estimate_scale)
        return max(0, estimate)

    def _effective_prompt_tokens(self) -> int:
        """Use the last provider count unless compaction changed the history."""
        if self._token_usage_stale_after_compaction:
            return self._estimated_current_prompt_tokens()
        reported = self._get_prompt_tokens()
        if reported > 0:
            return reported
        return self._estimated_current_prompt_tokens()

    def _outgoing_prompt_tokens(
        self, history: Optional[List[ConversationMessage]] = None
    ) -> int:
        """What the next request would actually carry.

        The provider count covers only the history already sent; tool results
        and injected hub messages append afterwards, so the outgoing prompt is
        at least the calibrated estimate of the current history (issue #279).
        """
        return max(
            self._effective_prompt_tokens(),
            self._estimated_current_prompt_tokens(history),
        )

    def _record_prompt_token_scale(self) -> None:
        """Calibrate the history estimate against the latest successful request."""
        reported = self._get_prompt_tokens()
        history = self._get_conversation_history() or []
        estimate = self._estimate_history_tokens(history)
        if reported > 0 and estimate > 0:
            # The estimator intentionally errs high. A bounded correction makes
            # post-compaction checks useful without treating it as provider data.
            self._token_estimate_scale = max(0.10, min(1.50, reported / estimate))
        self._token_usage_stale_after_compaction = False

    def _persist_session_budget_state(self) -> None:
        """Persist the turn/age budget with the rest of the session stats."""
        stats = getattr(self._llm_service, "session_stats", None)
        if not isinstance(stats, dict) or not self._session_budget_session_id:
            return
        stats["context_compaction_session_id"] = self._session_budget_session_id
        stats["context_compaction_turn_count"] = self._turn_count
        stats["context_compaction_started_at"] = self._session_started_at_epoch

    def _sync_session_budget(self) -> None:
        """Restore session budgets or reset them when the conversation changes."""
        session_id = self._get_current_session_id()
        if not session_id or session_id == self._session_budget_session_id:
            return

        stats = getattr(self._llm_service, "session_stats", None)
        restored = isinstance(stats, dict) and (
            stats.get("context_compaction_session_id") == session_id
        )
        if restored:
            try:
                self._turn_count = max(
                    0, int(stats.get("context_compaction_turn_count", 0))
                )
            except (TypeError, ValueError):
                self._turn_count = 0
            try:
                started_at = float(stats.get("context_compaction_started_at", 0.0))
            except (TypeError, ValueError):
                started_at = 0.0
            if started_at > 0:
                self._session_started_at_epoch = started_at
                elapsed = max(0.0, time.time() - started_at)
                self._session_started_monotonic = time.monotonic() - elapsed
            else:
                self._session_started_at_epoch = time.time()
                self._session_started_monotonic = time.monotonic()
        else:
            self._turn_count = 0
            self._session_started_at_epoch = time.time()
            self._session_started_monotonic = time.monotonic()

        self._session_budget_session_id = session_id
        self._compaction_round = 0
        self._consecutive_failures = 0
        self._disabled_for_session = False
        self._shrink_floor = None
        self._compaction_just_applied = False
        self._token_usage_stale_after_compaction = False
        self._token_estimate_scale = None
        self._last_hard_cap_guard_turn = None
        self._last_hard_cap_guard_signature = None
        self._last_summarization_model = None
        self._last_compaction_duration_seconds = None
        self._set_compaction_status("idle", "")
        self._session_turn_budget_warned = False
        self._persist_session_budget_state()

    def _warn_session_turn_budget(self) -> None:
        """Log and visibly warn once when a session exceeds its configured turn budget."""
        limit = self._get_session_turn_budget()
        if limit <= 0 or self._turn_count < limit or self._session_turn_budget_warned:
            return
        self._session_turn_budget_warned = True
        agent, hub_identity = self._get_agent_labels()
        age_minutes = int((time.monotonic() - self._session_started_monotonic) / 60)
        message = (
            f"Long session budget reached: {self._turn_count}/{limit} turns, "
            f"{age_minutes} minutes old. Compact or start a new session soon."
        )
        logger.warning(
            "[context_compaction][agent=%s][hub=%s] %s",
            agent,
            hub_identity,
            message,
        )
        self._set_compaction_status(
            "warning",
            message,
            action="Run /compact now or start a new session.",
        )


    def _get_context_window_tokens(self) -> int:
        """Return a validated context-window size, or zero when unknown."""
        raw_context_window = self._resolve_context_window()
        try:
            if isinstance(raw_context_window, bool):
                return 0
            if isinstance(raw_context_window, (int, float)):
                if isinstance(raw_context_window, float) and not math.isfinite(
                    raw_context_window
                ):
                    return 0
                context_window = int(raw_context_window)
            elif isinstance(raw_context_window, str):
                context_window = int(raw_context_window)
            else:
                return 0
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(0, context_window)

    def _get_hard_cap_threshold(self) -> int:
        """Return the absolute boundary cap, distinct from auto-compaction."""
        context_window = self._get_context_window_tokens()
        if context_window <= 0:
            return min(AUTO_THRESHOLD_CAP, 100_000)
        return min(context_window, AUTO_THRESHOLD_CAP)

    def _get_token_threshold(self) -> int:
        """Return the lower automatic trigger from ratio, manual value, and cap.

        A manual token threshold can lower the automatic trigger, but cannot
        override the model safety percentage or 272K ceiling. The hard boundary
        guard uses ``_get_hard_cap_threshold`` independently. Unknown model
        windows retain the conservative 100K automatic fallback.
        """
        context_window = self._get_context_window_tokens()
        raw_ratio = self.config.get(
            "plugins.context_compaction.compaction_ratio", 0.75
        )
        try:
            ratio = float(raw_ratio)
        except (TypeError, ValueError, OverflowError):
            ratio = 0.75
        if not math.isfinite(ratio):
            ratio = 0.75
        ratio = max(0.50, min(0.95, ratio))
        provider_cap = (
            int(context_window * ratio) if context_window > 0 else 100_000
        )
        safe_cap = min(provider_cap, AUTO_THRESHOLD_CAP)

        raw_token_k = self.config.get(
            "plugins.context_compaction.token_threshold_k", 0
        )
        try:
            token_k = int(raw_token_k)
        except (TypeError, ValueError, OverflowError):
            token_k = 0
        if token_k > 0:
            return min(token_k * 1000, safe_cap)
        return safe_cap

    def _should_compact(self, history: List[ConversationMessage]) -> bool:
        """Determine if compaction should trigger.

        Uses a dual-gate approach:
            1. Token gate: prompt_tokens exceeds token_threshold_k
            2. Turn gate: at least min_human_turns have occurred, or a reported count has older messages to summarize

        Both gates must pass. This prevents compaction from firing on
        a 2-message conversation with a huge system prompt, and also
        prevents token-blind compaction from agent chatter.
        """
        # Gate 1: token count. Runs at LLM_REQUEST_POST, so the API-reported
        # count is for the history that was just sent: exact, and the only
        # number compaction can move. The chars//3 estimate is the fallback for
        # a request that reported no usage. Never max() them: the estimate runs
        # ~2.3x high and counts the system prompt and the verbatim-preserved
        # tasks/HUD that compaction cannot remove, so it stayed above the
        # threshold after every compaction and re-fired it every turn.
        prompt_tokens = self._get_prompt_tokens()
        estimated = prompt_tokens <= 0
        if estimated:
            prompt_tokens = self._estimate_history_tokens(history)
        token_threshold = self._get_token_threshold()
        if prompt_tokens < token_threshold:
            if self._compaction_status == "paused":
                self._set_compaction_status("idle", "")
            self._compaction_just_applied = False
            self._shrink_floor = None
            return False

        # Gate 1b: can't-shrink stop. The first count reported after a compaction
        # is the compacted prompt: system prompt + summary + pinned tasks/HUD + the
        # kept window. Still over the threshold means another round rebuilds the
        # same prompt and re-fires every turn, so pause until the prompt grows by a
        # tenth of the threshold, then try again.
        if self._compaction_just_applied:
            if prompt_tokens == self._tokens_at_trigger:
                return False  # no fresh usage since the swap, so no verdict yet
            self._compaction_just_applied = False
            self._shrink_floor = prompt_tokens
            self._warn_cannot_shrink(prompt_tokens, token_threshold)
            return False
        if self._shrink_floor is not None:
            if prompt_tokens < self._shrink_floor + token_threshold // 10:
                return False
            self._shrink_floor = None

        # Gate 2: minimum human turns (safety floor)
        min_turns = int(
            self.config.get("plugins.context_compaction.min_human_turns", 6)
        )
        human_turns = sum(
            1
            for msg in history
            if msg.role == "user" and not msg.metadata.get("hub_message")
        )
        # A reported count over the threshold compacts once older messages can
        # be summarized, even before min_human_turns: a hub agent whose work
        # comes in one brief has few. The estimate runs ~2.3x high, so it waits.
        if human_turns < min_turns and (
            estimated
            or self._find_split_point(history, self._keep_recent_count(history)) < 2
        ):
            return False

        # Determine threshold source for logging
        token_k_override = int(
            self.config.get("plugins.context_compaction.token_threshold_k", 0)
        )
        if token_k_override > 0:
            source = "manual"
        elif self._resolve_context_window():
            source = "auto"
        else:
            source = "fallback"

        logger.info(
            f"Compaction triggered: {prompt_tokens} tokens"
            f"{' (estimated)' if estimated else ''} >= {token_threshold} "
            f"[{source}] ({human_turns} human turns, min {min_turns}) "
            f"(round {self._compaction_round})"
        )
        self._tokens_at_trigger = prompt_tokens
        return True

    def _warn_cannot_shrink(self, prompt_tokens: int, threshold: int) -> None:
        """Say, once per episode, that a compaction could not get under the threshold."""
        content = (
            f"Automatic compaction is paused after round {self._compaction_round}: "
            f"the prompt is {prompt_tokens // 1000}K tokens, still above the "
            f"configured {threshold // 1000}K auto-compaction threshold. Background "
            "retries are paused to avoid repeating a no-op summary, but the hard "
            "budget is still enforced at the next tool or request boundary. Kollab "
            "will retry compaction and roll over with a recovery summary if needed. "
            "You can also run /compact now."
        )
        self._set_compaction_status(
            "paused",
            content,
            action="Run /compact now, or retry your turn after compaction is ready.",
        )
        logger.warning(content)

    def _coordination_pending(self) -> bool:
        """Readiness gate: is undelivered coordination state in flight?

        True when either:
          1. The LLM service has queued agent-HUD entries that have not been
             drained into history yet (a mid-turn hub message is sitting in
             the queue — compacting now would summarize it before the model
             ever saw it).
          2. The hub task ledger has pending replies awaiting this agent.

        Compaction is deferred one turn while this is true; it retries on
        the next turn-complete. Data is never lost either way (JSONL and
        the ledger are durable) — this protects coordination STATE from
        being probabilistically summarized out of the model's context.
        """
        svc = self._llm_service
        if svc is not None:
            queued = getattr(svc, "_pending_agent_hud", None)
            # isinstance check guards test doubles: a Mock attribute is not
            # a queue. Real queues are list[AgentHudEntry].
            if isinstance(queued, list) and queued:
                return True
        # Ledger pending replies: agent_hub service pattern mirrors the
        # hub plugin's own HUD injection (plugin.py pending_replies()).
        if self.event_bus is not None:
            try:
                hub_plugin = self.event_bus.get_service("hub_plugin")
                ledger = (
                    getattr(hub_plugin, "_task_ledger", None)
                    if hub_plugin is not None
                    else None
                )
                if (
                    ledger is not None
                    and type(ledger).__module__ != "unittest.mock"
                    and hasattr(ledger, "pending_replies")
                    and ledger.pending_replies()
                ):
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _is_coordination_control(content: str) -> bool:
        """Recognize explicit control headers, not task-tool examples."""
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return False

        first = lines[0].lower()
        if first.startswith("[hub wake instruction]"):
            return True
        for line in lines:
            lowered = line.lower()
            if lowered.startswith("[hub"):
                continue
            return lowered.startswith(
                (
                    "go —",
                    "go:",
                    "shipped:",
                    "delivery report",
                    "board rebuild:",
                    "board rebuild complete",
                )
            )
        return False

    @staticmethod
    def _extract_coordination_lines(
        summarizable: List[ConversationMessage],
    ) -> List[str]:
        """Extract only explicit, bounded hub control blocks.

        Task-shaped blocks and pending-reply HUD dumps remain summarizable;
        they are not copied verbatim back into the compacted history. The
        extracted coordination text has fixed message and character budgets.
        """
        import re

        blocks: List[str] = []
        pattern = re.compile(r"\[hub[^\]]*?\]", re.IGNORECASE)
        for msg in summarizable:
            metadata = getattr(msg, "metadata", None) or {}
            if metadata.get("task_id"):
                continue

            content = str(getattr(msg, "content", "") or "")
            headers = list(pattern.finditer(content))
            for i, match in enumerate(headers):
                start = match.start()
                end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
                block = content[start:end].strip()
                if block and ContextCompactionPlugin._is_coordination_control(block):
                    blocks.append(block)

        selected: List[str] = []
        remaining = MAX_PRESERVED_COORDINATION_CHARS
        for block in reversed(blocks[-MAX_PRESERVED_COORDINATION_MESSAGES:]):
            separator_chars = 2 if selected else 0
            available = remaining - separator_chars
            if available <= 0:
                break
            if len(block) > available:
                suffix = "\n[coordination block truncated for bounded compaction]"
                excerpt = block[: max(0, available - len(suffix))] + suffix
                if excerpt:
                    selected.append(excerpt[:available])
                break
            selected.append(block)
            remaining -= len(block) + separator_chars
        return list(reversed(selected))

    def _compaction_task_done(self, task: asyncio.Task) -> None:
        """Observe and retire a background compaction task."""
        self._compaction_tasks.discard(task)
        if self._compaction_task is task:
            self._compaction_task = None
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            logger.exception("Failed to inspect completed compaction task")

    async def _on_llm_turn_complete(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """LLM_REQUEST_POST: check if compaction threshold reached."""
        self._sync_session_budget()
        self._record_prompt_token_scale()

        self._turn_count += 1

        self._persist_session_budget_state()

        self._warn_session_turn_budget()
        if self._disabled_for_session or self._compaction_in_progress:
            return data

        if not self.config.get("plugins.context_compaction.enabled", True):
            return data

        history = self._get_conversation_history()
        if getattr(self, "_compaction_status", "idle") in {
            "applied",
            "ready_to_retry",
            "failed",
        }:
            self._set_compaction_status("idle", "")


        if not history:
            return data

        # While the model has been asked, the idle watcher owns the fallback;
        # the model normally answers with <compact> first.
        if not self._awaiting_model:
            if not self._should_compact(history):
                self._coordination_deferrals = 0
            elif (
                self._coordination_pending()
                and self._coordination_deferrals < MAX_COORDINATION_DEFERRALS
                and self._outgoing_prompt_tokens(history)
                < self._get_hard_cap_threshold()
            ):
                self._coordination_deferrals += 1
                logger.info(
                    "Compaction deferred (%d/%d): coordination in flight "
                    "(queued HUD hub messages or pending hub replies)",
                    self._coordination_deferrals,
                    MAX_COORDINATION_DEFERRALS,
                )
            else:
                self._coordination_deferrals = 0
                if self.config.get(
                    "plugins.context_compaction.ask_model_first", True
                ):
                    await self._request_model_curation(wake=False, reason="threshold")
                else:
                    self._start_compaction()

        self._maybe_emit_budget_hud(history)

        return data

    async def _on_llm_request_error(self, data: Dict[str, Any], event) -> Dict[str, Any]:
        """Recover from a typed context-window failure without replaying it."""
        if not data.get("context_window_exceeded"):
            return data

        message = (
            "The provider rejected this request because the conversation context is "
            "too large. Kollab will not resend the failed request automatically. "
            "A bounded compaction attempt is starting; retry after it is ready."
        )
        action = "Wait for compaction to finish, then retry the failed turn."

        if self._pending_compaction is not None:
            self._set_compaction_status(
                "ready_to_retry",
                "A compacted context is ready to apply. The failed request was not "
                "retried; retry the turn to apply it.",
                action=action,
            )
            return data
        if self._compaction_in_progress:
            self._set_compaction_status("compacting", message, action=action)
            return data
        if self._disabled_for_session:
            self._set_compaction_status(
                "failed",
                "Automatic compaction is disabled after repeated failures. The failed "
                "request was not retried; use /compact now or reduce conversation history.",
                action="Run /compact now or start a smaller conversation.",
            )
            return data

        history = self._get_conversation_history() or []
        if self._find_split_point(history, self._keep_recent_count(history)) <= 1:
            self._set_compaction_status(
                "failed",
                "The provider rejected this request for context size, but there are "
                "not enough older messages to compact. The request was not retried.",
                action="Reduce the conversation history or start a new conversation.",
            )
            return data

        # A typed overflow is independent of the automatic shrink floor and
        # cooldown: force exactly one compaction run, never the failed request.
        self._shrink_floor = None
        self._compaction_just_applied = False
        self._set_compaction_status("compacting", message, action=action)
        if not self._start_compaction(reason="context_window_exceeded"):
            self._set_compaction_status(
                "failed",
                "The context-overflow compaction could not be started. The failed "
                "request was not retried.",
                action="Run /compact now or reduce conversation history.",
            )
        return data


    # ------------------------------------------------------------------
    # Model-first compaction
    # ------------------------------------------------------------------

    def _start_compaction(
        self, reason: str = "threshold", *, force: bool = False
    ) -> bool:
        """Spawn one background compaction run. False if one can't start."""
        if self._compaction_in_progress or (
            self._disabled_for_session and not force
        ):
            return False
        self._awaiting_model = False
        self._compaction_in_progress = True
        self._compaction_reason = reason
        if getattr(self, "_compaction_status", "idle") != "compacting":
            message = (
                "Compaction is running synchronously before the next tool/request boundary."
                if force
                else "Compaction is running in the background and will be applied before "
                "the next model request."
            )
            self._set_compaction_status("compacting", message)
        task = asyncio.create_task(self._run_compaction())
        self._compaction_task = task
        self._compaction_tasks.add(task)
        task.add_done_callback(self._compaction_task_done)
        return True

    async def _start_manual_compaction(self, ask: bool) -> str:
        """/compact and /compact now, run where the history lives."""
        if self._disabled_for_session:
            return "compaction is disabled for this session (3 failed runs)"
        if self._compaction_in_progress:
            return "compaction already running"
        history = self._get_conversation_history() or []
        keep = self._keep_recent_count(history)
        if self._find_split_point(history, keep) <= 1:
            return (
                f"nothing to compact yet: {len(history)} messages, "
                f"the last {keep} are always kept"
            )
        if not ask:
            if self._start_compaction():
                return "compacting now, applied before the next request"
            return "compaction could not start"
        if self._awaiting_model:
            return "already asked the model, waiting for its <compact>"
        await self._request_model_curation(wake=True, reason="requested by user")
        return (
            "asked the model what to keep; it compacts when ready "
            "(or when its turn ends)"
        )

    def _keep_recent_count(
        self, history: Optional[List[ConversationMessage]] = None
    ) -> int:
        """Messages always kept intact. Scales with the window (~1 per 100K)
        so 1M-token models don't lose the thread keeping only 8.

        Given the history, fewer are kept (down to 2, or the setting if lower)
        when the messages the cut really keeps would fill more than a quarter
        of the token threshold (#390).
        """
        keep = self.config.get("plugins.context_compaction.keep_recent", 8)
        window = self._resolve_context_window()
        keep = max(keep, window // 100_000) if window else keep
        if not history or keep <= 0:
            return keep
        # ponytail: fixed quarter of the threshold; a setting if users tune it
        budget = self._get_token_threshold() // 4
        reported = self._get_prompt_tokens()
        estimated = self._estimate_history_tokens(history)
        # chars/3 runs high (~2.3x); the provider's count scales it down.
        scale = min(1.0, reported / estimated) if reported > 0 and estimated else 1.0
        floor = min(2, keep)
        kept = 0
        used = 0.0
        for msg in reversed(history[-keep:]):
            cost = self._estimate_history_tokens([msg]) * scale
            if kept >= floor and used + cost > budget:
                break
            kept += 1
            used += cost
        # the cut never parts a tool call from its results (_find_split_point
        # walks back over them), so size what that cut really keeps
        while kept > floor:
            tail = history[self._find_split_point(history, kept) :]
            if self._estimate_history_tokens(tail) * scale <= budget:
                break
            kept -= 1
        return kept

    def _build_curation_request(self, reason: str) -> str:
        """The prompt that asks the model what to keep before compacting."""
        history = self._get_conversation_history() or []
        used = self._get_prompt_tokens() or self._estimate_history_tokens(history)
        lines = [
            "[context compaction: your call]",
            f"Compaction is due ({reason}): ~{used // 1000}K tokens in context, "
            f"threshold {self._get_token_threshold() // 1000}K.",
            "Old messages get replaced by a summary. Decide what you keep first:",
            "",
        ]
        context_svc = self._get_context_service()
        entries = []
        if context_svc is not None:
            try:
                entries = [
                    e
                    for e in context_svc.all_entries()
                    if e.decision in ("pending", "keep", "summary")
                ]
            except Exception:
                entries = []
        if entries:
            lines.append("Heavy items in your context:")
            for e in entries:
                lines.append(
                    f"  {e.ctx_id}  {e.kind:<10} {e.label:<40} "
                    f"{e.size_bytes // 1024:>5}KB  {e.decision}"
                )
            lines += [
                "Mark each one:",
                '  <curate id="ctx-N" decision="keep">why you need it verbatim</curate>',
                '  <curate id="ctx-N" decision="summary">your compressed version</curate>',
                "Unmarked items are auto-summarized.",
                "",
            ]
        lines += [
            "Then write what you must remember (current task, decisions, file "
            "paths, next step) and start compaction in the same reply:",
            "  <compact>your notes, kept verbatim at the top of your context</compact>",
            "Durable knowledge can also go to <scratchpad> or <vault_write>, "
            "both survive compaction.",
        ]
        return "\n".join(lines)

    def _get_context_service(self):
        """The context_service ledger, or None (mock-guarded like the others)."""
        if not self.event_bus:
            return None
        svc = self.event_bus.get_service("context_service")
        if (
            svc is None
            or type(svc).__module__ == "unittest.mock"
            or not hasattr(svc, "all_entries")
        ):
            return None
        return svc

    async def _request_model_curation(self, wake: bool, reason: str) -> None:
        """Ask the model what to keep; it answers with <curate>/<compact>.

        wake=False rides the next request (the agent is mid-chain or the
        user's next message carries it). wake=True starts a turn now when the
        agent is idle, the /compact path.
        """
        prompt = self._build_curation_request(reason)
        self._awaiting_model = True
        self._asked_turn = self._turn_count
        llm = self._llm_service
        busy = bool(getattr(llm, "is_processing", False))
        delivered = False

        if wake and not busy and llm is not None and self.event_bus:
            history = self._get_conversation_history()
            if history is not None:
                history.append(
                    ConversationMessage(
                        role="user",
                        content=prompt,
                        metadata={"compaction_request": True},
                    )
                )
                await self.event_bus.emit_with_hooks(
                    EventType.TRIGGER_LLM_CONTINUE,
                    {"source": "context_compaction", "content": prompt},
                    "context_compaction",
                )
                delivered = True

        if not delivered:
            context_svc = self._get_context_service()
            if context_svc is not None and hasattr(
                context_svc, "queue_ephemeral_injection"
            ):
                context_svc.queue_ephemeral_injection(prompt)
            elif llm is not None and hasattr(llm, "queue_agent_hud"):
                llm.queue_agent_hud(section="context", label="compaction", content=prompt)
            else:
                # Nobody can carry the question: compact without asking.
                logger.info("Compaction: no channel to ask the model, compacting")
                self._start_compaction()
                return

        logger.info(f"Compaction: asked the model what to keep ({reason})")
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.create_task(self._compact_when_idle())

    async def _compact_when_idle(self) -> None:
        """Fallback: the model saw the question and its turn ended without
        <compact>, so compact anyway (pending ledger items get auto-summarized).
        """
        # ponytail: 1s poll of is_processing; an idle event would be exact.
        try:
            while self._awaiting_model:
                answered = self._turn_count > self._asked_turn
                busy = bool(getattr(self._llm_service, "is_processing", False))
                if answered and not busy:
                    logger.info("Compaction: model turn ended without <compact>, compacting")
                    self._start_compaction()
                    return
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise

    def _register_compact_tag(self) -> None:
        """Register <compact>notes</compact> so the model can start compaction."""
        if not self.event_bus:
            return
        response_parser = self.event_bus.get_service("response_parser")
        tool_executor = self.event_bus.get_service("tool_executor")
        if not response_parser or not tool_executor:
            logger.debug("pipeline services not available, skipping <compact> tag")
            return
        import re

        pattern = re.compile(
            r"<compact\s*/>|<compact>(.*?)</compact>", re.DOTALL | re.IGNORECASE
        )

        def _extract(m):
            return {"notes": (m.group(1) or "").strip()}

        response_parser.register_plugin_tag("compact", pattern, "compact", _extract)
        tool_executor.register_plugin_handler("compact", self._handle_compact_tool)

    async def _handle_compact_tool(self, tool_data: Dict[str, Any]):
        """Stage model-requested compaction before continuing the same turn."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        tool_id = tool_data.get("id", "unknown")

        def failure(
            message: str,
            action: str = "",
            *,
            clear_model_notes: bool = False,
        ):
            self._awaiting_model = False
            if clear_model_notes:
                self._model_notes = None
            if action and action not in message:
                message = f"{message} Next step: {action}"
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="compact",
                success=False,
                error=message,
            )

        if self._compaction_in_progress:
            return failure(
                "Compaction is already running; this request was not retried.",
                "Wait for the running compaction to finish.",
            )
        if not self.config.get("plugins.context_compaction.enabled", True):
            return failure(
                "Compaction is disabled by configuration.",
                "Enable the context-compaction plugin, then start a new request.",
                clear_model_notes=True,
            )
        if self._disabled_for_session:
            return failure(
                "Compaction is disabled for this session after repeated failures.",
                "Start a new session after checking the summarizer provider.",
                clear_model_notes=True,
            )

        notes = tool_data.get("notes") or ""
        self._model_notes = notes or None
        pending_before = self._pending_compaction
        if not self._start_compaction():
            return failure(
                self._compaction_status_message
                or "Compaction could not start; no history was changed.",
                self._compaction_status_action
                or "Check the compaction status and run /compact now.",
                clear_model_notes=True,
            )

        compaction_task = self._compaction_task
        if compaction_task is None:
            self._compaction_in_progress = False
            return failure(
                "Compaction did not start its staging task; no history was changed.",
                "Check the compaction status before retrying.",
                clear_model_notes=True,
            )

        try:
            # Do not return an end_turn result here. The queue must issue the
            # next request, whose LLM_REQUEST_PRE hook applies the staged swap.
            # Awaiting this task ensures staging finishes before that request.
            await compaction_task
        except asyncio.CancelledError:
            if self._pending_compaction is pending_before:
                self._model_notes = None
            raise
        except Exception as exc:
            if self._pending_compaction is pending_before:
                logger.exception("Model-first compaction failed before staging")
                return failure(
                    f"Compaction failed and was not applied: {exc}",
                    "Check the log, then run /compact now.",
                    clear_model_notes=True,
                )
            logger.exception(
                "Model-first compaction staged history but failed final bookkeeping"
            )

        if self._pending_compaction is pending_before:
            return failure(
                self._compaction_status_message
                or "Compaction did not stage a replacement history; no history was changed.",
                self._compaction_status_action
                or "Check the compaction status, then run /compact now.",
                clear_model_notes=True,
            )

        return ToolExecutionResult(
            tool_id=tool_id,
            tool_type="compact",
            success=True,
            output=(
                f"[compact] history staged; continuing this request with compacted "
                f"context ({len(notes)} chars of notes kept verbatim)"
            ),
            metadata={},
        )

    def _remote_state(self):
        """The state service when it is an RPC proxy (attach clients)."""
        svc = self._get_state_service()
        if getattr(svc, "_rpc", None) is not None and hasattr(svc, "compact_command"):
            return svc
        return None

    def _maybe_emit_budget_hud(self, history: List[ConversationMessage]) -> None:
        """Periodically show the agent how full its context window is.

        Every N turns, queue a one-line budget readout into the agent HUD so the
        model gets a heads-up before it runs out of room. Uses the real
        API-reported token count when available (estimate only as a fallback),
        and the resolved per-model window.
        """
        every = int(
            self.config.get("plugins.context_compaction.budget_hud_every_n_turns", 5)
        )
        if every <= 0:
            return
        count = getattr(self, "_turns_since_budget_hud", 0) + 1
        if count < every:
            self._turns_since_budget_hud = count
            return
        self._turns_since_budget_hud = 0

        svc = self._llm_service
        if not (svc and hasattr(svc, "queue_agent_hud")):
            return
        window = self._resolve_context_window()
        if not window:
            return

        reported = self._get_prompt_tokens()
        used = reported if reported > 0 else self._estimate_history_tokens(history)
        pct = int(used * 100 / window)
        used_k, window_k = used // 1000, window // 1000
        if pct >= 85:
            body = (
                f"{used_k}K / {window_k}K tokens ({pct}%) — nearly full; "
                "wrap up or /compact soon"
            )
        elif pct >= 70:
            body = (
                f"{used_k}K / {window_k}K tokens ({pct}%) — getting full; "
                "prefer grep over whole-file reads"
            )
        else:
            body = f"{used_k}K / {window_k}K tokens ({pct}%)"

        try:
            svc.queue_agent_hud(section="context", label="budget", content=body)
        except Exception:
            pass

    async def _on_llm_request_pre(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Apply staged history, then enforce the prompt budget before sending."""
        data = data if isinstance(data, dict) else {}
        try:
            result = await self._apply_pending_compaction(data, event)
            if isinstance(result, dict):
                data = result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to apply pending context compaction before request")
        if not self.config.get("plugins.context_compaction.enabled", True):
            return data
        if not await self._enforce_context_budget_at_boundary(
            data, event, boundary="request"
        ):
            return self._context_budget_cancellation(
                data,
                event,
                "Context compaction could not reduce the prompt below the configured "
                "budget before the next model request.",
            )
        return data

    async def _on_tool_call_pre(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """Do not execute another tool while the prompt is above its hard budget."""
        data = data if isinstance(data, dict) else {}
        if not self.config.get("plugins.context_compaction.enabled", True):
            return data
        if not await self._enforce_context_budget_at_boundary(
            data, event, boundary="tool"
        ):
            return self._context_budget_cancellation(
                data,
                event,
                "Context compaction could not reduce the prompt below the configured "
                "budget; the tool call was stopped to avoid an API context overflow.",
            )
        return data

    def _context_budget_cancellation(
        self, data: Dict[str, Any], event, reason: str
    ) -> Dict[str, Any]:
        """Cancel the event and return an actionable decision to its caller."""
        result = dict(data)
        decision = {"allowed": False, "reason": reason}
        result["cancelled"] = True
        result["permission_decision"] = decision
        if event is not None:
            event.cancelled = True
            event_data = getattr(event, "data", None)
            if isinstance(event_data, dict):
                event_data["permission_decision"] = decision
        self._set_compaction_status(
            "failed",
            reason,
            action="Run /compact now or start a new session before continuing.",
        )
        return result

    async def _enforce_context_budget_at_boundary(
        self, data: Dict[str, Any], event, *, boundary: str
    ) -> bool:
        """Wait for compaction or roll over before a tool/API boundary.

        A paused no-shrink floor is only a normal auto-trigger guard. It must
        never authorize sending another oversized prompt after a tool result or
        continuing a long tool chain.
        """
        hard_cap = self._get_hard_cap_threshold()
        history = self._get_conversation_history() or []
        current_tokens = self._outgoing_prompt_tokens(history)
        if current_tokens < hard_cap:
            return True

        signature = self._history_signature(history)
        if (
            boundary == "tool"
            and self._last_hard_cap_guard_turn == self._turn_count
            and self._last_hard_cap_guard_signature == signature
        ):
            return True

        agent, hub_identity = self._get_agent_labels()
        used_k, hard_cap_k = current_tokens // 1000, hard_cap // 1000
        message = (
            f"Context budget reached ({used_k}K/{hard_cap_k}K hard cap); "
            f"compacting before the next {boundary}."
        )
        logger.warning(
            "[context_compaction][agent=%s][hub=%s] hard_cap_boundary=%s "
            "prompt_tokens=%s hard_cap_tokens=%s auto_trigger_tokens=%s "
            "turns=%s status=%s",
            agent,
            hub_identity,
            boundary,
            current_tokens,
            hard_cap,
            self._get_token_threshold(),
            self._turn_count,
            self._compaction_status,
        )
        self._set_compaction_status("compacting", message)

        attempts = self._get_hard_cap_compaction_attempts()
        for attempt in range(1, attempts + 1):
            before_history = self._get_conversation_history() or []
            before_signature = self._history_signature(before_history)

            task = self._compaction_task
            if task is not None and not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                    logger.warning(
                        "[context_compaction][agent=%s] background compaction was cancelled",
                        agent,
                    )
                except Exception:
                    logger.exception(
                        "[context_compaction][agent=%s] background compaction failed at boundary",
                        agent,
                    )

            if self._pending_compaction is not None:
                try:
                    await self._apply_pending_compaction(data, event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "[context_compaction][agent=%s] pending compaction could not be applied",
                        agent,
                    )
            elif not self._compaction_in_progress:
                try:
                    started = self._start_compaction(
                        reason=f"hard_cap_{boundary}_attempt_{attempt}", force=True
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "[context_compaction][agent=%s] forced compaction could not start",
                        agent,
                    )
                    started = False
                if started and self._compaction_task is not None:
                    try:
                        await asyncio.shield(self._compaction_task)
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task is not None and current_task.cancelling():
                            raise
                        logger.warning(
                            "[context_compaction][agent=%s] forced compaction was cancelled",
                            agent,
                        )
                    except Exception:
                        logger.exception(
                            "[context_compaction][agent=%s] forced compaction attempt %s failed",
                            agent,
                            attempt,
                        )
                    if self._pending_compaction is not None:
                        try:
                            await self._apply_pending_compaction(data, event)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.exception(
                                "[context_compaction][agent=%s] forced pending compaction could not be applied",
                                agent,
                            )

            history = self._get_conversation_history() or []
            after_signature = self._history_signature(history)
            if after_signature != before_signature:
                self._token_usage_stale_after_compaction = True
            current_tokens = self._outgoing_prompt_tokens(history)
            if current_tokens < hard_cap:
                self._last_hard_cap_guard_turn = self._turn_count
                self._last_hard_cap_guard_signature = after_signature
                self._set_compaction_status(
                    "applied",
                    f"Context reduced to approximately {current_tokens // 1000}K tokens "
                    f"before the next {boundary}.",
                )
                await self._log_compaction_event(
                    messages_summarized=max(0, len(before_history) - len(history)),
                    messages_kept=len(history),
                    summary_length=0,
                    pre_count=len(before_history),
                    post_count=len(history),
                    reason=f"hard_cap_{boundary}",
                    duration_seconds=self._last_compaction_duration_seconds,
                    summarization_model=self._last_summarization_model,
                )
                return True

            logger.warning(
                "[context_compaction][agent=%s][hub=%s] hard-cap attempt=%s/%s "
                "still_over budget current_tokens=%s hard_cap_tokens=%s",
                agent,
                hub_identity,
                attempt,
                attempts,
                current_tokens,
                hard_cap,
            )

        try:
            rolled_over = await self._rollover_with_summary(
                data, event, boundary=boundary
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "[context_compaction][agent=%s][hub=%s] recovery summary rollover failed",
                agent,
                hub_identity,
            )
            rolled_over = False

        if rolled_over:
            history = self._get_conversation_history() or []
            current_tokens = self._effective_prompt_tokens()
            if current_tokens < hard_cap:
                self._last_hard_cap_guard_turn = self._turn_count
                self._last_hard_cap_guard_signature = self._history_signature(history)
                self._set_compaction_status(
                    "rolled_over",
                    f"Compaction failed; context rolled over with a recovery summary "
                    f"({current_tokens // 1000}K/{hard_cap_k}K).",
                    action="Continue from the recovery summary; the previous transcript remains in session logs.",
                )
                return True

        self._set_compaction_status(
            "failed",
            f"Context remains above the safe budget ({current_tokens // 1000}K/"
            f"{hard_cap_k}K) after compaction and recovery-summary attempts.",
            action="Run /compact now or start a new session before continuing.",
        )
        return False

    async def _rollover_with_summary(
        self, data: Dict[str, Any], event, *, boundary: str
    ) -> bool:
        """Replace the old prefix with a deterministic recovery summary.

        This path is intentionally local and bounded: if the summary model is
        unavailable, a concise extract of recent user/assistant messages plus
        the capped live-task excerpts is enough to safely continue.
        """

        rollover_started_at = time.perf_counter()
        history = self._get_conversation_history() or []
        if len(history) < 3:
            return False

        keep_count = min(max(1, self._keep_recent_count()), 4)
        split_point = self._find_split_point(history, keep_count)
        if split_point <= 1:
            split_point = max(1, len(history) - keep_count)
        system_msg = history[0] if history and history[0].role == "system" else None
        live_task_ids = self._get_live_task_ids()
        preserved_tasks, _summarizable_tasks = self._extract_preservable_messages(
            history, live_task_ids=live_task_ids
        )
        recent = history[split_point:]
        safe_recent = [self._truncate_message_for_rollover(message) for message in recent]
        fallback_summary = self._build_emergency_summary(history[1:split_point])
        if not fallback_summary:
            fallback_summary = (
                "Earlier conversation context was rolled over after automatic compaction "
                "failed. Continue with the current user request, recent messages, and the "
                "preserved live-task excerpts below. The full transcript remains in logs."
            )

        next_round = self._compaction_round + 1
        compacted_history = self._build_compacted_history(
            system_msg=system_msg,
            summary_text=fallback_summary,
            to_keep=safe_recent,
            preserved_tasks=preserved_tasks,
            ledger_handled=[],
            compaction_round=next_round,
            pre_message_count=len(history),
        )
        self._pending_compaction = compacted_history
        self._pending_session_id = self._get_current_session_id()
        self._pre_compaction_len = len(history)
        self._compaction_round = next_round
        self._compaction_reason = f"emergency_rollover_{boundary}"
        self._token_usage_stale_after_compaction = True
        await self._apply_pending_compaction(data, event)
        duration_seconds = time.perf_counter() - rollover_started_at
        self._last_compaction_duration_seconds = duration_seconds
        await self._log_compaction_event(
            messages_summarized=max(0, len(history) - len(compacted_history)),
            messages_kept=len(safe_recent),
            summary_length=len(fallback_summary),
            pre_count=len(history),
            post_count=len(compacted_history),
            tasks_preserved=len(preserved_tasks),
            summary_text=fallback_summary,
            reason=self._compaction_reason,
            duration_seconds=duration_seconds,
            summarization_model=None,
        )
        logger.warning(
            "[context_compaction][agent=%s][hub=%s] emergency rollover applied "
            "round=%s prompt_estimate=%s",
            *self._get_agent_labels(),
            self._compaction_round,
            self._effective_prompt_tokens(),
        )
        return True

    def _truncate_message_for_rollover(
        self, message: ConversationMessage, max_chars: int = 8000
    ) -> ConversationMessage:
        """Keep tool-call metadata but cap unusually large recent message bodies."""
        cloned = copy.deepcopy(message)
        content = getattr(cloned, "content", "") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        if len(content) > max_chars:
            cloned.content = content[:max_chars] + "… [truncated during context rollover]"
        return cloned

    def _build_emergency_summary(
        self,
        messages: List[ConversationMessage],
        max_chars: int = MAX_EMERGENCY_SUMMARY_MESSAGE_CHARS,
    ) -> str:
        """Build a bounded recovery note, prioritizing the latest user request.

        ``max_chars`` is the cap on the final injected message, including its
        framing. Non-intended hub notices and standalone agent HUD updates are
        coordination context. Only a direct-scope request addressed to this hub
        identity, with no TaskLedger task ID, remains human intent. Raw task
        assignments are represented by TaskLedger-selected excerpts and must
        not reappear as active work.
        """
        wrapper_chars = len(SUMMARY_INJECTION_PREFIX) + len(SUMMARY_INJECTION_SUFFIX)
        body_limit = max(0, max_chars - wrapper_chars)
        lines = [
            "Automatic context rollover: the summarization provider failed at "
            "the hard prompt budget.",
            "The transcript remains available in the session log; this note "
            "preserves the latest user intent.",
        ]
        body_length = len("\n".join(lines))
        if body_length >= body_limit:
            return "\n".join(lines)[:body_limit]

        _, hub_identity = self._get_agent_labels()
        hub_identity_key = (
            hub_identity.strip().casefold()
            if isinstance(hub_identity, str)
            else ""
        )
        eligible_messages = []
        for index, message in enumerate(messages):
            metadata = getattr(message, "metadata", None) or {}
            if (
                metadata.get("context_compaction")
                or metadata.get("compaction_task_reminder")
                or metadata.get("context_compaction_task_excerpt")
            ):
                continue

            role = getattr(message, "role", "message")
            if role not in {"user", "assistant"}:
                continue

            # The hub marks group aliases as intended too. Keep an untracked
            # hub request only when direct scope and recipient metadata match
            # this agent; group, other-recipient, and standalone HUD records are
            # not human requests for this summary. Task-ID messages are handled
            # below.
            explicit_task_id = metadata.get("hub_task_id") or (
                metadata.get("task_id") if metadata.get("hub_is_intended") else None
            )
            recipient = metadata.get("hub_to")
            is_untracked_direct_hub_request = bool(
                metadata.get("hub_message")
                and metadata.get("hub_is_intended")
                and not explicit_task_id
                and str(metadata.get("hub_scope", "direct")).strip().casefold()
                == "direct"
                and hub_identity_key not in {"", "unknown"}
                and isinstance(recipient, str)
                and recipient.strip().casefold() == hub_identity_key
            )
            is_hub_notice = bool(
                metadata.get("hub_message")
                and not explicit_task_id
                and not is_untracked_direct_hub_request
            )
            if (
                is_hub_notice
                or metadata.get("agent_hud")
                or metadata.get("agent_hud_sources")
            ):
                continue

            content = getattr(message, "content", "") or ""
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            content = content.strip()
            if not content:
                continue

            # Active task excerpts are added independently by
            # _build_compacted_history. Do not replay raw closed, superseded,
            # or duplicate hub assignments from the old prefix.
            is_hub_task = bool(explicit_task_id)
            if not is_hub_task and not is_untracked_direct_hub_request:
                try:
                    is_hub_task = self._is_hub_task(message)
                except (AttributeError, TypeError):
                    is_hub_task = False
            if is_hub_task:
                continue

            eligible_messages.append((index, role, content))

        recent_messages = eligible_messages[-8:]
        latest_user = next(
            (
                item
                for item in reversed(eligible_messages)
                if item[1] == "user"
            ),
            None,
        )
        latest_user_index = latest_user[0] if latest_user is not None else None
        ordered_messages = []
        if latest_user is not None:
            ordered_messages.append(latest_user)
        ordered_messages.extend(
            item
            for item in reversed(recent_messages)
            if item[0] != latest_user_index
        )

        for message_index, role, content in ordered_messages:
            prefix = f"[{role}] "
            remaining = body_limit - body_length - 1
            if remaining <= len(prefix):
                break
            content_limit = remaining - len(prefix)
            if message_index != latest_user_index:
                content_limit = min(1200, content_limit)
            line = prefix + content[:content_limit]
            lines.append(line)
            body_length += len(line) + 1

        return "\n".join(lines)

    async def _apply_pending_compaction(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """LLM_REQUEST_PRE: apply staged compaction atomically."""
        # Terminal compaction states can publish from a background task after
        # the previous turn's event stream closed, so web clients that missed
        # the original event never see the banner. Re-announce the persistent
        # status at the start of every turn; the TUI widget reads this state
        # directly, and this keeps the Web UI banner's lifetime aligned with it.
        status = getattr(self, "_compaction_status", "idle")
        if status != "idle":
            publish_semantic(
                getattr(self, "renderer", None),
                "context_compaction_status",
                status=status,
                message=getattr(self, "_compaction_status_message", ""),
                action=getattr(self, "_compaction_status_action", ""),
            )

        if self._pending_compaction is None:
            return data

        history = self._get_conversation_history()
        if not history:
            self._pending_compaction = None
            self._pending_session_id = None
            self._pre_compaction_len = 0
            self._set_compaction_status(
                "failed",
                "The staged compaction could not be applied because conversation history "
                "is empty.",
                action="Use /compact now or start a new conversation.",
            )
            return data

        pending_session_id = self._pending_session_id
        current_session_id = self._get_current_session_id()
        if pending_session_id and current_session_id != pending_session_id:
            logger.info("Discarded staged compaction due to session change")
            self._set_compaction_status("idle", "")
            self._pending_compaction = None
            self._pending_session_id = None
            self._pre_compaction_len = 0
            return data

        if len(history) < self._pre_compaction_len:
            logger.info(
                "Discarded staged compaction because conversation history was reset"
            )
            self._set_compaction_status(
                "failed",
                "The staged compaction was discarded because conversation history changed.",
                action="Retry the turn; if the context is still too large, run /compact now.",
            )
            self._pending_compaction = None
            self._pending_session_id = None
            self._pre_compaction_len = 0
            return data

        # Capture any messages added during background compaction
        new_msgs = history[self._pre_compaction_len :]
        pending_compaction = self._pending_compaction
        assert pending_compaction is not None

        # Atomic swap via slice assignment
        pre_len = len(history)
        history[:] = pending_compaction + new_msgs
        post_len = len(history)
        self._token_usage_stale_after_compaction = True

        # The summary metadata is also the Web UI's durable compaction marker.
        # Update it after the swap so it agrees with the visible event when
        # messages arrived while the background compaction was running.
        for message in pending_compaction:
            metadata = getattr(message, "metadata", None)
            if isinstance(metadata, dict) and metadata.get("context_compaction"):
                metadata["pre_message_count"] = pre_len
                metadata["post_message_count"] = post_len
                break

        logger.info(
            f"Applied compaction round {self._compaction_round}: "
            f"{pre_len} -> {post_len} messages"
        )

        self._display_compaction_event(pre_len, post_len)

        # Sync conversation_manager if available
        if self._conversation_manager:
            self._conversation_manager._update_context_window()

        self._pending_compaction = None
        self._pending_session_id = None
        self._pre_compaction_len = 0
        self._compaction_just_applied = True

        self._set_compaction_status(
            "applied",
            f"Compaction round {self._compaction_round} was applied to the conversation.",
        )

        return data

    def _display_compaction_event(self, pre_count: int, post_count: int) -> None:
        """Show the completed compaction in the visible chat transcript.

        Compaction runs in the background and replaces the model-facing history
        without going through the normal message display path. Emit a
        display-only system message after the replacement succeeds so the user
        can see that the transcript changed. It is intentionally not added to
        ``conversation_history``: the compaction summary already represents
        the model-facing context, and this marker is UI-only.
        """
        renderer = self.renderer
        if renderer is None or getattr(renderer, "pipe_mode", False) is True:
            return

        coordinator = getattr(renderer, "message_coordinator", None)
        if coordinator is None or not hasattr(coordinator, "display_message_sequence"):
            return

        content = (
            f"Context compacted (round {self._compaction_round}): "
            f"{pre_count} -> {post_count} messages."
        )
        try:
            coordinator.display_message_sequence(
                [
                    (
                        "system",
                        content,
                        {
                            "display_type": "info",
                            "context_compaction": True,
                            "compaction_round": self._compaction_round,
                            "pre_message_count": pre_count,
                            "post_message_count": post_count,
                        },
                    )
                ]
            )
        except Exception:
            # Rendering is observability only; never turn a successful history
            # swap into a failed compaction.
            logger.debug("Failed to display compaction event", exc_info=True)

    # ------------------------------------------------------------------
    # Core compaction logic
    # ------------------------------------------------------------------

    def _get_conversation_history(self) -> Optional[List[ConversationMessage]]:
        """Get the live conversation_history list from llm_service."""
        if self._llm_service and hasattr(self._llm_service, "conversation_history"):
            return self._llm_service.conversation_history
        return None

    def _get_state_service(self):
        """Return the daemon state_service proxy, or None if unavailable.

        The mock guard mirrors _partition_by_ledger so unit tests that inject a
        MagicMock event_bus fall back to local state instead of awaiting a mock
        (which is not awaitable).
        """
        if not self.event_bus:
            return None
        svc = self.event_bus.get_service("state_service")
        if svc is None or type(svc).__module__ == "unittest.mock":
            return None
        return svc

    async def _fetch_display_history(self) -> List[ConversationMessage]:
        """Conversation history for /compact display, daemon-preferred.

        In attach mode the local llm_service is only a client-side shadow with
        an empty history, which made /compact report zeros. Pull the real
        conversation from the daemon via state_service; fall back to local.
        """
        svc = self._get_state_service()
        if svc is not None and hasattr(svc, "get_conversation"):
            try:
                snapshot = await svc.get_conversation()
                messages = getattr(snapshot, "messages", None)
                if messages is not None:
                    return list(messages)
            except Exception as e:
                logger.debug(f"/compact: get_conversation failed: {e}")
        return self._get_conversation_history() or []

    async def _fetch_display_tokens(self) -> int:
        """Prompt-token count for /compact display, daemon-preferred."""
        svc = self._get_state_service()
        if svc is not None and hasattr(svc, "get_session_stats"):
            try:
                stats = await svc.get_session_stats()
                input_tokens = int(getattr(stats, "input_tokens", 0) or 0)
                if input_tokens > 0:
                    return input_tokens
            except Exception as e:
                logger.debug(f"/compact: get_session_stats failed: {e}")
        return self._get_prompt_tokens()

    def _get_current_session_id(self) -> Optional[str]:
        """Return current session ID for stale-stage detection."""
        if self._conversation_logger and hasattr(
            self._conversation_logger, "session_id"
        ):
            return self._conversation_logger.session_id
        return None

    def _find_split_point(
        self, history: List[ConversationMessage], keep_recent: int
    ) -> int:
        """Find the split point for compaction.

        Returns the index of the first message to keep (keep_recent count).
        Adjusts the split to never break an assistant+tool_calls / tool_result
        group, which would cause 400 errors from OpenAI and compatible APIs.
        """
        if len(history) <= keep_recent:
            return 0

        split = len(history) - keep_recent

        # If the split lands on a tool-role message, walk backward to include
        # the preceding assistant message that owns the tool_calls.
        while split > 0 and self._is_tool_result(history[split]):
            split -= 1

        # If we landed on an assistant message with tool_calls, the tool
        # results are in to_keep but the assistant isn't -- pull it in too.
        if split > 0 and self._has_tool_calls(history[split - 1]):
            # Actually we need to NOT split here -- the assistant at split-1
            # has tool_calls whose results start at split. Move split back
            # to include the assistant message.
            split -= 1
            # Walk back further in case this assistant is itself preceded
            # by tool results from an even earlier call (shouldn't happen
            # normally, but be safe).
            while split > 0 and self._is_tool_result(history[split]):
                split -= 1

        return max(split, 0)

    @staticmethod
    def _is_tool_result(msg: ConversationMessage) -> bool:
        """Check if a message is a tool result."""
        return msg.role == "tool" or msg.metadata.get("tool_call_id") is not None

    @staticmethod
    def _has_tool_calls(msg: ConversationMessage) -> bool:
        """Check if an assistant message contains tool_calls."""
        return msg.role == "assistant" and bool(msg.metadata.get("tool_calls"))

    @staticmethod
    def _is_hub_message(msg: ConversationMessage) -> bool:
        """Check if a message was injected by the hub system."""
        # Metadata-tagged hub messages (new path)
        if msg.metadata.get("hub_message"):
            return True
        # Content-pattern fallback for messages injected before metadata tagging.
        # CONTAINMENT, not startswith: hub messages that arrive mid-turn are
        # drained as a MERGED agent-HUD block (context/budget sections first),
        # so "[hub channel:" appears mid-content, not at the start.
        if (
            msg.role == "user"
            and ("[hub channel:" in msg.content or "[hub:" in msg.content)
        ):
            return True
        return False

    @staticmethod
    def _is_hub_task(msg: ConversationMessage) -> bool:
        """Check if a hub message contains a task assignment.

        A hub task is an intended message (not an observed broadcast)
        that contains action-oriented language -- something the agent
        is expected to DO and potentially report back on.
        """
        if not msg.metadata.get("hub_message"):
            # Fallback: content-based detection for untagged messages.
            # Containment — see _is_hub_message for the merged-HUD rationale.
            if not (
                msg.role == "user"
                and (
                    "[hub channel:" in msg.content or "[hub:" in msg.content
                )
            ):
                return False

        content_lower = msg.content.lower()

        # Observed messages (not intended for us) are never tasks.
        # Check BEFORE the hub_is_intended shortcut: an intended message
        # that is explicitly observed-flagged is still not a task.
        if (
            "you do not need to respond" in content_lower
            or "do not relay, repeat, or respond" in content_lower
        ):
            return False

        metadata = getattr(msg, "metadata", None) or {}
        if metadata.get("hub_task_id") or (
            metadata.get("task_id") and metadata.get("hub_is_intended")
        ):
            return True


        # Check for task indicators
        for indicator in _TASK_INDICATORS:
            if indicator in content_lower:
                return True

        # If the message is directly addressed hub message (hub_is_intended)
        # and has substantial content, treat as task to be safe
        if msg.metadata.get("hub_is_intended") and len(msg.content) > 50:
            return True

        return False

    @staticmethod
    def _message_task_id(msg: ConversationMessage) -> Optional[str]:
        metadata = getattr(msg, "metadata", None) or {}
        task_id = metadata.get("task_id")
        return str(task_id).strip() if task_id else None

    def _get_live_task_ids(self) -> set[str]:
        """Return this identity's active task IDs plus its targeted OPEN cards."""
        if not self.event_bus:
            return set()
        try:
            hub_plugin = self.event_bus.get_service("hub_plugin")
            ledger = getattr(hub_plugin, "_task_ledger", None)
            identity = getattr(getattr(hub_plugin, "_identity", None), "identity", None)
            if (
                not ledger
                or not identity
                or type(ledger).__module__ == "unittest.mock"
            ):
                return set()

            cards = list(ledger.get_active_for(identity))
            cards.extend(
                card
                for card in ledger.get_all(status="paused")
                if getattr(card, "status", None) == "paused"
                and getattr(card, "assignee", "") == identity
            )
            cards.extend(
                card
                for card in ledger.get_open()
                if getattr(card, "status", None) == "open"
                and not getattr(card, "assignee", "")
                and str(getattr(card, "directive", "")).startswith(f"{identity}:")
            )
            return {
                str(card.id)
                for card in cards
                if getattr(card, "id", None)
            }
        except Exception:
            logger.debug("Unable to read live TaskLedger IDs for compaction", exc_info=True)
            return set()

    def _extract_preservable_messages(
        self,
        messages: List[ConversationMessage],
        *,
        live_task_ids: Optional[set[str]] = None,
    ) -> tuple:
        """Keep only bounded excerpts for the latest messages of live tasks.

        Historical and superseded task messages remain summarizable. Explicit
        GO, delivery, and board-rebuild blocks are handled separately by
        ``_extract_coordination_lines`` so a task message is never preserved
        wholesale merely because it mentions a control command.
        """
        live_ids = (
            {str(task_id) for task_id in live_task_ids}
            if live_task_ids is not None
            else self._get_live_task_ids()
        )
        latest_by_task: Dict[str, tuple[int, ConversationMessage]] = {}

        for index, msg in enumerate(messages):
            if not self._is_hub_task(msg):
                continue
            task_id = self._message_task_id(msg)
            if task_id and task_id in live_ids:
                latest_by_task[task_id] = (index, msg)

        latest_tasks = sorted(
            latest_by_task.values(), key=lambda entry: entry[0]
        )[-MAX_PRESERVED_HUB_TASK_MESSAGES:]
        preserved_by_index: Dict[int, ConversationMessage] = {}
        oversized_indices: set[int] = set()
        for index, msg in latest_tasks:
            content = str(getattr(msg, "content", "") or "")
            if len(content) <= MAX_PRESERVED_HUB_TASK_CHARS:
                preserved_by_index[index] = msg
                continue

            task_id = (self._message_task_id(msg) or "unknown")[:128]
            prefix = f"[Active TaskLedger task {task_id}; latest assignment excerpt]\n"
            suffix = "\n[Remaining assignment text is summarized below.]"
            excerpt_budget = max(
                0, MAX_PRESERVED_HUB_TASK_CHARS - len(prefix) - len(suffix)
            )
            truncated = copy.copy(msg)
            truncated.content = f"{prefix}{content[:excerpt_budget]}{suffix}"
            truncated.metadata = {
                **(getattr(msg, "metadata", None) or {}),
                "context_compaction_task_excerpt": True,
            }
            preserved_by_index[index] = truncated
            oversized_indices.add(index)

        preserved = [
            preserved_by_index[index]
            for index in sorted(preserved_by_index)
        ]
        summarizable = [
            msg
            for index, msg in enumerate(messages)
            if index not in preserved_by_index or index in oversized_indices
        ]

        if preserved:
            logger.info(
                "Task extraction: preserved %d bounded live-task messages; "
                "%d messages will be summarized",
                len(preserved),
                len(summarizable),
            )
        return preserved, summarizable

    def _format_messages_for_summary(self, messages: List[ConversationMessage]) -> str:
        """Format messages into a text block for summarization.

        Skips tool-role messages and assistant tool_calls metadata since
        the summarizer only needs the high-level conversation flow.
        Tool outputs are captured by the assistant's follow-up response.
        """
        parts = []
        for msg in messages:
            # Skip tool result messages -- their output is echoed by the
            # assistant in its follow-up, so including them is redundant
            # and can confuse the summarizer with raw JSON.
            if self._is_tool_result(msg):
                continue

            role_label = msg.role.capitalize()
            content = msg.content
            if len(content) > 2000:
                content = content[:2000] + "... [truncated]"

            # For assistant messages with tool_calls, note which tools ran
            if self._has_tool_calls(msg):
                tool_names = [
                    tc.get("function", {}).get("name", tc.get("name", "?"))
                    for tc in msg.metadata["tool_calls"]
                ]
                content += f"\n[Used tools: {', '.join(tool_names)}]"

            parts.append(f"[{role_label}]: {content}")
        return "\n\n".join(parts)

    async def _run_compaction(self) -> None:
        """Background task: summarize old messages and stage compaction."""
        staged = False
        round_started_at = time.perf_counter()
        round_number = self._compaction_round + 1
        self._last_summarization_model = None
        self._last_compaction_duration_seconds = None
        try:
            history = self._get_conversation_history()
            if not history:
                self._set_compaction_status(
                    "failed",
                    "Compaction could not run because conversation history is empty; "
                    "no history was changed.",
                    action="Start a new request before compacting.",
                )
                return
            history_snapshot = list(history)
            snapshot_len = len(history_snapshot)
            snapshot_session_id = self._get_current_session_id()

            keep_recent = self._keep_recent_count(history_snapshot)
            max_summary_tokens = self.config.get(
                "plugins.context_compaction.max_summary_tokens", 2000
            )

            # Identify boundaries
            split_point = self._find_split_point(history_snapshot, keep_recent)
            if split_point <= 1:
                # Nothing meaningful to summarize
                logger.info("Not enough messages to summarize, skipping")
                self._set_compaction_status(
                    "failed",
                    "Compaction could not run because there are not enough older messages "
                    "to summarize.",
                    action="Use /compact preview or reduce the conversation history.",
                )
                return

            # Separate system message
            system_msg = None
            start_idx = 0
            if history_snapshot and history_snapshot[0].role == "system":
                system_msg = history_snapshot[0]
                start_idx = 1

            to_summarize = history_snapshot[start_idx:split_point]
            to_keep = history_snapshot[split_point:]

            if not to_summarize:
                logger.info("No messages to summarize after boundaries")
                self._set_compaction_status(
                    "failed",
                    "Compaction could not run because the removable history is empty.",
                    action="Use /compact preview or reduce the conversation history.",
                )
                return

            # Save verbatim messages to vault before they're lost to compaction
            try:
                hub_plugin = (
                    self.event_bus.get_service("hub_plugin") if self.event_bus else None
                )
                vault = (
                    hub_plugin.get_vault()
                    if hub_plugin and hasattr(hub_plugin, "get_vault")
                    else None
                )
                if vault:
                    snapshot_lines = []
                    for msg in to_summarize[-20:]:  # last 20 messages max
                        role = getattr(msg, "role", "?")
                        content = getattr(msg, "content", "")
                        if content:
                            snapshot_lines.append(f"[{role}] {content[:500]}")
                    if snapshot_lines:
                        vault.append_stream(
                            "context_snapshot",
                            "\n---\n".join(snapshot_lines),
                        )
            except Exception as e:
                logger.debug(f"Vault snapshot failed: {e}")

            # --- Context service ledger integration ---
            # Partition messages: those with ctx_ids in metadata get
            # handled by the ledger; the rest go to LLM summarization.
            ledger_handled, untracked_msgs = (
                self._partition_by_ledger(to_summarize)
            )

            # --- Task-aware extraction ---
            # Separate hub task messages (preserved verbatim) from
            # regular conversation (summarized by LLM).
            preserved_tasks, summarizable = self._extract_preservable_messages(
                untracked_msgs
            )

            # --- Deterministic coordination block (no LLM discretion) -----
            # Any hub-coordination line still present in the SUMMARIZABLE
            # set (mid-turn messages that reached history without hub
            # metadata, human-elsewhere traffic, GO signals, delivery
            # reports) is extracted VERBATIM and prepended to the summary.
            # The summarizer never gets to decide whether coordination
            # state survives — it is passed through.
            coordination_lines = self._extract_coordination_lines(summarizable)
            coordination_block = ""
            if coordination_lines:
                coordination_block = (
                    "=== COORDINATION STATE (verbatim, machine-extracted) ===\n"
                    + "\n---\n".join(coordination_lines)
                    + "\n=== END COORDINATION STATE ==="
                )
                logger.info(
                    f"Coordination block: {len(coordination_lines)} hub lines "
                    "extracted verbatim into summary"
                )

            summary_text = None
            if summarizable:
                # Format only the summarizable portion for the LLM
                formatted = self._format_messages_for_summary(summarizable)
                system_prompt = SUMMARIZATION_SYSTEM_PROMPT.format(
                    max_tokens=max_summary_tokens
                )

                summary_text = await self._call_summarization_llm(
                    formatted, system_prompt, max_summary_tokens
                )

                if not summary_text or len(summary_text) < 100:
                    logger.warning(
                        f"Summary too short or empty "
                        f"({len(summary_text) if summary_text else 0} chars), "
                        "treating as failure"
                    )
                    self._consecutive_failures += 1
                    self._check_failure_threshold()
                    self._set_compaction_status(
                        "failed",
                        "The summarizer returned too little content, so compaction was not "
                        "applied. The failed model request was not retried.",
                        action="Run /compact now, or reduce conversation history.",
                    )
                    return

            # Build compacted history:
            # system + summary (coordination block PREPENDS the LLM
            # summary so it can never be lost to summarization) + ledger
            # decisions + tasks + to_keep
            if coordination_block:
                if summary_text:
                    summary_text = coordination_block + "\n\n" + summary_text
                else:
                    summary_text = coordination_block
            # The model's own <compact> notes go first, verbatim: it chose them.
            model_notes = self._model_notes
            if model_notes:
                notes_block = (
                    "=== YOUR NOTES (written by you before compaction, verbatim) ===\n"
                    + model_notes
                    + "\n=== END NOTES ==="
                )
                summary_text = (
                    notes_block + "\n\n" + summary_text if summary_text else notes_block
                )
            compacted = self._build_compacted_history(
                system_msg,
                summary_text or "",
                to_keep,
                preserved_tasks,
                ledger_handled=ledger_handled,
                compaction_round=self._compaction_round + 1,
                pre_message_count=snapshot_len,
            )

            # Write checkpoint before staging the compaction swap
            checkpoint_filename = self._write_compaction_checkpoint(
                history_snapshot
            )
            if checkpoint_filename:
                self._log_checkpoint_event(
                    checkpoint_filename, len(history_snapshot)
                )

            # Stage for safe application from the snapshot baseline.
            self._pre_compaction_len = snapshot_len
            self._pending_session_id = snapshot_session_id
            self._pending_compaction = compacted
            staged = True

            self._compaction_round += 1
            self._consecutive_failures = 0
            self._model_notes = None

            # Log compaction event (summary_text persisted for rebirth)
            duration_seconds = time.perf_counter() - round_started_at
            self._last_compaction_duration_seconds = duration_seconds
            await self._log_compaction_event(
                messages_summarized=len(summarizable),
                messages_kept=len(to_keep),
                summary_length=len(summary_text),
                pre_count=snapshot_len,
                post_count=len(compacted),
                tasks_preserved=len(preserved_tasks),
                summary_text=summary_text or "",
                reason=getattr(self, "_compaction_reason", "threshold"),
                duration_seconds=duration_seconds,
                summarization_model=self._last_summarization_model,
            )

            logger.info(
                f"Compaction round {self._compaction_round} staged: "
                f"summarized {len(summarizable)} msgs, "
                f"preserved {len(preserved_tasks)} task(s), "
                f"keeping {len(to_keep)}"
            )
            if getattr(self, "_compaction_reason", "threshold") == "context_window_exceeded":
                ready_message = (
                    "A bounded compaction finished after the provider rejected the last "
                    "request. The failed request was not retried; retry it now to apply "
                    "the compacted context."
                )
                action = "Retry the failed turn to apply compaction and continue."
            else:
                ready_message = (
                    "Compaction is ready and will be applied before the next model request."
                )
                action = "Continue with the next request."
            self._set_compaction_status(
                "ready_to_retry", ready_message, action=action
            )

            try:
                from kollabor_ai.notifications.producer import push_env

                removed = snapshot_len - len(compacted)
                push_env(
                    self.event_bus,
                    "file",
                    (
                        f"compacted r{self._compaction_round}: "
                        f"{removed} msgs removed"
                    ),
                    kind="compaction",
                )
            except Exception:
                pass

        except asyncio.CancelledError:
            agent, hub_identity = self._get_agent_labels()
            logger.info(
                "[context_compaction][agent=%s][hub=%s] round=%s cancelled",
                agent,
                hub_identity,
                round_number,
            )
            if staged:
                self._set_compaction_status(
                    "ready_to_retry",
                    "Compaction was staged before cancellation and will be applied "
                    "before the next model request.",
                    action="Start a new request to apply the staged history.",
                )
            else:
                self._set_compaction_status(
                    "failed",
                    "Compaction was cancelled before staging; no history was changed.",
                    action="Retry /compact now after the cancelled turn ends.",
                )
            raise
        except Exception as e:
            agent, hub_identity = self._get_agent_labels()
            logger.error(
                "[context_compaction][agent=%s][hub=%s] round=%s failed: %s",
                agent,
                hub_identity,
                round_number,
                e,
                exc_info=True,
            )
            if staged:
                self._set_compaction_status(
                    "ready_to_retry",
                    "Compaction was staged and will be applied before the next model "
                    "request, but final bookkeeping failed.",
                    action="Continue with the next request and check the log.",
                )
            else:
                self._consecutive_failures += 1
                self._check_failure_threshold()
                self._set_compaction_status(
                    "failed",
                    "Compaction failed and was not applied. The failed request was not "
                    "retried automatically; check the log and run /compact now.",
                    action="Run /compact now after checking the displayed error.",
                )
        finally:
            self._last_compaction_duration_seconds = time.perf_counter() - round_started_at
            agent, hub_identity = self._get_agent_labels()
            logger.info(
                "[context_compaction][agent=%s][hub=%s] round=%s model=%s "
                "duration_seconds=%.3f status=%s",
                agent,
                hub_identity,
                round_number,
                self._last_summarization_model or "unresolved",
                self._last_compaction_duration_seconds,
                self._compaction_status,
            )
            self._compaction_in_progress = False

    async def _call_provider_with_retry(self, provider, messages):
        """provider.call() with the main request path's transient-error retry.

        ApiCommunicationService retries 429/5xx/transport errors with capped
        exponential backoff, but the summarizer talks to its own provider, so one
        429 failed the round (three failures disable compaction for the session).
        Same knob, constants and Retry-After handling as that path.
        """
        import random

        from kollabor_ai.api_communication_service import (
            RETRY_BASE_DELAY_SECONDS,
            RETRY_JITTER_MAX,
            RETRY_JITTER_MIN,
            RETRY_MAX_DELAY_SECONDS,
        )
        from kollabor_ai.providers.errors import (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            ServerError,
            TransientHTTPError,
        )

        try:
            max_retries = max(0, int(self.config.get("kollabor.llm.max_retries", 5)))
        except (TypeError, ValueError):
            max_retries = 5
        for attempt in range(max_retries + 1):
            try:
                return await provider.call(messages=messages)
            except Exception as e:
                status = getattr(e, "status_code", None)
                transient = (
                    isinstance(
                        e,
                        (
                            RateLimitError,
                            ServerError,
                            APIConnectionError,
                            APITimeoutError,
                            TransientHTTPError,
                        ),
                    )
                    or status in (408, 409, 429)
                    or (isinstance(status, int) and 500 <= status < 600)
                )
                if not transient or attempt == max_retries:
                    raise
                try:
                    wait = float(getattr(e, "retry_after", None))
                except (TypeError, ValueError):
                    wait = None
                if wait is not None and not 0 <= wait <= RETRY_MAX_DELAY_SECONDS:
                    raise  # the server asked for longer than we will wait
                delay = wait if wait is not None else min(
                    RETRY_BASE_DELAY_SECONDS
                    * 2**attempt
                    * random.uniform(RETRY_JITTER_MIN, RETRY_JITTER_MAX),
                    RETRY_MAX_DELAY_SECONDS,
                )
                logger.warning(
                    "Summarization call failed (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1,
                    max_retries + 1,
                    delay,
                    str(e)[:120],
                )
                await asyncio.sleep(delay)

    def _get_active_session_model(
        self, profile_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Resolve the model already selected for the active conversation."""
        llm_service = self._llm_service
        api_service = getattr(llm_service, "api_service", None)
        provider = getattr(api_service, "_provider", None)
        provider_config = getattr(provider, "config", None)
        for value in (
            getattr(provider, "model", None),
            getattr(provider_config, "model", None),
            getattr(api_service, "model", None),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()

        manager = self._profile_manager
        if manager:
            try:
                active_profile = manager.get_active_profile()
                model = active_profile.get_model() if active_profile else None
                if isinstance(model, str) and model.strip():
                    return model.strip()
                active_data = active_profile.to_dict() if active_profile else None
                if isinstance(active_data, dict):
                    model = active_data.get("model")
                    if isinstance(model, str) and model.strip():
                        return model.strip()
            except Exception:
                logger.debug("Unable to resolve the active session model", exc_info=True)

        model = profile_data.get("model") if isinstance(profile_data, dict) else None
        return model.strip() if isinstance(model, str) and model.strip() else None

    def _resolve_summarization_model(
        self, profile_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Choose plugin override, shared fast model, then active session model."""
        fast_model = resolve_fast_model(
            self.config, "plugins.context_compaction.summarization_model"
        )
        if fast_model:
            return str(fast_model)
        return self._get_active_session_model(profile_data)

    def _summarization_model_source(self) -> str:
        """Identify the configured precedence layer used for summarization."""
        plugin_model = self.config.get(
            "plugins.context_compaction.summarization_model", None
        )
        if plugin_model:
            return "plugin"
        shared_model = self.config.get("kollabor.llm.fast_model", None)
        return "shared_fast_model" if shared_model else "session"


    async def _call_summarization_llm(
        self,
        formatted_messages: str,
        system_prompt: str,
        max_tokens: int,
    ) -> Optional[str]:
        """Call LLM to generate conversation summary."""
        from kollabor_ai.providers.registry import (
            ProviderRegistry,
            create_config_from_profile,
        )

        started_at = time.perf_counter()
        agent, hub_identity = self._get_agent_labels()
        selected_model: Optional[str] = None

        try:
            # Get profile for summarization
            profile_name = self.config.get(
                "plugins.context_compaction.summarization_profile", None
            )
            profile_data = None

            if profile_name and self._profile_manager:
                profile_obj = self._profile_manager.get_profile(profile_name)
                if profile_obj:
                    profile_data = profile_obj.to_dict()
                else:
                    logger.warning(
                        f"Summarization profile '{profile_name}' not found, "
                        "falling back to current"
                    )

            if not profile_data and self._profile_manager:
                profile_obj = self._profile_manager.get_active_profile()
                if profile_obj:
                    profile_data = profile_obj.to_dict()

            if not profile_data:
                logger.error("No profile available for summarization")
                return None

            profile_data = dict(profile_data)
            selected_model = self._resolve_summarization_model(profile_data)
            if selected_model:
                profile_data["model"] = selected_model
            self._last_summarization_model = selected_model
            agent, hub_identity = self._get_agent_labels()
            logger.info(
                "[context_compaction][agent=%s][hub=%s] round=%s "
                "summarization_start model=%s source=%s",
                agent,
                hub_identity,
                self._compaction_round + 1,
                selected_model or profile_data.get("model", "profile_default"),
                self._summarization_model_source(),
            )

            # Create provider config and get provider
            provider_config = create_config_from_profile(profile_data)
            provider = await ProviderRegistry.get_provider(provider_config)

            # Build messages for the summarization call
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Summarize this conversation:\n\n{formatted_messages}",
                },
            ]

            # provider.call() returns UnifiedResponse
            # max_tokens is determined by the provider config from the profile
            response = await self._call_provider_with_retry(provider, messages)

            # UnifiedResponse has get_text_content() helper
            if hasattr(response, "get_text_content"):
                summary = response.get_text_content()
            elif isinstance(response, str):
                summary = response
            else:
                summary = str(response)

            self._last_compaction_duration_seconds = time.perf_counter() - started_at
            logger.info(
                "[context_compaction][agent=%s][hub=%s] round=%s "
                "summarization_complete model=%s duration_seconds=%.3f",
                agent,
                hub_identity,
                self._compaction_round + 1,
                selected_model or profile_data.get("model", "profile_default"),
                self._last_compaction_duration_seconds,
            )
            return summary

        except Exception as e:
            self._last_compaction_duration_seconds = time.perf_counter() - started_at
            logger.error(
                "[context_compaction][agent=%s][hub=%s] summarization_failed "
                "model=%s duration_seconds=%.3f error=%s",
                agent,
                hub_identity,
                selected_model or self._last_summarization_model or "unresolved",
                self._last_compaction_duration_seconds,
                e,
                exc_info=True,
            )
            return None

    def _partition_by_ledger(
        self,
        messages: List[ConversationMessage],
    ) -> tuple:
        """Partition messages into ledger-handled and untracked.

        For each message with ctx_ids in metadata, look up the
        ledger entries and apply decisions:
          - keep: preserve the original message verbatim
          - summary: replace with agent-written summary
          - pending: left untracked, so the summarizer covers it
          - evicted: keep the already-rewritten stub

        Returns (ledger_handled, untracked_msgs).
        """
        context_svc = None
        if self.event_bus:
            _svc = self.event_bus.get_service("context_service")
            if (
                _svc is not None
                and type(_svc).__module__ != "unittest.mock"
                and hasattr(_svc, "entry_for_message")
            ):
                context_svc = _svc

        if context_svc is None:
            return [], list(messages)

        ledger_handled = []
        untracked = []

        for msg in messages:
            ctx_ids = getattr(msg, "metadata", {}).get("ctx_ids", [])
            if not ctx_ids:
                untracked.append(msg)
                continue

            # Message has ledger entries — apply decisions
            # For batched tool results with multiple entries, apply
            # the most conservative decision across all entries.
            entries = []
            for cid in ctx_ids:
                # Find entry by ctx_id
                for e in context_svc.all_entries():
                    if e.ctx_id == cid:
                        entries.append(e)
                        break

            if not entries:
                untracked.append(msg)
                continue

            # Decision priority: keep > summary > pending > evicted
            has_keep = any(e.decision == "keep" for e in entries)
            has_summary = any(
                e.decision == "summary" and e.decision_body for e in entries
            )
            has_evicted = all(
                e.decision == "evicted" for e in entries
            )

            if has_keep or has_evicted:
                # keep: verbatim. evicted: the already-rewritten stub.
                ledger_handled.append(self._detach_tool_result(msg, ctx_ids))
            elif has_summary:
                # Replace with agent-written summary
                summary_parts = []
                for e in entries:
                    if e.decision == "summary" and e.decision_body:
                        summary_parts.append(
                            f"[{e.ctx_id} summary] {e.decision_body}"
                        )
                new_msg = ConversationMessage(
                    role="user" if msg.role == "tool" else msg.role,
                    content="\n".join(summary_parts),
                    metadata={
                        "compacted_from": ctx_ids,
                        "ledger_decision": "summary",
                    },
                )
                ledger_handled.append(new_msg)
            else:
                # Pending (agent never curated it): fall through to the
                # summarizer, as the curator prompt promises. Eliding it
                # would drop the content with no summary at all.
                untracked.append(msg)

        if ledger_handled:
            logger.info(
                f"Ledger partition: {len(ledger_handled)} handled, "
                f"{len(untracked)} untracked"
            )

        return ledger_handled, untracked

    @staticmethod
    def _detach_tool_result(
        msg: ConversationMessage, ctx_ids: List[str]
    ) -> ConversationMessage:
        """A native tool result whose tool_call is summarized away would be an
        orphan the API rejects (400), so it is kept as a plain user message."""
        if msg.role != "tool":
            return msg
        return ConversationMessage(
            role="user",
            content=f"[kept tool result {', '.join(ctx_ids)}]\n{msg.content}",
            metadata={"compacted_from": ctx_ids, "ledger_decision": "keep"},
        )

    def _build_compacted_history(
        self,
        system_msg: Optional[ConversationMessage],
        summary_text: str,
        to_keep: List[ConversationMessage],
        preserved_tasks: Optional[List[ConversationMessage]] = None,
        ledger_handled: Optional[List[ConversationMessage]] = None,
        compaction_round: Optional[int] = None,
        pre_message_count: Optional[int] = None,
        _dropped_preserved: int = 0,
    ) -> List[ConversationMessage]:
        """Build the new compacted history list.

        Layout after compaction:
          [system_msg]           -- original system prompt (if any)
          [summary message]      -- LLM-generated summary (if any)
          [ledger_handled msgs]  -- messages with ledger decisions applied
          [preserved task 1]     -- verbatim hub task messages
          [preserved task 2]     -- ...
          [task reminder]        -- synthetic system note listing active tasks
          [to_keep messages]     -- recent messages kept intact
        """
        base_summary = summary_text  # each drop retry starts from this, so one note
        if _dropped_preserved:
            # Issue #279: say what was dropped so the model knows the
            # preserved record is incomplete.
            summary_text = (
                (summary_text or "")
                + f"\n\n[Dropped {_dropped_preserved} oldest preserved task "
                "message(s) to fit the hard context cap; the full text remains "
                "in the session transcript.]"
            )
        # Every round writes a fresh reminder and older ones ride along (as kept
        # messages, or as "tasks" because they say "report back"), so five piled up
        # (23K chars) in the 2026-09-30 r8 checkpoint. Keep one: this round's, or
        # the newest old one when no task is live.
        def _is_reminder(m: ConversationMessage) -> bool:
            return bool(m.metadata.get("compaction_task_reminder"))

        stale = [
            m
            for m in (*(ledger_handled or ()), *(preserved_tasks or ()), *to_keep)
            if _is_reminder(m)
        ]
        if stale:
            ledger_handled = [m for m in ledger_handled or () if not _is_reminder(m)]
            preserved_tasks = [m for m in preserved_tasks or () if not _is_reminder(m)]
            to_keep = [m for m in to_keep if not _is_reminder(m)]

        compacted: List[ConversationMessage] = []

        if system_msg:
            compacted.append(system_msg)

        summary_content = (
            SUMMARY_INJECTION_PREFIX + summary_text + SUMMARY_INJECTION_SUFFIX
        )

        summary_metadata: Dict[str, Any] = {"context_compaction": True}
        if compaction_round is not None:
            summary_metadata["compaction_round"] = compaction_round
        if pre_message_count is not None:
            summary_metadata["pre_message_count"] = pre_message_count

        summary_message = ConversationMessage(
            role="user",
            content=summary_content,
            metadata=summary_metadata,
        )
        compacted.append(summary_message)

        # Inject ledger-handled messages (decisions already applied)
        if ledger_handled:
            compacted.extend(ledger_handled)
            logger.info(
                f"Injected {len(ledger_handled)} ledger-handled "
                f"message(s) into compacted history"
            )

        # Inject preserved task messages verbatim
        if preserved_tasks:
            for task_msg in preserved_tasks:
                compacted.append(task_msg)

            # Add a task reminder so the agent has a clear, scannable
            # list of what it still needs to do after compaction.
            reminder_lines = [
                "[Task Reminder after context compaction]",
                "The following tasks from hub agents are still active "
                "and were preserved verbatim above:",
                "",
            ]
            for i, task_msg in enumerate(preserved_tasks, 1):
                # Extract the sender from metadata or content
                sender = task_msg.metadata.get("hub_from", "unknown")
                # Truncate content for the reminder -- full text is
                # in the preserved message itself
                preview = task_msg.content[:200]
                if len(task_msg.content) > 200:
                    preview += "..."
                reminder_lines.append(f"  {i}. From {sender}: {preview}")

            reminder_lines.append("")
            reminder_lines.append(
                "Complete these tasks and report back as instructed. "
                "The full task text is in the messages above."
            )

            compacted.append(
                ConversationMessage(
                    role="user",
                    content="\n".join(reminder_lines),
                    metadata={"compaction_task_reminder": True},
                )
            )

            logger.info(
                f"Injected {len(preserved_tasks)} preserved task(s) "
                f"and task reminder into compacted history"
            )
        elif stale:
            compacted.append(stale[-1])

        compacted.extend(to_keep)

        # A preserved floor at or above the hard cap would defeat every later
        # boundary guard: drop the oldest preserved tasks and keep the newest.
        hard_cap = 0
        try:
            hard_cap = self._get_hard_cap_threshold()
        except AttributeError:
            pass
        if (
            hard_cap > 0
            and preserved_tasks
            and len(preserved_tasks) > 1
            and self._estimate_history_tokens(compacted) >= hard_cap
        ):
            return self._build_compacted_history(
                system_msg,
                base_summary,
                to_keep,
                preserved_tasks[1:],
                ledger_handled=ledger_handled,
                compaction_round=compaction_round,
                pre_message_count=pre_message_count,
                _dropped_preserved=_dropped_preserved + 1,
            )

        if pre_message_count is not None:
            summary_message.metadata["post_message_count"] = len(compacted)

        return compacted

    def _check_failure_threshold(self) -> None:
        """Disable compaction for session after 3 consecutive failures."""
        if self._consecutive_failures >= 3:
            logger.warning(
                "3 consecutive compaction failures, disabling for this session"
            )
            self._disabled_for_session = True

    # ------------------------------------------------------------------
    # JSONL logging
    # ------------------------------------------------------------------

    def _write_compaction_checkpoint(
        self,
        history_snapshot: List[ConversationMessage],
    ) -> Optional[str]:
        """Write pre-compaction history to a checkpoint JSONL file.

        Creates a snapshot of the full conversation history before the
        atomic swap, preserving the pre-compaction state for auditing.

        Returns the checkpoint filename, or None on failure.
        """
        if not self._conversation_logger:
            return None

        session_file = getattr(self._conversation_logger, "session_file", None)
        if not session_file or not isinstance(session_file, Path):
            return None

        next_round = self._compaction_round + 1
        checkpoint_name = f"{session_file.stem}.checkpoint.r{next_round}.jsonl"
        checkpoint_path = session_file.parent / checkpoint_name

        try:
            with open(checkpoint_path, "w") as f:
                for msg in history_snapshot:
                    record = asdict(msg)
                    # Ensure timestamp is serializable
                    ts = record.get("timestamp")
                    if hasattr(ts, "isoformat"):
                        record["timestamp"] = ts.isoformat()
                    f.write(json.dumps(record, default=str) + "\n")

            logger.info(
                f"Wrote compaction checkpoint: {checkpoint_name} "
                f"({len(history_snapshot)} messages)"
            )
            return checkpoint_name
        except Exception as e:
            logger.warning(f"Failed to write compaction checkpoint: {e}")
            return None

    def _log_checkpoint_event(
        self,
        checkpoint_filename: str,
        message_count: int,
    ) -> None:
        """Append a checkpoint record to the main session JSONL."""
        if not self._conversation_logger:
            return

        session_file = getattr(self._conversation_logger, "session_file", None)
        if not session_file or not isinstance(session_file, Path):
            return

        session_id = getattr(self._conversation_logger, "session_id", "unknown")
        next_round = self._compaction_round + 1

        record = {
            "type": "compaction_checkpoint",
            "sessionId": session_id,
            "uuid": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "compaction_round": next_round,
            "checkpoint_file": checkpoint_filename,
            "message_count": message_count,
        }

        try:
            with open(session_file, "a") as f:
                f.write(json.dumps(record) + "\n")
            logger.debug(
                f"Logged checkpoint event for round {next_round}"
            )
        except Exception as e:
            logger.warning(f"Failed to log checkpoint event: {e}")


    async def _log_compaction_event(
        self,
        messages_summarized: int,
        messages_kept: int,
        summary_length: int,
        pre_count: int,
        post_count: int,
        tasks_preserved: int = 0,
        summary_text: str = "",
        reason: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        summarization_model: Optional[str] = None,
    ) -> None:
        """Append compaction record to session JSONL.

        summary_text (full summary incl. the deterministic COORDINATION STATE
        block) is persisted so rebirth/replay can recover coordination state
        from disk — the in-memory summary_message never reaches JSONL on its
        own (aquamarine's review, gap [A]).
        """
        if not self.config.get(
            "plugins.context_compaction.log_compaction_events", True
        ):
            return

        if not self._conversation_logger:
            return

        session_file = getattr(self._conversation_logger, "session_file", None)
        if not session_file or not isinstance(session_file, Path):
            return

        session_id = getattr(self._conversation_logger, "session_id", "unknown")

        record = {
            "type": "context_compaction",
            "sessionId": session_id,
            "uuid": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "compaction_round": self._compaction_round,
            "messages_summarized": messages_summarized,
            "messages_kept": messages_kept,
            "summary_length": summary_length,
            "pre_message_count": pre_count,
            "post_message_count": post_count,
            "tasks_preserved": tasks_preserved,
        }
        agent, hub_identity = self._get_agent_labels()
        record["agent"] = agent
        record["hub_identity"] = hub_identity
        record["reason"] = reason or getattr(self, "_compaction_reason", "auto")
        if summarization_model:
            record["summarization_model"] = summarization_model
        if duration_seconds is not None:
            record["duration_seconds"] = round(duration_seconds, 3)
        if summary_text:
            # Replayable payload: conversation_manager loader (or rebirth
            # tooling) treats this as a replayable summary record —
            # last-round-wins + tail-after-timestamp on resume.
            record["content"] = summary_text
            record["role"] = "user"
            record["subtype"] = "compaction_summary"

        try:
            with open(session_file, "a") as f:
                f.write(json.dumps(record) + "\n")
            logger.debug(f"Logged compaction event for round {self._compaction_round}")
        except Exception as e:
            logger.warning(f"Failed to log compaction event: {e}")

    # ------------------------------------------------------------------
    # Status line
    # ------------------------------------------------------------------

    def get_status_lines(self) -> Dict[str, List[str]]:
        """Status line for area C showing compaction and hard-cap state."""
        if not self.config.get("plugins.context_compaction.enabled", True):
            return {"A": [], "B": [], "C": []}

        prompt_tokens = self._effective_prompt_tokens()
        threshold = self._get_token_threshold()
        token_k = f"{prompt_tokens / 1000:.0f}K" if prompt_tokens else "0"
        thresh_k = f"{threshold / 1000:.0f}K"
        state = getattr(self, "_compaction_status", "idle")
        status_message = getattr(self, "_compaction_status_message", "")
        hard_cap_active = state in {"paused", "failed", "warning", "rolled_over"} or (
            state == "compacting" and "context budget reached" in status_message.lower()
        )
        if state in {"paused", "failed", "rolled_over"} or (
            state == "compacting" and "context budget reached" in status_message.lower()
        ):
            threshold = self._get_hard_cap_threshold()
            thresh_k = f"{threshold / 1000:.0f}K"

        if hard_cap_active:
            limit = self._get_session_turn_budget()
            turn_label = (
                f" {self._turn_count}/{limit}t"
                if limit > 0 and self._turn_count >= limit
                else ""
            )
            agent, _hub_identity = self._get_agent_labels()
            agent_label = f" {agent}" if agent not in {"interactive", "unknown"} else ""
            status = f"ctx: {state} {token_k}/{thresh_k}{turn_label}{agent_label}"
        elif self._compaction_in_progress:
            status = f"ctx: compacting {token_k}/{thresh_k}"
        elif self._compaction_round > 0:
            status = f"ctx: r{self._compaction_round} {token_k}/{thresh_k}"
        else:
            status = f"ctx: {token_k}/{thresh_k}"

        if self._disabled_for_session:
            status += " [disabled]"

        return {"A": [], "B": [], "C": [status]}
