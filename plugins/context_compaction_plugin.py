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
from kollabor_events.models import CommandCategory, CommandDefinition, SubcommandInfo
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
                    "help": "Compact at this fraction of context window (0.75 = 75%), auto threshold capped at 272K",
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
                {
                    "type": "checkbox",
                    "label": "Ask Model First",
                    "config_path": "plugins.context_compaction.ask_model_first",
                    "help": "Ask the model what to keep before compacting; it starts compaction with <compact>",
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
        self._compaction_tasks: set[asyncio.Task] = set()

        # Model-first compaction: the model is asked what to keep and starts
        # compaction itself with <compact>notes</compact>.
        self._awaiting_model: bool = False
        self._asked_turn: int = 0
        self._turn_count: int = 0
        self._model_notes: Optional[str] = None
        self._watch_task: Optional[asyncio.Task] = None

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
            prompt_tokens = self._get_prompt_tokens()
            tokens_estimated = self._prompt_tokens_are_estimated()
        threshold = self._get_token_threshold()
        token_prefix = "~" if tokens_estimated and prompt_tokens > 0 else ""
        if not prompt_tokens:
            token_k = "0"
        elif prompt_tokens < 1000:
            token_k = f"{token_prefix}{prompt_tokens}"
        else:
            token_k = f"{token_prefix}{prompt_tokens / 1000:.0f}K"
        thresh_k = f"{threshold / 1000:.0f}K"

        if self._disabled_for_session:
            return "ctx: off"
        if self._compaction_in_progress:
            return "ctx: compacting..."
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
                SubcommandInfo("status", "", "Show compaction profile and trigger info"),
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
            if input_tokens > 0:
                return input_tokens

        # Fallback: api_service.last_token_usage
        api_service = getattr(self._llm_service, "api_service", None)
        if api_service:
            usage = api_service.get_last_token_usage()
            if usage:
                return usage.get("prompt_tokens", 0)

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
          2. Auto-detect: context_window * compaction_ratio, capped at 272K
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
            # OpenAI bills 2x/1.5x past 272K input, so never auto-wait longer.
            return min(int(context_window * ratio), AUTO_THRESHOLD_CAP)

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
    def _extract_coordination_lines(
        summarizable: List[ConversationMessage],
    ) -> List[str]:
        """Deterministically pull hub-coordination fragments from messages
        about to be summarized.

        Matches [hub channel: / [hub: blocks (including wake instructions
        and pending-reply HUD sections) anywhere in the content, extracts
        them as whole blocks. Pure string surgery — no LLM involved, no
        probability of loss. Everything else stays summarizable.
        """
        blocks: List[str] = []
        for msg in summarizable:
            content = msg.content or ""
            if "[hub channel:" not in content and "[hub:" not in content:
                continue
            # Split into blocks on the hub-channel header; each block runs
            # to the next header or end of content.
            import re

            pattern = re.compile(r"\[hub[^\]]*?\]", re.IGNORECASE)
            headers = list(pattern.finditer(content))
            for i, m in enumerate(headers):
                start = m.start()
                end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
                block = content[start:end].strip()
                if block:
                    blocks.append(block)
        return blocks

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
        self._turn_count += 1
        if self._disabled_for_session or self._compaction_in_progress:
            return data

        if not self.config.get("plugins.context_compaction.enabled", True):
            return data

        history = self._get_conversation_history()
        if not history:
            return data

        # While the model has been asked, the idle watcher owns the fallback;
        # the model normally answers with <compact> first.
        if not self._awaiting_model and self._should_compact(history):
            if self._coordination_pending():
                logger.info(
                    "Compaction deferred: coordination in flight "
                    "(queued HUD hub messages or pending hub replies)"
                )
            elif self.config.get("plugins.context_compaction.ask_model_first", True):
                await self._request_model_curation(wake=False, reason="threshold")
            else:
                self._start_compaction()

        self._maybe_emit_budget_hud(history)

        return data

    # ------------------------------------------------------------------
    # Model-first compaction
    # ------------------------------------------------------------------

    def _start_compaction(self) -> bool:
        """Spawn the background compaction run. False if one can't start."""
        if self._compaction_in_progress or self._disabled_for_session:
            return False
        self._awaiting_model = False
        self._compaction_in_progress = True
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
        keep = self._keep_recent_count()
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

    def _keep_recent_count(self) -> int:
        """Messages always kept intact. Scales with the window (~1 per 100K)
        so 1M-token models don't lose the thread keeping only 8."""
        keep = self.config.get("plugins.context_compaction.keep_recent", 8)
        window = self._resolve_context_window()
        return max(keep, window // 100_000) if window else keep

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
        """<compact>: the model is ready, keep its notes and compact now."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        tool_id = tool_data.get("id", "unknown")
        if self._compaction_in_progress:
            return ToolExecutionResult(
                tool_id=tool_id, tool_type="compact", success=False,
                error="compaction already running",
            )
        notes = tool_data.get("notes", "")
        if notes:
            self._model_notes = notes
        if not self._start_compaction():
            return ToolExecutionResult(
                tool_id=tool_id, tool_type="compact", success=False,
                error="compaction is disabled for this session",
            )
        # End the turn: the swap lands before the next request, nothing to
        # follow up on.
        return ToolExecutionResult(
            tool_id=tool_id,
            tool_type="compact",
            success=True,
            output=f"[compact] compacting, {len(notes)} chars of notes kept verbatim",
            metadata={"end_turn": True},
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
        pending_compaction = self._pending_compaction
        assert pending_compaction is not None

        # Atomic swap via slice assignment
        pre_len = len(history)
        history[:] = pending_compaction + new_msgs
        post_len = len(history)

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

            keep_recent = self._keep_recent_count()
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
            self._compaction_round += 1
            self._consecutive_failures = 0
            self._model_notes = None

            # Log compaction event (summary_text persisted for rebirth)
            await self._log_compaction_event(
                messages_summarized=len(summarizable),
                messages_kept=len(to_keep),
                summary_length=len(summary_text),
                pre_count=snapshot_len,
                post_count=len(compacted),
                tasks_preserved=len(preserved_tasks),
                summary_text=summary_text or "",
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

        compacted.extend(to_keep)

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
