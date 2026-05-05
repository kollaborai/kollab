"""Core status widgets for the Kollab status area.

Provides the built-in widgets that display system information like
current directory, profile, model, status, stats, agent, and skills.
"""

import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from kollabor_tui.design_system import T

from .altview_widget import register_altview_widget
from .core_widget_modals import cwd_directory_modal, git_branch_modal, profile_switcher_modal, tmux_session_modal
from .system_monitor import render_sysmon
from .utils import fg as _fg
from .utils import middle_truncate as _middle_truncate
from .utils import truncate as _truncate
from .widget_registry import StatusWidgetRegistry, WidgetCategory

logger = logging.getLogger(__name__)


def _smart_path_compress(path_str: str, max_width: int) -> str:
    """Intelligently compress a path to fit within max_width.

    Compression strategy uses middle-truncation to keep both start and end visible:
    1. If fits: return as-is
    2. Compress middle directories: ~/dev/foo/bar/baz -> ~/d/f/bar/baz
    3. Middle-truncate final component: ~/d/kollab.. or ~/d..status-widgets
    4. Minimal: keep meaningful start and end

    Examples at different widths:
    - Full (40): ~/dev/kollab-status-widgets
    - Medium (25): ~/d/kollab-status..
    - Compact (18): ~/d..status-widgets
    - Minimal (12): ~/..widgets

    Args:
        path_str: Path string (may start with ~)
        max_width: Maximum characters allowed

    Returns:
        Compressed path that fits within max_width
    """
    if len(path_str) <= max_width:
        return path_str

    if max_width < 5:
        return path_str[:max_width]

    # Handle ~ prefix
    if path_str.startswith("~/"):
        prefix = "~/"
        rest = path_str[2:]
    elif path_str.startswith("/"):
        prefix = "/"
        rest = path_str[1:]
    else:
        prefix = ""
        rest = path_str

    parts = rest.split("/")

    # If only one part (no directory structure), use middle truncation
    if len(parts) <= 1:
        return _middle_truncate(path_str, max_width)

    last_part = parts[-1]
    middle_parts = parts[:-1]

    # Strategy 1: Compress directories to first char only
    # ~/dev/kollabor -> ~/d/kollabor
    compressed_dirs = "/".join(p[0] if p else "" for p in middle_parts)
    result = f"{prefix}{compressed_dirs}/{last_part}"
    if len(result) <= max_width:
        return result

    # Strategy 2: Use middle-truncation on last part
    # ~/d/kollab-status-widgets -> ~/d/koll..widgets
    available_for_last = max_width - len(prefix) - len(compressed_dirs) - 1  # -1 for /
    if available_for_last >= 8:
        truncated_last = _middle_truncate(last_part, available_for_last)
        return f"{prefix}{compressed_dirs}/{truncated_last}"

    # Strategy 3: Skip directories entirely, middle-truncate the path
    # ~/d..status-widgets
    available = max_width - len(prefix)
    if available >= 6:
        return prefix + _middle_truncate(rest.replace("/", "/"), available)

    # Strategy 4: Minimal - just end of last part
    # ~/..widgets
    if max_width >= 8:
        end_chars = max_width - len(prefix) - 2  # -2 for ".."
        return f"{prefix}..{last_part[-end_chars:]}"

    return path_str[:max_width]


class WidgetContext:
    """Context object passed to widget render functions.

    Provides access to services and configuration needed to render widgets.
    """

    def __init__(
        self,
        llm_service: Any = None,
        profile_manager: Any = None,
        agent_manager: Any = None,
        config: Any = None,
        tmux_plugin: Any = None,
        background_tasks_plugin: Any = None,
        navigation_state: Any = None,
        layout_manager: Any = None,
        event_bus: Any = None,
    ):
        self.llm_service = llm_service
        self.profile_manager = profile_manager
        self.agent_manager = agent_manager
        self.config = config
        self.tmux_plugin = tmux_plugin
        self.background_tasks_plugin = background_tasks_plugin
        self.navigation_state = navigation_state
        self.layout_manager = layout_manager
        self.event_bus = event_bus
        self.widget_config: Any = None
        self.widget_id: str | None = None
        # Remote state from daemon (populated in attach mode)
        self.remote_state: dict = {}


# =============================================================================
# CORE WIDGET RENDER FUNCTIONS
# =============================================================================


