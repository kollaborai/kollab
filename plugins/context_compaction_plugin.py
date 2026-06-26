"""Context compaction plugin for automatic conversation summarization.

Monitors conversation length and automatically compresses old messages
into a summary when thresholds are reached. Keeps recent interactions
intact and swaps history seamlessly between LLM turns.
"""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from kollabor_config.config_utils import resolve_global_path
from kollabor_events import EventType, Hook, HookPriority
from kollabor_events.data_models import ConversationMessage
from kollabor_events.models import CommandCategory, CommandDefinition
from kollabor_plugins import BasePlugin

logger = logging.getLogger(__name__)

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
                    "help": "Compact at this fraction of context window (0.75 = 75%)",
                },
                {
                    "type": "slider",
                    "label": "Token Threshold (K) Override",
                    "config_path": "plugins.context_compaction.token_threshold_k",
                    "min_value": 0,
                    "max_value": 2000,
                    "step": 10,
                    "help": "Manual override (0 = auto-detect from provider)",
                },
                {
                    "type": "slider",
                    "label": "Min Human Turns",
                    "config_path": "plugins.context_compaction.min_human_turns",
                    "min_value": 1,
                    "max_value": 20,
                    "step": 1,
                    "help": "Minimum human messages before compaction can fire",
                },
                {
                    "type": "slider",
                    "label": "Keep Recent",
                    "config_path": "plugins.context_compaction.keep_recent",
                    "min_value": 1,
                    "max_value": 30,
                    "step": 1,
                    "help": "Minimum recent messages to preserve after compaction",
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
            ],
        }

    def __init__(self, name: str, event_bus, renderer, config) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config

        # State
        self._compaction_round: int = 0
        self._compaction_in_progress: bool = False
        self._pending_compaction: Optional[List[ConversationMessage]] = None
        self._pre_compaction_len: int = 0
        self._pending_session_id: Optional[str] = None
        self._consecutive_failures: int = 0
        self._disabled_for_session: bool = False
        self._compaction_task: Optional[asyncio.Task] = None

        # References set during initialize()
        self._llm_service = None
        self._conversation_logger = None
        self._conversation_manager = None
        self._profile_manager = None
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

        # Register /compact command
        self._register_compact_command()

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

        pre_hook = Hook(
            name="context_compaction_pre",
            plugin_name=self.name,
            event_type=EventType.LLM_REQUEST_PRE,
            priority=HookPriority.PREPROCESSING.value,
            callback=self._apply_pending_compaction,
        )
        await self.event_bus.register_hook(pre_hook)

        logger.info("Context compaction hooks registered")

    async def shutdown(self) -> None:
        if self._compaction_task and not self._compaction_task.done():
            self._compaction_task.cancel()
            try:
                await self._compaction_task
            except asyncio.CancelledError:
                pass
        self._pending_compaction = None
        self._pending_session_id = None
        logger.info("Context compaction plugin shutdown")

    # ------------------------------------------------------------------
    # Status widget
    # ------------------------------------------------------------------

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
        prompt_tokens = self._get_prompt_tokens()
        # Attach mode fallback: client-side LLMService has no stats,
        # read from daemon state snapshot instead
        if prompt_tokens == 0 and context and getattr(context, "remote_state", None):
            prompt_tokens = context.remote_state.get("input_tokens", 0)
        threshold = self._get_token_threshold()
        token_k = f"{prompt_tokens / 1000:.0f}K" if prompt_tokens else "0"
        thresh_k = f"{threshold / 1000:.0f}K"

        if self._disabled_for_session:
            return "ctx: off"
        if self._compaction_in_progress:
            return "ctx: compacting..."
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
            description="Show compaction profile and trigger info",
            plugin_name=self.name,
            category=CommandCategory.SYSTEM,
            handler=self._handle_compact_command,
        )
        self._command_registry.register_command(cmd)

    async def _handle_compact_command(self, command) -> str:
        """Handle /compact -- show active compaction config."""
        args = getattr(command, "args", []) or []
        if args and str(args[0]).lower() == "preview":
            return self._build_compact_preview()

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

        # Current state
        prompt_tokens = self._get_prompt_tokens()
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

        # History stats
        history = self._get_conversation_history()
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
            f"  threshold:      {threshold // 1000}K [{source}]",
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

        return "\n".join(lines)

    def _build_compact_preview(self) -> str:
        """Build a non-mutating preview of what compaction would affect."""
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

        prompt_tokens = self._get_prompt_tokens()
        estimated_removed = int(
            prompt_tokens * (len(summarizable) / max(1, len(history)))
        )

        lines = [
            "compact preview:",
            f"  messages:       {len(history)}",
            f"  preserved:      {len(to_keep)} recent",
            f"  removed:        {len(summarizable)} summarizable",
            f"  pinned:         {len(preserved)} hub/task",
            f"  token delta:    ~{estimated_removed // 1000}K removed",
            "",
            "  apply:          /compact apply",
        ]
        return "\n".join(lines)

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
            if input_tokens > 0:
                return input_tokens

        # Fallback: api_service.last_token_usage
        api_service = getattr(self._llm_service, "api_service", None)
        if api_service:
            usage = api_service.get_last_token_usage()
            if usage:
                return usage.get("prompt_tokens", 0)

        return 0

    def _estimate_history_tokens(
        self, history: List[ConversationMessage]
    ) -> int:
        """Estimate tokens for the conversation about to be sent.

        The API-reported count lags by one request and reads 0 after a
        context-window overflow, so on its own it lets an over-budget
        conversation slip through without ever triggering compaction.
        Estimating from current message content (~3 chars/token, deliberately
        conservative) lets compaction react to a single turn that just added a
        large payload, before that oversized request goes out.
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
            window = getattr(cfg, "context_window", None)
            if window:
                return int(window)
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

    def _get_token_threshold(self) -> int:
        """Get the token threshold for triggering compaction.

        Resolution order:
          1. Manual override: token_threshold_k > 0 in config
          2. Auto-detect: context_window * compaction_ratio
          3. Hardcoded fallback: 100K
        """
        # Manual override takes precedence
        token_k = int(
            self.config.get("plugins.context_compaction.token_threshold_k", 0)
        )
        if token_k > 0:
            return token_k * 1000

        # Auto-detect from provider
        context_window = self._resolve_context_window()
        if context_window:
            ratio = float(
                self.config.get("plugins.context_compaction.compaction_ratio", 0.75)
            )
            ratio = max(0.50, min(0.95, ratio))
            return int(context_window * ratio)

        # Fallback if provider can't be resolved
        return 100_000

    def _should_compact(self, history: List[ConversationMessage]) -> bool:
        """Determine if compaction should trigger.

        Uses a dual-gate approach:
            1. Token gate: prompt_tokens exceeds token_threshold_k
            2. Turn gate: at least min_human_turns have occurred

        Both gates must pass. This prevents compaction from firing on
        a 2-message conversation with a huge system prompt, and also
        prevents token-blind compaction from agent chatter.
        """
        # Gate 1: token count. Use the larger of the API-reported count
        # (accurate, but from the PREVIOUS request and 0 after an overflow) and
        # a fresh estimate of the history about to be sent. The estimate is what
        # lets compaction react to a turn that just added a large payload,
        # before that oversized request goes out.
        prompt_tokens = max(
            self._get_prompt_tokens(), self._estimate_history_tokens(history)
        )
        token_threshold = self._get_token_threshold()
        if prompt_tokens < token_threshold:
            return False

        # Gate 2: minimum human turns (safety floor)
        min_turns = int(
            self.config.get("plugins.context_compaction.min_human_turns", 6)
        )
        human_turns = sum(
            1
            for msg in history
            if msg.role == "user" and not msg.metadata.get("hub_message")
        )
        if human_turns < min_turns:
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
            f"Compaction triggered: {prompt_tokens} tokens >= {token_threshold} "
            f"[{source}] ({human_turns} human turns, min {min_turns}) "
            f"(round {self._compaction_round})"
        )
        return True

    async def _on_llm_turn_complete(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """LLM_REQUEST_POST: check if compaction threshold reached."""
        if self._disabled_for_session or self._compaction_in_progress:
            return data

        if not self.config.get("plugins.context_compaction.enabled", True):
            return data

        history = self._get_conversation_history()
        if not history:
            return data

        if self._should_compact(history):
            self._compaction_in_progress = True
            self._compaction_task = asyncio.ensure_future(self._run_compaction())

        self._maybe_emit_budget_hud(history)

        return data

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

    async def _apply_pending_compaction(
        self, data: Dict[str, Any], event
    ) -> Dict[str, Any]:
        """LLM_REQUEST_PRE: apply staged compaction atomically."""
        if self._pending_compaction is None:
            return data

        history = self._get_conversation_history()
        if not history:
            self._pending_compaction = None
            self._pending_session_id = None
            self._pre_compaction_len = 0
            return data

        pending_session_id = self._pending_session_id
        current_session_id = self._get_current_session_id()
        if pending_session_id and current_session_id != pending_session_id:
            logger.info("Discarded staged compaction due to session change")
            self._pending_compaction = None
            self._pending_session_id = None
            self._pre_compaction_len = 0
            return data

        if len(history) < self._pre_compaction_len:
            logger.info(
                "Discarded staged compaction because conversation history was reset"
            )
            self._pending_compaction = None
            self._pending_session_id = None
            self._pre_compaction_len = 0
            return data

        # Capture any messages added during background compaction
        new_msgs = history[self._pre_compaction_len :]

        # Atomic swap via slice assignment
        pre_len = len(history)
        history[:] = self._pending_compaction + new_msgs
        post_len = len(history)

        logger.info(
            f"Applied compaction round {self._compaction_round}: "
            f"{pre_len} -> {post_len} messages"
        )

        # Sync conversation_manager if available
        if self._conversation_manager:
            self._conversation_manager._update_context_window()

        self._pending_compaction = None
        self._pending_session_id = None
        self._pre_compaction_len = 0

        return data

    # ------------------------------------------------------------------
    # Core compaction logic
    # ------------------------------------------------------------------

    def _get_conversation_history(self) -> Optional[List[ConversationMessage]]:
        """Get the live conversation_history list from llm_service."""
        if self._llm_service and hasattr(self._llm_service, "conversation_history"):
            return self._llm_service.conversation_history
        return None

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
        # Content-pattern fallback for messages injected before metadata tagging
        if msg.role == "user" and msg.content.startswith("[hub channel:"):
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
            # Fallback: content-based detection for untagged messages
            if not (msg.role == "user" and msg.content.startswith("[hub channel:")):
                return False

        content_lower = msg.content.lower()

        # Observed messages (not intended for us) are never tasks
        if "you do not need to respond" in content_lower:
            return False

        # Check for task indicators
        for indicator in _TASK_INDICATORS:
            if indicator in content_lower:
                return True

        # If the message is directly addressed hub message (hub_is_intended)
        # and has substantial content, treat as task to be safe
        if msg.metadata.get("hub_is_intended") and len(msg.content) > 50:
            return True

        return False

    def _extract_preservable_messages(
        self, messages: List[ConversationMessage]
    ) -> tuple:
        """Separate messages into preservable (tasks) and summarizable.

        Returns:
            (preserved, summarizable): Two lists. Preserved messages are
            hub task assignments that must survive compaction verbatim.
            Summarizable is everything else.
        """
        preserved: List[ConversationMessage] = []
        summarizable: List[ConversationMessage] = []

        for msg in messages:
            if self._is_hub_task(msg):
                preserved.append(msg)
                logger.debug(f"Preserving hub task message: {msg.content[:80]}...")
            else:
                summarizable.append(msg)

        if preserved:
            logger.info(
                f"Task extraction: {len(preserved)} task messages preserved, "
                f"{len(summarizable)} messages will be summarized"
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
        try:
            history = self._get_conversation_history()
            if not history:
                return
            history_snapshot = list(history)
            snapshot_len = len(history_snapshot)
            snapshot_session_id = self._get_current_session_id()

            keep_recent_cfg = self.config.get(
                "plugins.context_compaction.keep_recent", 8
            )
            # Scale keep_recent up for large context windows so agents
            # don't lose thread with 1M-token models keeping only 8 messages.
            context_window = self._resolve_context_window()
            if context_window:
                # ~1 extra message per 100K of context window
                auto_keep = max(keep_recent_cfg, context_window // 100_000)
            else:
                auto_keep = keep_recent_cfg
            keep_recent = auto_keep
            max_summary_tokens = self.config.get(
                "plugins.context_compaction.max_summary_tokens", 2000
            )

            # Identify boundaries
            split_point = self._find_split_point(history_snapshot, keep_recent)
            if split_point <= 1:
                # Nothing meaningful to summarize
                logger.info("Not enough messages to summarize, skipping")
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
                    return

            # Build compacted history:
            # system + summary (if any) + ledger decisions + tasks + to_keep
            compacted = self._build_compacted_history(
                system_msg,
                summary_text or "",
                to_keep,
                preserved_tasks,
                ledger_handled=ledger_handled,
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
            self._compaction_round += 1
            self._consecutive_failures = 0

            # Log compaction event
            await self._log_compaction_event(
                messages_summarized=len(summarizable),
                messages_kept=len(to_keep),
                summary_length=len(summary_text),
                pre_count=snapshot_len,
                post_count=len(compacted),
                tasks_preserved=len(preserved_tasks),
            )

            logger.info(
                f"Compaction round {self._compaction_round} staged: "
                f"summarized {len(summarizable)} msgs, "
                f"preserved {len(preserved_tasks)} task(s), "
                f"keeping {len(to_keep)}"
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
            logger.info("Compaction cancelled")
            raise
        except Exception as e:
            logger.error(f"Compaction failed: {e}", exc_info=True)
            self._consecutive_failures += 1
            self._check_failure_threshold()
        finally:
            self._compaction_in_progress = False

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
            response = await provider.call(messages=messages)

            # UnifiedResponse has get_text_content() helper
            if hasattr(response, "get_text_content"):
                return response.get_text_content()

            # Fallback for unexpected return types
            if isinstance(response, str):
                return response

            return str(response)

        except Exception as e:
            logger.error(f"Summarization LLM call failed: {e}", exc_info=True)
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
          - pending: elide with a size marker
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

            if has_keep:
                # Preserve verbatim
                ledger_handled.append(msg)
            elif has_evicted:
                # Already rewritten, keep the stub
                ledger_handled.append(msg)
            elif has_summary:
                # Replace with agent-written summary
                summary_parts = []
                for e in entries:
                    if e.decision == "summary" and e.decision_body:
                        summary_parts.append(
                            f"[{e.ctx_id} summary] {e.decision_body}"
                        )
                new_msg = ConversationMessage(
                    role=msg.role,
                    content="\n".join(summary_parts),
                    metadata={
                        "compacted_from": ctx_ids,
                        "ledger_decision": "summary",
                    },
                )
                ledger_handled.append(new_msg)
            else:
                # Pending — elide with marker
                markers = []
                for e in entries:
                    markers.append(
                        f"[{e.ctx_id} {e.kind} {e.label}, "
                        f"{e.size_bytes // 1024}KB, elided]"
                    )
                new_msg = ConversationMessage(
                    role=msg.role,
                    content="\n".join(markers),
                    metadata={
                        "compacted_from": ctx_ids,
                        "ledger_decision": "elided",
                        "elided": True,
                    },
                )
                ledger_handled.append(new_msg)

        if ledger_handled:
            logger.info(
                f"Ledger partition: {len(ledger_handled)} handled, "
                f"{len(untracked)} untracked"
            )

        return ledger_handled, untracked


    def _build_compacted_history(
        self,
        system_msg: Optional[ConversationMessage],
        summary_text: str,
        to_keep: List[ConversationMessage],
        preserved_tasks: Optional[List[ConversationMessage]] = None,
        ledger_handled: Optional[List[ConversationMessage]] = None,
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
        compacted: List[ConversationMessage] = []

        if system_msg:
            compacted.append(system_msg)

        summary_content = (
            SUMMARY_INJECTION_PREFIX + summary_text + SUMMARY_INJECTION_SUFFIX
        )

        compacted.append(ConversationMessage(role="user", content=summary_content))

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

        compacted.extend(to_keep)

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
    ) -> None:
        """Append compaction record to session JSONL."""
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
        """Status line for area C showing compaction state."""
        if not self.config.get("plugins.context_compaction.enabled", True):
            return {"A": [], "B": [], "C": []}

        prompt_tokens = self._get_prompt_tokens()
        threshold = self._get_token_threshold()
        token_k = f"{prompt_tokens / 1000:.0f}K" if prompt_tokens else "0"
        thresh_k = f"{threshold / 1000:.0f}K"

        if self._compaction_in_progress:
            status = "ctx: compacting..."
        elif self._compaction_round > 0:
            status = f"ctx: r{self._compaction_round} {token_k}/{thresh_k}"
        else:
            status = f"ctx: {token_k}/{thresh_k}"

        if self._disabled_for_session:
            status += " [disabled]"

        return {"A": [], "B": [], "C": [status]}