def render_cwd(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render current working directory widget.

    Uses smart path compression to fit within width:
    - Full path if fits
    - Compress middle dirs: ~/d/k/kollab-status-widgets
    - Minimal: ~/..kollab-status-widgets
    """
    try:
        cwd = Path.cwd()
        home = Path.home()

        # Build full relative path
        if cwd == home:
            display = "~"
        elif cwd.is_relative_to(home):
            display = f"~/{cwd.relative_to(home)}"
        else:
            display = str(cwd)

        # "cwd " prefix takes 4 chars
        prefix_len = 4
        available = max(5, width - prefix_len)

        # Use smart compression to fit available width
        compressed = _smart_path_compress(display, available)

        icon = _fg("cwd", T().ai_tag)
        text = _fg(compressed, T().text)
        return f"{icon} {text}"
    except Exception as e:
        logger.error(f"cwd widget error: {e}")
        return _fg("cwd ?", T().text_dim)


def render_profile(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render active LLM profile widget.

    Width-aware formats:
    - Full (10+ chars): "⌘ default"
    - Compact (6+ chars): "⌘ def.."
    - Minimal (4+ chars): "⌘ d.."

    Prefers ctx.remote_state (populated by WidgetStateRefresher from the
    daemon's StateService in attach mode) so the widget shows daemon state,
    not the client's shadow ProfileManager. Falls back to the local
    profile_manager when remote_state has nothing useful.
    """
    try:
        name = ""
        # Prefer daemon-sourced state so attach clients display the real
        # active profile instead of the client's shadow default.
        if ctx and ctx.remote_state:
            name = ctx.remote_state.get("profile_name", "") or ""
        if not name and ctx and ctx.profile_manager:
            profile = ctx.profile_manager.get_active_profile()
            if profile:
                name = profile.name
        if not name:
            name = "default"

        icon = _fg("\u2318", T().secondary[0])  # Command symbol

        # "⌘ " takes 2 chars
        available = max(1, width - 2)

        if len(name) <= available:
            text = _fg(name, T().text)
        else:
            # Use middle truncation for profile names
            text = _fg(_middle_truncate(name, available), T().text)

        return f"{icon} {text}"
    except Exception as e:
        logger.error(f"profile widget error: {e}")
        return _fg("\u2318 ?", T().text_dim)


def render_model(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render model name widget.

    Prefers ctx.remote_state["model"] (populated by WidgetStateRefresher
    from the daemon's StateService in attach mode) so the widget shows
    the daemon's active model, not the client's shadow profile. Falls
    back to the local profile_manager.
    """
    try:
        model = ""
        if ctx and ctx.remote_state:
            model = ctx.remote_state.get("model", "") or ""
        if not model and ctx and ctx.profile_manager:
            profile = ctx.profile_manager.get_active_profile()
            if profile:
                model = profile.get_model() or ""
        if not model:
            model = "unknown"

        text = _fg(_truncate(model, width), T().text)
        return text
    except Exception as e:
        logger.error(f"model widget error: {e}")
        return _fg("model?", T().text_dim)


def render_endpoint(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render API endpoint widget.

    Prefers ctx.remote_state["endpoint"] + ["provider"] (populated by
    WidgetStateRefresher from the daemon's StateService in attach mode)
    so the widget reflects the daemon's active endpoint, not the
    client's shadow profile. Falls back to the local profile_manager.
    """
    try:
        endpoint = ""
        api_url = ""
        provider = ""

        # Prefer daemon-sourced state in attach mode.
        if ctx and ctx.remote_state:
            api_url = ctx.remote_state.get("endpoint", "") or ""
            provider = ctx.remote_state.get("provider", "") or ""

        # Fall back to local profile_manager if remote_state didn't
        # surface anything (local mode or refresher not yet primed).
        if not api_url and not provider and ctx and ctx.profile_manager:
            profile = ctx.profile_manager.get_active_profile()
            if profile:
                api_url = profile.get_endpoint() or ""
                provider = getattr(profile, "provider", "") or ""

        if api_url:
            try:
                endpoint = urlparse(api_url).hostname or "local"
            except Exception as e:
                logger.warning(f"Failed to parse endpoint URL: {e}")
                endpoint = "local"
        else:
            # No explicit base_url - show provider's default endpoint
            provider_hosts = {
                "openai": "api.openai.com",
                "openai_responses": "api.openai.com",
                "anthropic": "api.anthropic.com",
                "gemini": "generativelanguage.googleapis.com",
                "openrouter": "openrouter.ai",
            }
            endpoint = provider_hosts.get(provider, "local")

        icon = _fg("@", T().text_dim)
        text = _fg(_truncate(endpoint, width - 2), T().text)
        return f"{icon} {text}"
    except Exception as e:
        logger.error(f"endpoint widget error: {e}")
        return _fg("@ ?", T().text_dim)


def render_status(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render status indicator widget (Ready/Working).

    Width-aware formats:
    - Full (7+ chars): "* Ready" or "* Working"
    - Compact (<7 chars): "*R" or "*W"
    """
    try:
        is_processing = False
        if ctx and ctx.llm_service:
            is_processing = ctx.llm_service.is_processing
        if not is_processing and ctx and ctx.remote_state:
            is_processing = ctx.remote_state.get("is_processing", False)

        # "* Ready" = 7 chars, "* Working" = 9 chars
        # Use 7 as threshold since "Ready" is the common state
        if width >= 7:
            # Full format
            if is_processing:
                icon = _fg("*", T().warning[0])
                text = _fg("Working", T().warning[0])
            else:
                icon = _fg("*", T().ai_tag)
                text = _fg("Ready", T().ai_tag)
            return f"{icon} {text}"
        else:
            # Compact format: just icon with first letter
            if is_processing:
                return _fg("*W", T().warning[0])
            else:
                return _fg("*R", T().ai_tag)
    except Exception as e:
        logger.error(f"status widget error: {e}")
        return _fg("*?", T().text_dim)


def render_stats(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render message/token/cost stats widget.

    Width-aware formats (dynamic thresholds based on actual content length):
    - Extended: "0 msg | 0 tok | $0.00 | cache 0"
    - Full:     "0 msg | 0 tok | $0.00"
    - Compact:  "0m|0t|$0"
    - Minimal:  "$0.42"
    """
    try:
        msgs = 0
        tokens = 0
        cache_read = 0
        cost = 0.0

        if ctx and ctx.llm_service and hasattr(ctx.llm_service, "session_stats"):
            stats = ctx.llm_service.session_stats
            msgs = stats.get("messages", 0)
            tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
            cache_read = stats.get("cache_read_tokens", 0)
            cost = stats.get("total_cost_usd", 0.0)

        # Attach mode fallback: read from daemon state snapshot
        if msgs == 0 and tokens == 0 and ctx and ctx.remote_state:
            rs = ctx.remote_state
            msgs = rs.get("messages", 0)
            tokens = rs.get("input_tokens", 0) + rs.get("output_tokens", 0)
            cache_read = rs.get("cache_read_tokens", 0)
            cost = rs.get("total_cost_usd", 0.0)

        def _fmt(n: int) -> str:
            if n < 1000:
                return f"{n}"
            if n < 1_000_000:
                return f"{n/1000:.1f}K"
            return f"{n/1_000_000:.1f}M"

        def _fmt_short(n: int) -> str:
            if n < 1000:
                return f"{n}"
            if n < 1_000_000:
                return f"{n//1000}K"
            return f"{n//1_000_000}M"

        def _fmt_cost(c: float) -> str:
            if c < 0.01:
                return "$0.00"
            if c < 100:
                return f"${c:.2f}"
            return f"${c:.0f}"

        def _fmt_cost_short(c: float) -> str:
            if c < 0.01:
                return "$0"
            if c < 1:
                return f"${c:.1f}"
            return f"${c:.0f}"

        token_full = _fmt(tokens)
        token_short = _fmt_short(tokens)
        cache_full = _fmt(cache_read)
        cost_full = _fmt_cost(cost)
        cost_short = _fmt_cost_short(cost)

        cost_color = T().warning[0] if cost > 1 else T().text_dim

        # Build format strings and pick the best fit
        extended = f"{msgs} msg | {token_full} tok | {cost_full} | \u27f3 {cache_full}"
        full = f"{msgs} msg | {token_full} tok | {cost_full}"
        compact = f"{msgs}m|{token_short}t|{cost_short}"
        minimal = cost_full

        if cache_read > 0 and width >= len(extended):
            text = (
                _fg(f"{msgs} msg", T().text_dim)
                + _fg(" | ", T().text_dim)
                + _fg(f"{token_full} tok", T().text_dim)
                + _fg(" | ", T().text_dim)
                + _fg(cost_full, cost_color)
                + _fg(" | ", T().text_dim)
                + _fg(
                    f"\u27f3 {cache_full}",
                    T().success[0] if hasattr(T(), "success") else T().text_dim,
                )
            )
        elif width >= len(full):
            text = (
                _fg(f"{msgs} msg", T().text_dim)
                + _fg(" | ", T().text_dim)
                + _fg(f"{token_full} tok", T().text_dim)
                + _fg(" | ", T().text_dim)
                + _fg(cost_full, cost_color)
            )
        elif width >= len(compact):
            text = (
                _fg(f"{msgs}m", T().text_dim)
                + _fg("|", T().text_dim)
                + _fg(f"{token_short}t", T().text_dim)
                + _fg("|", T().text_dim)
                + _fg(cost_short, cost_color)
            )
        else:
            text = _fg(minimal, cost_color)

        return text
    except Exception as e:
        logger.error(f"stats widget error: {e}")
        return _fg("0m", T().text_dim)


def render_agent(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render active agent widget.

    Width-aware: uses middle truncation for long agent names.
    """
    try:
        agent_name = None
        if ctx and ctx.agent_manager:
            agent = ctx.agent_manager.get_active_agent()
            if agent:
                agent_name = agent.name

        # Attach mode fallback
        if not agent_name and ctx and ctx.remote_state:
            agent_name = ctx.remote_state.get("agent")

        if not agent_name:
            return ""  # Return empty if no agent active

        icon = _fg("\u2301", T().thinking_tag)  # Electric arrow

        # "⌁ " takes 2 chars
        available = max(1, width - 2)

        if len(agent_name) <= available:
            text = _fg(agent_name, T().text)
        else:
            text = _fg(_middle_truncate(agent_name, available), T().text)

        return f"{icon} {text}"
    except Exception as e:
        logger.error(f"agent widget error: {e}")
        return _fg("\u2301 ?", T().text_dim)


def render_skills(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render active skills widget."""
    try:
        # Hidden skills that shouldn't be shown
        hidden_skills = {"system_prompt"}

        active_skills = []
        total_skills = 0

        if ctx and ctx.agent_manager:
            agent = ctx.agent_manager.get_active_agent()
            if agent:
                all_skills = [
                    s.name for s in agent.list_skills() if s.name not in hidden_skills
                ]
                active_set = set(agent.active_skills) - hidden_skills
                active_skills = [s for s in all_skills if s in active_set]
                total_skills = len(all_skills)

        # Attach mode fallback
        if not active_skills and total_skills == 0 and ctx and ctx.remote_state:
            skills_str = ctx.remote_state.get("skills", "")
            if skills_str:
                return _fg(skills_str, T().text_dim)

        if not active_skills and total_skills == 0:
            return ""  # Return empty if no agent/skills

        # Build skills display
        if active_skills:
            # Show up to 2 active skills
            shown = active_skills[:2]
            skill_text = " ".join([_fg(s, T().text) for s in shown])
            remaining = total_skills - len(shown)
            if remaining > 0:
                skill_text += " " + _fg(f"+{remaining}", T().text_dim)
        else:
            skill_text = _fg("no-skill", T().text_dim)

        return skill_text
    except Exception as e:
        logger.error(f"skills widget error: {e}")
        return _fg("skills?", T().text_dim)


def render_hub(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render hub identity and peer count."""
    try:
        hub = None
        if ctx and ctx.event_bus:
            try:
                hub = ctx.event_bus.get_service("hub_plugin")
            except Exception:
                pass

        if hub and getattr(hub, "_identity", None) and getattr(hub, "_started", False):
            identity_obj = hub._identity
            identity_name = identity_obj.identity or "?"
            peers = len(getattr(hub, "_roster", []))
            is_coord = identity_obj.is_coordinator

            icon = _fg("\u25c8", T().success[0])  # ◈ diamond
            name = _fg(identity_name, T().text)
            coord_mark = _fg("*", T().warning[0]) if is_coord else ""
            peer_text = _fg(f"+{peers}", T().text_dim)

            return f"{icon} {name}{coord_mark} {peer_text}"

        # Attach mode fallback: use daemon state snapshot (periodic)
        # or attach_hub_info (one-time from ack)
        if ctx and ctx.remote_state:
            rs = ctx.remote_state
            identity_name = rs.get("hub_identity", "")
            if identity_name:
                is_coord = rs.get("hub_is_coordinator", False)
                peers = rs.get("hub_peers", 0)
                icon = _fg("\u25c8", T().success[0])
                name = _fg(identity_name, T().text)
                coord_mark = _fg("*", T().warning[0]) if is_coord else ""
                peer_text = _fg(f"+{peers}", T().text_dim)
                return f"{icon} {name}{coord_mark} {peer_text}"

        if ctx and ctx.event_bus:
            try:
                info = ctx.event_bus.get_service("attach_hub_info")
                if info:
                    identity_name = info.get("identity", "?")
                    is_coord = info.get("is_coordinator", False)
                    icon = _fg("\u25c8", T().success[0])
                    name = _fg(identity_name, T().text)
                    coord_mark = _fg("*", T().warning[0]) if is_coord else ""
                    return f"{icon} {name}{coord_mark}"
            except Exception:
                pass

        return ""
    except Exception:
        return ""


def render_tasks(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render task info widget (pending tasks, current task)."""
    try:
        pending = 0
        current_task = None

        # Future: integrate with task manager
        # For now, return minimal info
        if pending == 0 and current_task is None:
            return _fg("\u2237 0 pending", T().text_dim)  # Four dots

        icon = _fg("\u2237", T().ai_tag)
        if current_task:
            text = _fg(_truncate(current_task, width - 3), T().text)
        else:
            text = _fg(f"{pending} pending", T().text)

        return f"{icon} {text}"
    except Exception as e:
        logger.error(f"tasks widget error: {e}")
        return _fg("\u2237 ?", T().text_dim)


def render_tmux(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render tmux sessions count widget."""
    try:
        session_count = 0

        if ctx and ctx.tmux_plugin:
            sessions = getattr(ctx.tmux_plugin, "sessions", {})
            session_count = len(sessions)

        # Attach mode fallback
        if session_count == 0 and ctx and ctx.remote_state:
            session_count = ctx.remote_state.get("tmux_sessions", 0)

        if session_count == 0:
            return _fg("0 term", T().text_dim)

        text = _fg(f"{session_count} term", T().text)
        return text
    except Exception as e:
        logger.error(f"terminal widget error: {e}")
        return _fg("? term", T().text_dim)


def render_bg_tasks(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render background tasks count widget."""
    try:
        bg_count = 0

        if ctx and ctx.background_tasks_plugin:
            registry = getattr(ctx.background_tasks_plugin, "registry", None)
            if registry and hasattr(registry, "count_running"):
                bg_count = registry.count_running()

        # Attach mode fallback
        if bg_count == 0 and ctx and ctx.remote_state:
            bg_count = ctx.remote_state.get("bg_tasks", 0)

        if bg_count == 0:
            return _fg("0 bg", T().text_dim)

        text = _fg(f"{bg_count} bg", T().ai_tag)
        return text
    except Exception as e:
        logger.error(f"bg_tasks widget error: {e}")
        return _fg("? bg", T().text_dim)


def render_clock(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render current time widget."""
    try:
        from datetime import datetime

        now = datetime.now()
        time_str = now.strftime("%H:%M:%S")
        return _fg(time_str, T().text_dim)
    except Exception as e:
        logger.error(f"clock widget error: {e}")
        return _fg("--:--:--", T().text_dim)


def render_label(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render label widget with editable text.

    Text is read from persisted widget_config (set by layout_renderer).
    Edit by navigating to label and pressing Enter.
    """
    try:
        # Get text from persisted widget_config (set by layout_renderer before render)
        text = "Label"
        if ctx and hasattr(ctx, "widget_config") and ctx.widget_config:
            text = ctx.widget_config.get("text", text)

        # Truncate to fit width
        return _fg(_truncate(text, width), T().text)
    except Exception as e:
        logger.error(f"label widget error: {e}")
        return _fg("Label", T().text_dim)


async def activate_label_edit(widget_id: str, ctx: Optional[WidgetContext]) -> dict:
    """Activate inline text editor for label widget.

    Returns editor config for InlineTextEditor.
    Persists text to layout config file via layout_manager.
    """
    # Get current position from navigation state
    row_id = None
    widget_index = None
    layout_manager = None

    if ctx and hasattr(ctx, "navigation_state") and ctx.navigation_state:
        nav_state = ctx.navigation_state
        row_id = getattr(nav_state, "selected_row", None)
        widget_index = getattr(nav_state, "selected_widget_index", None)

    if ctx and hasattr(ctx, "layout_manager") and ctx.layout_manager:
        layout_manager = ctx.layout_manager

    # Get current text from widget config (persisted)
    # Row IDs in layout are 1-indexed, selected_row is 0-indexed
    current_text = "Label"
    if layout_manager and row_id is not None and widget_index is not None:
        actual_row_id = row_id + 1
        current_text = layout_manager.get_widget_config(
            actual_row_id, widget_index, "text", "Label"
        )

    async def on_save(new_text: str) -> None:
        """Save the new label text to layout config."""
        if layout_manager and row_id is not None and widget_index is not None:
            # Row IDs in layout are 1-indexed, selected_row is 0-indexed
            actual_row_id = row_id + 1
            layout_manager.update_widget_config(
                actual_row_id, widget_index, "text", new_text
            )
            layout_manager.save()
        else:
            logger.warning("Cannot persist label text - no layout_manager or position")

    return {
        "type": "text",
        "current": current_text,
        "placeholder": "Enter label text...",
        "max_length": 50,
        "on_save": on_save,
    }


def render_spacer(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render spacer widget - empty space for visual separation.

    Configurable width (default 2 chars).
    Non-interactive, purely visual.
    """
    try:
        # Just return spaces
        return " " * width
    except Exception as e:
        logger.error(f"spacer widget error: {e}")
        return " " * width


def render_divider(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render divider widget - vertical separator character.

    Uses dim text color with box-drawing character.
    Non-interactive visual separator.
    """
    try:
        # Use vertical bar character
        return _fg("│", T().text_dim)
    except Exception as e:
        logger.error(f"divider widget error: {e}")
        return _fg("|", T().text_dim)


# =============================================================================
# GIT WIDGETS (non-blocking async cache)
# =============================================================================

# Git data cache -- render path always reads this, never blocks.
# A background asyncio subprocess refreshes it every CACHE_TTL seconds.
_git_cache: dict[str, Any] = {
    "branch": None,
    "status": None,
    "timestamp": 0.0,
}
CACHE_TTL = 5.0  # seconds
_git_refresh_pending = False


async def _refresh_git_cache() -> None:
    """Refresh git cache using async subprocess (non-blocking).

    Runs git rev-parse and git status as async subprocesses so the
    event loop is never blocked. Called when TTL expires.
    """
    import asyncio
    import time

    global _git_cache, _git_refresh_pending

    if _git_refresh_pending:
        return  # Already refreshing
    _git_refresh_pending = True

    try:
        # Get current branch (async)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            if proc.returncode == 0:
                _git_cache["branch"] = stdout.decode().strip()
            else:
                _git_cache["branch"] = None
        except (asyncio.TimeoutError, FileNotFoundError):
            _git_cache["branch"] = None

        # Get git status (async)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "status",
                "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            if proc.returncode == 0:
                status_output = stdout.decode().strip()
                modified = sum(
                    1 for line in status_output.split("\n") if line and line[0] in "M"
                )
                added = sum(
                    1 for line in status_output.split("\n") if line and line[0] in "A"
                )
                deleted = sum(
                    1 for line in status_output.split("\n") if line and line[0] in "D"
                )
                _git_cache["status"] = {
                    "clean": len(status_output) == 0,
                    "modified": modified,
                    "added": added,
                    "deleted": deleted,
                    "total": len(status_output.split("\n")) if status_output else 0,
                }
            else:
                _git_cache["status"] = None
        except (asyncio.TimeoutError, FileNotFoundError):
            _git_cache["status"] = None

        _git_cache["timestamp"] = time.time()
    except Exception as e:
        logger.debug(f"Git cache refresh failed: {e}")
    finally:
        _git_refresh_pending = False


def _get_cached_git_data() -> dict:
    """Get cached git data. Schedules async refresh if TTL expired.

    Never blocks -- always returns current cache immediately.
    If cache is stale, kicks off a background refresh task.

    Returns:
        dict with keys: 'branch', 'status', 'timestamp'
    """
    import asyncio
    import time

    now = time.time()
    if now - _git_cache["timestamp"] > CACHE_TTL and not _git_refresh_pending:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_refresh_git_cache())
        except RuntimeError:
            pass  # No running loop (e.g. during tests)

    return _git_cache


def render_git_branch(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render git branch widget.

    Shows current git branch name with branch icon.
    Returns empty string if not in a git repository.

    Width-aware formats:
    - Full (12+ chars): "git: feature/branch-name"
    - Compact (8+ chars): "git: feat..name"
    - Minimal (6+ chars): "git: br.."

    Caches result for 5 seconds to avoid subprocess overhead.
    """
    try:
        git_data = _get_cached_git_data()
        branch = git_data.get("branch")

        if not branch:
            # Not in git repo or git not available
            return ""

        icon = _fg("git:", T().ai_tag)

        # Available width for branch name
        available = max(3, width - 5)  # -5 for "git: "

        if len(branch) <= available:
            text = _fg(branch, T().text)
        else:
            # Use middle truncation for branch name
            text = _fg(_middle_truncate(branch, available), T().text)

        return f"{icon} {text}"
    except Exception as e:
        logger.error(f"git-branch widget error: {e}")
        return _fg("git:?", T().text_dim)


def render_git_status(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render git status widget.

    Shows working directory clean/dirty status.
    Caches result for 5 seconds.

    Width-aware formats:
    - Full (12+ chars): "clean" or "M:3 A:1 D:0"
    - Compact (8+ chars): "clean" or "M:3 +1"
    - Minimal (4+ chars): "✓" or "M3"
    - Ultra-minimal (2 chars): "" or "*"

    The dirty indicator shows:
    - M = modified files count
    - A = added files count
    - D = deleted files count
    """
    try:
        git_data = _get_cached_git_data()
        status = git_data.get("status")

        if not status:
            # Not in git repo
            return ""

        if status["clean"]:
            # Clean state
            if width >= 5:
                return _fg("clean", T().success[0])
            elif width >= 2:
                return _fg("✓", T().success[0])
            else:
                return ""  # Too narrow for anything
        else:
            # Dirty state - show file changes
            m = status["modified"]
            a = status["added"]
            d = status["deleted"]

            if width >= 12:
                # Full format: "M:3 A:1 D:0"
                parts = []
                if m > 0:
                    parts.append(f"M:{m}")
                if a > 0:
                    parts.append(f"A:{a}")
                if d > 0:
                    parts.append(f"D:{d}")
                if not parts:
                    parts.append("dirty")
                text = " ".join(parts)
                return _fg(text, T().warning[0])
            elif width >= 8:
                # Compact format: "M:3 +1" (modified + added)
                parts = []
                if m > 0:
                    parts.append(f"M:{m}")
                if a > 0:
                    parts.append(f"+{a}")
                if d > 0:
                    parts.append(f"-{d}")
                if not parts:
                    parts.append("dirty")
                text = " ".join(parts)
                return _fg(text, T().warning[0])
            elif width >= 4:
                # Minimal format: "M3" or just "dirty"
                if m > 0:
                    return _fg(f"M{m}", T().warning[0])
                elif a > 0:
                    return _fg(f"+{a}", T().warning[0])
                else:
                    return _fg("dirty", T().warning[0])
            else:
                # Ultra-minimal: just "*"
                return _fg("*", T().warning[0])
    except Exception as e:
        logger.error(f"git-status widget error: {e}")
        return _fg("?", T().text_dim)


# =============================================================================
# TEST TOGGLE WIDGET (for verification testing)
# =============================================================================

# Test toggle widget states (for cycling)
_test_toggle_states = ["off", "on", "auto"]


def render_test_toggle(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render test toggle widget - shows current state with visual indicator."""
    try:
        # Read toggle state from widget config (persisted in layout file)
        # Must check both attribute existence AND that it's not None
        if ctx and hasattr(ctx, "widget_config") and ctx.widget_config:
            state = ctx.widget_config.get("toggle_state", "off")
        else:
            state = "off"

        # Validate state and default to "off" if unknown
        valid_states = ["off", "on", "auto"]
        if state not in valid_states:
            logger.debug(f"Invalid toggle state '{state}', defaulting to 'off'")
            state = "off"

        # Use different colors for different states (use single color from gradient)
        state_colors = {
            "off": T().text_dim,
            "on": T().success[0],  # First color from success gradient
            "auto": T().primary[2],  # Third color from primary gradient
        }

        # Use distinctive display codes for each state
        display_codes = {
            "off": "OFF",
            "on": "ON",
            "auto": "AUTO",
        }

        color = state_colors.get(state, T().text_dim)
        state_display = display_codes.get(state, "?")

        return _fg(f"TEST:{state_display}", color)
    except Exception as e:
        logger.error(f"test_toggle widget error: {e}")
        return _fg("TEST:?", T().text_dim)


async def activate_test_toggle(widget_id: str, ctx: Optional[WidgetContext]) -> dict:
    """Toggle test mode through states (off -> on -> auto -> off).

    State is persisted to widget config, so it survives application restarts.
    """
    try:
        # Get position info from interaction_data
        row_id = None
        widget_index = None
        direction = "next"

        if ctx and ctx.navigation_state:
            interaction_data = ctx.navigation_state.get_interaction_data()
            direction = interaction_data.get("direction", "next")
            row_id = interaction_data.get("row_id")
            widget_index = interaction_data.get("widget_index")

        # Get layout_manager from context
        if not ctx or not ctx.layout_manager:
            logger.error("No layout_manager in context for toggle persistence")
            return {"error": "No layout_manager available"}

        layout_manager = ctx.layout_manager

        # Get current state from widget config
        # NOTE: row_id from navigation_state is 0-indexed, but layout uses 1-indexed IDs
        if row_id is not None and widget_index is not None:
            actual_row_id = row_id + 1  # Convert 0-indexed to 1-indexed
            current_state = layout_manager.get_widget_config(
                actual_row_id, widget_index, "toggle_state", "off"
            )
        else:
            # Fallback to default if position info not available
            current_state = "off"

        # Validate current state and default to "off" if unknown
        if current_state not in _test_toggle_states:
            logger.debug(
                f"Invalid current state '{current_state}', defaulting to 'off'"
            )
            current_state = "off"

        # Cycle to next state
        current_idx = _test_toggle_states.index(current_state)
        if direction == "next":
            next_idx = (current_idx + 1) % len(_test_toggle_states)
        else:  # prev
            next_idx = (current_idx - 1) % len(_test_toggle_states)

        next_state = _test_toggle_states[next_idx]

        # Save to config if we have position info
        if row_id is not None and widget_index is not None:
            actual_row_id = row_id + 1  # Convert 0-indexed to 1-indexed
            layout_manager.update_widget_config(
                actual_row_id, widget_index, "toggle_state", next_state
            )
            layout_manager.save()
            logger.info(
                f"Test toggle widget: {next_state} (direction: {direction}) "
                f"saved to config at row {actual_row_id}, widget {widget_index}"
            )
        else:
            logger.warning(
                f"Test toggle widget: {next_state} (direction: {direction}) "
                f"NOT saved - no position info"
            )

        return {"new_state": next_state}
    except Exception as e:
        logger.error(f"Error activating test toggle: {e}", exc_info=True)
        return {"error": str(e)}


# =============================================================================
# TOKEN I/O ACTIVITY WIDGET
# =============================================================================


class TokenIOState:
    """Global state for token I/O activity widget animation.

    Tracks upload/download separately with arrow toggle animation.
    """

    def __init__(self):
        self.mode = "idle"  # idle, upload, waiting, download
        # Separate tracking for upload and download
        self.upload_tokens = 0
        self.upload_target = 0
        self.upload_display = 0
        self.download_tokens = 0
        self.download_target = 0
        self.download_display = 0
        # Animation state
        self.frame = 0
        self._last_update = 0
        # Arrow toggle for visual feedback
        self.arrow_blink = False
        self._blink_count = 0

    def start_upload(self, token_count: int):
        """Start upload animation with actual token count from API."""
        import time

        self.mode = "upload"
        self.upload_target = max(1, token_count)
        self.upload_tokens = 0
        self.upload_display = 0
        self.frame = 0
        self._last_update = time.time()
        self.arrow_blink = False
        self._blink_count = 0
        logger.debug(f"TokenIO: upload started, target={self.upload_target} tokens")

    def start_waiting(self):
        """Switch to waiting state - no arrows, shows upload total."""
        # Finalize upload
        self.upload_tokens = self.upload_target
        self.upload_display = self.upload_target
        self.mode = "waiting"
        self.arrow_blink = False
        logger.debug(f"TokenIO: waiting, uploaded {self.upload_display} tokens")

    def start_receiving(self):
        """Start receiving mode - counts real tokens as they stream in."""
        # Finalize upload display
        self.upload_tokens = self.upload_target
        self.upload_display = self.upload_target
        # Start download - no target, counts as chunks arrive
        self.mode = "download"
        self.download_target = 0
        self.download_tokens = 0
        self.download_display = 0
        self._char_buffer = 0  # Track chars for token estimation
        self.frame = 0
        self.arrow_blink = False
        logger.debug("TokenIO: receiving started")

    def add_chunk(self, char_count: int):
        """Add streaming chunk - estimates tokens from characters."""
        if self.mode != "download":
            return
        self._char_buffer += char_count
        # Estimate tokens (~4 chars per token)
        estimated = self._char_buffer // 4
        if estimated > self.download_tokens:
            self.download_tokens = estimated
            self.download_display = estimated
            self.download_target = estimated

    def finish(self, actual_tokens: int | None = None):
        """Finish and return to idle. Optionally set actual token count."""
        self.mode = "idle"
        self.upload_tokens = self.upload_target
        self.upload_display = self.upload_target
        if actual_tokens is not None:
            self.download_tokens = actual_tokens
            self.download_display = actual_tokens
            self.download_target = actual_tokens
        self.arrow_blink = False
        logger.debug(
            f"TokenIO: finished, up={self.upload_tokens}, down={self.download_tokens}"
        )

    def update(self) -> bool:
        """Update animation state. Returns True if still animating."""
        import random
        import time

        if self.mode == "idle" or self.mode == "waiting":
            return False

        now = time.time()
        dt = now - self._last_update

        # Variable update interval (30-80ms) for organic feel
        update_interval = 0.03 + random.random() * 0.05
        if dt < update_interval:
            return True

        self._last_update = now
        self.frame += 1

        # Handle arrow blink on state change
        if self.arrow_blink and self._blink_count > 0:
            if self.frame % 2 == 0:
                self._blink_count -= 1
            if self._blink_count <= 0:
                self.arrow_blink = False

        # Get current target and tokens based on mode
        if self.mode == "upload":
            target = self.upload_target
            current = self.upload_tokens
        else:
            target = self.download_target
            current = self.download_tokens

        remaining = target - current
        if remaining <= 0:
            self.finish()
            return False

        progress = current / max(1, target)

        # Variable speed with randomness
        if progress < 0.2:
            base = random.randint(1, 3)
            burst = random.randint(0, 2) if random.random() > 0.7 else 0
            increment = min(remaining, base + burst)
        elif progress < 0.8:
            base = random.randint(8, 18)
            variance = random.randint(-5, 7)
            increment = min(remaining, max(5, base + variance))
        else:
            if random.random() > 0.85:
                increment = 0
            else:
                increment = min(remaining, random.randint(1, 4))

        # Update correct counter based on mode
        if self.mode == "upload":
            self.upload_tokens += increment
            display_diff = self.upload_tokens - self.upload_display
            if display_diff > 0:
                catch_up = max(1, display_diff // random.randint(2, 4))
                self.upload_display += catch_up
                if self.upload_display > self.upload_tokens:
                    self.upload_display = self.upload_tokens
        else:
            self.download_tokens += increment
            display_diff = self.download_tokens - self.download_display
            if display_diff > 0:
                catch_up = max(1, display_diff // random.randint(2, 4))
                self.download_display += catch_up
                if self.download_display > self.download_tokens:
                    self.download_display = self.download_tokens

        return True

    def get_display(self) -> tuple:
        """Get current display state.

        Returns:
            (up_arrow, up_tokens, down_arrow, down_tokens, mode)
        """
        # Handle arrow blink (hide arrow during blink)
        show_arrow = not (self.arrow_blink and self.frame % 4 < 2)

        if self.mode == "upload":
            up_arrow = "↑" if show_arrow else " "
            return (up_arrow, self.upload_display, "", 0, "upload")
        elif self.mode == "waiting":
            # No arrows during waiting, just show upload total
            return ("", self.upload_display, "", 0, "waiting")
        elif self.mode == "download":
            down_arrow = "↓" if show_arrow else " "
            return (
                "↑",
                self.upload_display,
                down_arrow,
                self.download_display,
                "download",
            )
        else:
            # Idle - show both totals
            return ("", self.upload_display, "", self.download_display, "idle")


# Global instance for token I/O state
_token_io_state = TokenIOState()


def get_token_io_state() -> TokenIOState:
    """Get the global token I/O state for event handlers."""
    return _token_io_state


def _format_tokens(n: int) -> tuple:
    """Format token count for display. Returns (full, short) formats."""
    if n < 1000:
        return (str(n), str(n))
    elif n < 1000000:
        return (f"{n/1000:.1f}K", f"{n//1000}K")
    else:
        return (f"{n/1000000:.1f}M", f"{n//1000000}M")


def render_token_io(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render token I/O activity widget.

    Shows upload/download activity with animated token counts:
    - Upload (↑): Green arrow with prompt token count
    - Download (↓): Blue arrow with completion token count
    - Shows both during download phase: "↑1.2K ↓500"
    """
    try:
        # Update animation state
        _token_io_state.update()

        up_arrow, up_tokens, down_arrow, down_tokens, mode = (
            _token_io_state.get_display()
        )

        up_full, up_short = _format_tokens(up_tokens)
        down_full, down_short = _format_tokens(down_tokens)

        # Colors
        up_color = T().success[0]  # Green for upload
        down_color = T().primary[0]  # Blue for download
        dim_color = T().text_dim
        text_color = T().text

        if mode == "upload":
            # Uploading: show "↑ 1.2K" with animation
            if width >= 10:
                return _fg(up_arrow, up_color) + _fg(f" {up_full}", text_color)
            elif width >= 5:
                return _fg(up_arrow, up_color) + _fg(up_short, text_color)
            else:
                return _fg(up_arrow, up_color)

        elif mode == "waiting":
            # Waiting: no arrows, just show upload total dimmed
            if width >= 8:
                return _fg(f"{up_full} tok", dim_color)
            else:
                return _fg(f"{up_short}t", dim_color)

        elif mode == "download":
            # Downloading: show both "↑1.2K ↓500"
            if width >= 14:
                return (
                    _fg(up_arrow, dim_color)
                    + _fg(up_short, dim_color)
                    + _fg(" ", dim_color)
                    + _fg(down_arrow, down_color)
                    + _fg(down_full, text_color)
                )
            elif width >= 10:
                return (
                    _fg(up_arrow, dim_color)
                    + _fg(up_short, dim_color)
                    + _fg(down_arrow, down_color)
                    + _fg(down_short, text_color)
                )
            elif width >= 5:
                # Just show download
                return _fg(down_arrow, down_color) + _fg(down_short, text_color)
            else:
                return _fg(down_arrow, down_color)

        else:
            # Idle: show totals dimmed
            total = up_tokens + down_tokens
            if total == 0:
                return _fg("--", dim_color)
            total_full, total_short = _format_tokens(total)
            if width >= 8:
                return _fg(f"{total_full} tok", dim_color)
            else:
                return _fg(f"{total_short}t", dim_color)

    except Exception as e:
        logger.error(f"token_io widget error: {e}")
        return _fg("--", T().text_dim)


def render_mcp(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render MCP server connection status widget.

    Shows:
    - Connection status with icon (+ for connected, ? for unknown, ! for error)
    - Connected server count
    - Total tool count
    - Error indicator if connection failed

    Examples:
      "+ mcp:2 15t"  (2 servers connected, 15 tools available)
      "mcp:off"      (no servers configured)
      "! mcp:1"      (1 server error)
    """
    try:
        if not ctx or not hasattr(ctx, "llm_service"):
            return _fg("mcp?", T().text_dim)

        mcp_integration = (
            getattr(ctx.llm_service, "mcp_integration", None)
            if ctx.llm_service
            else None
        )
        connected = 0
        total_tools = 0

        if mcp_integration:
            connections = mcp_integration.server_connections
            tool_registry = mcp_integration.tool_registry
            connected = len(
                [c for c in connections.values() if getattr(c, "initialized", False)]
            )
            total_tools = len(tool_registry)
        elif ctx and ctx.remote_state:
            # Attach mode fallback
            mcp = ctx.remote_state.get("mcp", {})
            connected = mcp.get("connected", 0)
            total_tools = mcp.get("tools", 0)

        if connected == 0:
            # No servers connected
            if total_tools == 0:
                # No tools either - probably not configured
                return _fg("mcp:off", T().text_dim)
            else:
                # Has tools but no active connections (shouldn't happen)
                return _fg(f"mcp:{connected} {total_tools}t", T().text_dim)

        # Connected - show status
        icon = _fg("+", T().success[0])
        text = _fg(f"mcp:{connected}", T().text)
        tools_text = _fg(f"{total_tools}t", T().text_dim)

        if width >= 15:
            return f"{icon} {text} {tools_text}"
        elif width >= 10:
            return f"{icon} {text}"
        else:
            return icon

    except Exception as e:
        logger.error(f"mcp widget error: {e}")
        return _fg("mcp:err", T().error[0])


def render_session(width: int, ctx: Optional[WidgetContext]) -> str:
    """Render session name widget.

    Shows the current conversation session name (memorable themed names).

    Width-aware formats:
    - Full (20+ chars): "session: quantum-spark"
    - Compact (15+ chars): "sess: quan..spark"
    - Minimal (10+ chars): "quan..park"

    Examples:
        "session: quantum-spark"
        "session: phoenix-rise"
        "sess: qua..ark"
    """
    try:
        session_name = "unknown"
        if ctx and ctx.llm_service:
            conversation_manager = getattr(
                ctx.llm_service, "conversation_manager", None
            )
            if conversation_manager:
                session_name = getattr(
                    conversation_manager, "current_session_id", "unknown"
                )

        # Attach mode fallback
        if session_name == "unknown" and ctx and ctx.remote_state:
            session_name = ctx.remote_state.get("session", "unknown")

        # Strip timestamp prefix if present (format: YYMMDDHHMM-name-name)
        # Session names are like "2601231430-quantum-spark"
        if "-" in session_name and session_name[0].isdigit():
            parts = session_name.split("-", 1)
            if len(parts) == 2 and len(parts[0]) == 10 and parts[0].isdigit():
                # Has timestamp prefix, remove it for display
                display_name = parts[1]
            else:
                display_name = session_name
        else:
            display_name = session_name

        # Width-aware rendering (always use name without timestamp)
        if width >= 20:
            # Full format with "session:" prefix
            icon = _fg("session:", T().secondary[0])
            text = _fg(display_name, T().text)
            return f"{icon} {text}"
        elif width >= 15:
            # Compact prefix
            icon = _fg("sess:", T().secondary[0])
            # Middle-truncate name
            available = width - 6  # "sess: " = 6 chars
            text = _fg(_middle_truncate(display_name, available), T().text)
            return f"{icon} {text}"
        else:
            # Minimal - just truncated name
            text = _fg(_middle_truncate(display_name, width), T().text)
            return text

    except Exception as e:
        logger.error(f"session widget error: {e}")
        return _fg("session?", T().text_dim)


# =============================================================================
# REGISTRATION FUNCTION
# =============================================================================


def register_core_widgets(registry: StatusWidgetRegistry) -> None:
    """Register all core widgets with the registry.

    Args:
        registry: The StatusWidgetRegistry to register widgets with
    """
    logger.info("Registering core status widgets...")

    # Current Working Directory
    registry.register(
        id="cwd",
        name="Directory",
        description="Current working directory",
        render_fn=render_cwd,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=8,
        interactive=True,
        interaction_type="modal",
        on_activate=cwd_directory_modal,
    )

    # LLM Profile
    registry.register(
        id="profile",
        name="Profile",
        description="Active LLM profile name",
        render_fn=render_profile,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=6,
        interactive=True,
        interaction_type="modal",
        on_activate=profile_switcher_modal,
    )

    # Model Name
    registry.register(
        id="model",
        name="Model",
        description="Active model name",
        render_fn=render_model,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=8,
        interactive=True,
        interaction_type="command",
        command="/model",
    )

    # API Endpoint
    registry.register(
        id="endpoint",
        name="Endpoint",
        description="API endpoint hostname",
        render_fn=render_endpoint,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=8,
    )

    # Status Indicator
    registry.register(
        id="status",
        name="Status",
        description="Ready/Working indicator",
        render_fn=render_status,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=6,
    )

    # Message/Token Stats
    registry.register(
        id="stats",
        name="Stats",
        description="Message and token counts",
        render_fn=render_stats,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=12,
    )

    # Hub Identity
    registry.register(
        id="hub",
        name="Hub",
        description="Hub identity and peer count",
        render_fn=render_hub,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=8,
    )

    # Active Agent
    registry.register(
        id="agent",
        name="Agent",
        description="Active agent name",
        render_fn=render_agent,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=8,
        interactive=True,
        interaction_type="command",
        command="/agent",
    )

    # Active Skills
    registry.register(
        id="skills",
        name="Skills",
        description="Active skills list",
        render_fn=render_skills,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=10,
        interactive=True,
        interaction_type="command",
        command="/skill",
    )

    # Tasks
    registry.register(
        id="tasks",
        name="Tasks",
        description="Pending/current task info",
        render_fn=render_tasks,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=10,
    )

    # Tmux Sessions
    registry.register(
        id="tmux",
        name="Term",
        description="Active terminal session count",
        render_fn=render_tmux,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=6,
        interactive=True,
        interaction_type="modal",
        on_activate=tmux_session_modal,
    )

    # Background Tasks
    registry.register(
        id="bg-tasks",
        name="Background",
        description="Running background task count",
        render_fn=render_bg_tasks,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=5,
    )

    # Clock
    registry.register(
        id="clock",
        name="Clock",
        description="Current time",
        render_fn=render_clock,
        category=WidgetCategory.CORE,
        default_width="8ch",
        min_width=8,
    )

    # Git Branch
    registry.register(
        id="git-branch",
        name="Git Branch",
        description="Current git branch name",
        render_fn=render_git_branch,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=6,
        interactive=True,
        interaction_type="modal",
        on_activate=git_branch_modal,
    )

    # Git Status
    registry.register(
        id="git-status",
        name="Git Status",
        description="Working directory clean/dirty status",
        render_fn=render_git_status,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=4,
    )

    # Test Toggle Widget (for verification testing)
    registry.register(
        id="test-toggle",
        name="Test Toggle",
        description="Test toggle widget for verification (cycles: off -> on -> auto)",
        render_fn=render_test_toggle,
        category=WidgetCategory.CORE,
        default_width="6ch",
        min_width=4,
        interactive=True,
        interaction_type="toggle",
        on_activate=activate_test_toggle,
        states=_test_toggle_states,
    )

    # Label Widget (editable)
    registry.register(
        id="label",
        name="Label",
        description="Editable text label (press Enter to edit)",
        render_fn=render_label,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=3,
        interaction_type="inline_edit",
        on_activate=activate_label_edit,
    )

    # Spacer Widget
    registry.register(
        id="spacer",
        name="Spacer",
        description="Empty space for visual separation",
        render_fn=render_spacer,
        category=WidgetCategory.CORE,
        default_width="2ch",
        min_width=1,
    )

    # Divider Widget
    registry.register(
        id="divider",
        name="Divider",
        description="Vertical separator line",
        render_fn=render_divider,
        category=WidgetCategory.CORE,
        default_width="1ch",
        min_width=1,
    )

    # Token I/O Activity Widget
    registry.register(
        id="token-io",
        name="Token I/O",
        description="Token upload/download activity with animation",
        render_fn=render_token_io,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=6,
    )

    # MCP Status Widget
    registry.register(
        id="mcp",
        name="MCP",
        description="MCP server connection status",
        render_fn=render_mcp,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=8,
        interactive=True,
        interaction_type="command",
        command="/mcp",
    )

    # Session Name Widget
    registry.register(
        id="session",
        name="Session",
        description="Current conversation session name",
        render_fn=render_session,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=10,
    )

    # System Monitor Widget
    registry.register(
        id="sysmon",
        name="System Monitor",
        description="CPU, memory, and disk usage with color thresholds",
        render_fn=render_sysmon,
        category=WidgetCategory.CORE,
        default_width="auto",
        min_width=15,
    )

    # AltView Sessions
    register_altview_widget(registry)

    logger.info(f"Registered {len(registry.get_core_widgets())} core widgets")
