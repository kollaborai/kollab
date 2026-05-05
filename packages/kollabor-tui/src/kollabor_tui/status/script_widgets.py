"""Script widget manager for discovering and executing user-defined status widgets.

Discovers script-based widgets from:
- ~/.kollab/status-widgets/ (global widgets)
- .kollab/status-widgets/ (project-specific widgets)

Parses metadata from script comments and executes scripts with timeout protection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kollabor_config.config_utils import (
    get_local_config_directory,
    resolve_global_path,
)

if TYPE_CHECKING:
    from .script_refresh_scheduler import ScriptWidgetRefreshScheduler

logger = logging.getLogger(__name__)

# Unicode display width support
try:
    from wcwidth import wcswidth  # type: ignore[import-not-found]

    def _display_width(text: str) -> int:
        """Get actual display width of text, accounting for wide unicode chars."""
        return int(wcswidth(text))

except ImportError:
    # Fallback: use len() - not perfect but works for most cases
    def _display_width(text: str) -> int:
        """Fallback display width using len()."""
        return len(text)


# ANSI escape code pattern for stripping
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHfABCDnr]")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text for width calculation."""
    return _ANSI_RE.sub("", text)


def _display_width_aware(text: str) -> int:
    """Get display width, ignoring ANSI color codes."""
    return _display_width(_strip_ansi(text))


class RefreshType(Enum):
    """Widget refresh strategy types."""

    MANUAL = "manual"
    TIME_BASED = "time_based"
    ON_EVENT = "on_event"
    ONCE = "once"
    SMART = "smart"


class InteractionType(Enum):
    """Widget interaction types."""

    MODAL = "modal"
    TOGGLE = "toggle"
    ACTION = "action"
    INLINE_EDIT = "inline_edit"


@dataclass
class ScriptWidget:
    """Definition of a script-based status widget.

    Attributes:
        id: Unique identifier (kebab-case)
        name: Display name in picker
        description: Brief description
        category: Widget category (git, docker, python, custom, etc.)
        interactive: Whether widget is clickable
        interaction_type: Type of interaction (modal, toggle, action, inline_edit)
        on_activate: Script to run on activation (path or script content)
        refresh_type: Refresh strategy type
        refresh_interval: Time interval in seconds (for time-based refresh)
        hooks: List of hook names that trigger refresh
        min_width: Minimum width in characters
        timeout: Max execution time in seconds
        color: Allow ANSI color codes
        script_path: Path to the widget script
        json_sidecar_path: Optional path to JSON sidecar metadata
        last_output: Cached script output
        last_execution: Timestamp of last execution
        last_refresh: Timestamp of last successful refresh
        extra_metadata: Additional metadata from JSON sidecar
    """

    id: str
    name: str
    description: str
    category: str = "custom"
    interactive: bool = False
    interaction_type: Optional[InteractionType] = None
    on_activate: Optional[str] = None
    refresh_type: RefreshType = RefreshType.MANUAL
    refresh_interval: Optional[int] = None
    hooks: List[str] = field(default_factory=list)
    min_width: int = 10
    timeout: int = 5
    color: bool = True
    script_path: Optional[Path] = None
    json_sidecar_path: Optional[Path] = None
    last_output: Optional[str] = None
    last_execution: Optional[datetime] = None
    last_refresh: Optional[datetime] = None
    extra_metadata: Dict[str, Any] = field(default_factory=dict)

    def is_fresh(self, max_age_seconds: int = 60) -> bool:
        """Check if cached output is still fresh.

        Args:
            max_age_seconds: Maximum age in seconds before output is stale

        Returns:
            True if output is fresh (within max age), False otherwise
        """
        if self.last_refresh is None or self.last_output is None:
            return False

        age = (datetime.now() - self.last_refresh).total_seconds()
        return age < max_age_seconds

    def should_refresh(self, event_name: Optional[str] = None) -> bool:
        """Determine if widget should refresh based on its refresh strategy.

        Args:
            event_name: Optional event name that triggered the check

        Returns:
            True if widget should refresh, False otherwise
        """
        # ONCE widgets never refresh after first run
        if self.refresh_type == RefreshType.ONCE:
            return self.last_refresh is None

        # MANUAL widgets only refresh when explicitly triggered
        if self.refresh_type == RefreshType.MANUAL:
            return False

        # ON_EVENT widgets refresh on matching hook events
        if self.refresh_type == RefreshType.ON_EVENT:
            return event_name in self.hooks

        # SMART widgets refresh on time OR event
        if self.refresh_type == RefreshType.SMART:
            # Check time-based refresh
            if self.refresh_interval and self.last_refresh:
                age = (datetime.now() - self.last_refresh).total_seconds()
                if age >= self.refresh_interval:
                    return True
            # Check event-based refresh
            if event_name in self.hooks:
                return True
            return False

        # TIME_BASED widgets refresh on interval
        if self.refresh_type == RefreshType.TIME_BASED:
            if self.refresh_interval and self.last_refresh:
                age = (datetime.now() - self.last_refresh).total_seconds()
                return age >= self.refresh_interval
            return self.last_refresh is None

        return False


class ScriptWidgetManager:
    """Manager for discovering and executing script-based status widgets.

    Discovers widgets from global and local directories, parses metadata
    from script comments, and executes scripts with timeout protection.
    """

    # Widget directories
    GLOBAL_WIDGETS_DIR = resolve_global_path("status-widgets")
    LOCAL_WIDGETS_DIR = get_local_config_directory() / "status-widgets"

    # Metadata field patterns (support optional @ prefix after #)
    METADATA_PATTERNS = {
        "widget-id": r"@?widget-id:\s*(.+)",
        "name": r"@?name:\s*(.+)",
        "description": r"@?description:\s*(.+)",
        "category": r"@?category:\s*(.+)",
        "interactive": r"@?interactive:\s*(true|false)",
        "interaction-type": r"@?interaction-type:\s*(modal|toggle|action|inline_edit)",
        "on-activate": r"@?on-activate:\s*(.+)",
        "refresh": r"@?refresh:\s*(.+)",
        "hooks": r"@?hooks:\s*(.+)",
        "min-width": r"@?min-width:\s*(\d+)",
        "timeout": r"@?timeout:\s*(.+)",
        "color": r"@?color:\s*(true|false)",
    }

    def __init__(self, default_timeout: int = 5, enable_cache: bool = True):
        """Initialize the script widget manager.

        Args:
            default_timeout: Default execution timeout in seconds
            enable_cache: Whether to cache script outputs
        """
        self.default_timeout = default_timeout
        self.enable_cache = enable_cache
        self._widgets: Dict[str, ScriptWidget] = {}
        self._discovered = False

        # Refresh scheduler
        self._scheduler: Optional[ScriptWidgetRefreshScheduler] = None
        self._event_bus = None
        self._scheduler_running = False

        logger.debug(f"ScriptWidgetManager initialized (timeout={default_timeout}s)")

    def discover_widgets(self, force: bool = False) -> List[ScriptWidget]:
        """Discover script-based widgets from global and local directories.

        Args:
            force: Force re-discovery even if already discovered

        Returns:
            List of discovered ScriptWidget instances
        """
        if self._discovered and not force:
            return list(self._widgets.values())

        self._widgets.clear()
        discovered_count = 0

        # Search in both global and local directories
        search_dirs = [
            (self.GLOBAL_WIDGETS_DIR, "global"),
            (self.LOCAL_WIDGETS_DIR, "local"),
        ]

        for widget_dir, scope in search_dirs:
            if not widget_dir.exists():
                logger.debug(f"Widget directory does not exist: {widget_dir}")
                continue

            # Find executable script files (skip hidden/dotfiles)
            for script_path in widget_dir.glob("*"):
                # Skip hidden files (starting with dot)
                if script_path.name.startswith("."):
                    continue
                if script_path.is_file() and self._is_executable(script_path):
                    try:
                        widget = self._parse_widget_from_script(script_path, scope)
                        if widget:
                            self._widgets[widget.id] = widget
                            discovered_count += 1
                            logger.debug(
                                f"Discovered widget '{widget.id}' from {scope}: {script_path}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse widget from {script_path}: {e}"
                        )

        self._discovered = True
        logger.info(f"Discovered {discovered_count} script widget(s)")

        return list(self._widgets.values())

    def get_widget(self, widget_id: str) -> Optional[ScriptWidget]:
        """Get a widget by ID.

        Args:
            widget_id: Widget identifier

        Returns:
            ScriptWidget if found, None otherwise
        """
        if not self._discovered:
            self.discover_widgets()
        return self._widgets.get(widget_id)

    def get_all_widgets(self) -> List[ScriptWidget]:
        """Get all discovered widgets.

        Returns:
            List of all ScriptWidget instances
        """
        if not self._discovered:
            self.discover_widgets()
        return list(self._widgets.values())

    def execute_script(
        self,
        widget_id: str,
        force: bool = False,
        use_cache: bool = True,
    ) -> Optional[str]:
        """Execute a widget script and return its output.

        Args:
            widget_id: Widget identifier
            force: Force execution even if cached output is fresh
            use_cache: Whether to use cached output if available

        Returns:
            Script output string, or None if execution failed
        """
        widget = self.get_widget(widget_id)
        if not widget:
            logger.error(f"Widget not found: {widget_id}")
            return None

        # Check cache if enabled
        if use_cache and self.enable_cache and not force:
            if widget.is_fresh(max_age_seconds=widget.timeout):
                logger.debug(f"Using cached output for widget '{widget_id}'")
                return widget.last_output

        # Execute script
        output = self._execute_widget_script(widget)
        if output is not None:
            widget.last_output = output
            widget.last_refresh = datetime.now()
            widget.last_execution = datetime.now()

        return output

    def execute_all(
        self,
        force: bool = False,
        use_cache: bool = True,
    ) -> Dict[str, Optional[str]]:
        """Execute all widget scripts.

        Args:
            force: Force execution even if cached output is fresh
            use_cache: Whether to use cached output if available

        Returns:
            Dictionary mapping widget IDs to their outputs
        """
        results = {}
        for widget in self.get_all_widgets():
            results[widget.id] = self.execute_script(
                widget.id, force=force, use_cache=use_cache
            )
        return results

    def execute_on_event(self, event_name: str) -> Dict[str, Optional[str]]:
        """Execute widgets that should refresh on a specific event.

        Args:
            event_name: Hook event name (e.g., "post_user_input")

        Returns:
            Dictionary mapping widget IDs to their outputs
        """
        results = {}
        for widget in self.get_all_widgets():
            if widget.should_refresh(event_name=event_name):
                results[widget.id] = self.execute_script(widget.id)
        return results

    def clear_cache(self, widget_id: Optional[str] = None) -> None:
        """Clear cached output.

        Args:
            widget_id: Specific widget ID to clear, or None to clear all
        """
        if widget_id:
            widget = self.get_widget(widget_id)
            if widget:
                widget.last_output = None
                widget.last_refresh = None
        else:
            for widget in self._widgets.values():
                widget.last_output = None
                widget.last_refresh = None

    # Refresh Scheduler Management

    async def initialize_refresh_scheduler(self, event_bus) -> None:
        """Initialize and start the refresh scheduler for script widgets.

        This method:
        1. Creates the ScriptWidgetRefreshScheduler
        2. Schedules all discovered widgets based on their refresh config
        3. Starts the scheduler background task

        Args:
            event_bus: EventBus instance for hook registration
        """
        from .script_refresh_scheduler import ScriptWidgetRefreshScheduler

        if self._scheduler_running:
            logger.warning("Script refresh scheduler already running")
            return

        self._event_bus = event_bus
        self._scheduler = ScriptWidgetRefreshScheduler(event_bus)

        # Ensure widgets are discovered
        widgets = self.get_all_widgets()

        if not widgets:
            logger.info("No script widgets to schedule for refresh")
            return

        scheduled_count = 0

        for widget in widgets:
            try:
                # Skip widgets without refresh configuration
                if widget.refresh_type == RefreshType.MANUAL and not widget.hooks:
                    logger.debug(
                        f"Widget '{widget.id}' is manual-only, skipping schedule"
                    )
                    continue

                # Map refresh type to scheduler mode
                mode = self._map_refresh_mode_for_scheduler(widget)
                interval = self._format_interval_for_scheduler(widget)
                hooks = widget.hooks if widget.hooks else None

                # Create async refresh callback using factory to ensure proper closure capture
                def make_refresh_callback(widget_id: str):
                    """Factory to create refresh callback with correct closure capture."""

                    async def refresh_callback():
                        """Async wrapper for sync execute_script."""
                        # Run sync execution in a worker thread to avoid blocking the event loop.
                        return await asyncio.to_thread(
                            self.execute_script,
                            widget_id,
                            force=True,
                            use_cache=False,
                        )

                    return refresh_callback

                refresh_callback = make_refresh_callback(widget.id)

                # Schedule the widget
                await self._scheduler.schedule_widget(
                    widget_id=widget.id,
                    refresh_callback=refresh_callback,
                    refresh_mode=mode,
                    interval=interval,
                    hooks=hooks,
                )

                scheduled_count += 1
                logger.info(
                    f"Scheduled widget '{widget.id}': mode={mode}, "
                    f"interval={interval}, hooks={hooks}"
                )

            except Exception as e:
                logger.error(f"Failed to schedule widget '{widget.id}': {e}")

        logger.info(f"Scheduled {scheduled_count}/{len(widgets)} script widgets")

        # Start the scheduler
        await self._scheduler.start()
        self._scheduler_running = True
        logger.info("Script widget refresh scheduler started")

    def _map_refresh_mode_for_scheduler(self, widget: ScriptWidget) -> str:
        """Map ScriptWidget RefreshType to scheduler refresh_mode string.

        Args:
            widget: ScriptWidget instance

        Returns:
            Refresh mode string for scheduler
        """
        rt = widget.refresh_type

        if rt == RefreshType.TIME_BASED:
            return "time"
        elif rt == RefreshType.ON_EVENT:
            return "hook"
        elif rt == RefreshType.ONCE:
            return "once"
        elif rt == RefreshType.SMART:
            return "smart"
        elif rt == RefreshType.MANUAL:
            # If it has hooks, treat as hook mode
            if widget.hooks:
                return "hook"
            return "manual"

        logger.warning(
            f"Unknown refresh type '{rt}' for widget '{widget.id}', "
            "defaulting to manual"
        )
        return "manual"

    def _format_interval_for_scheduler(self, widget: ScriptWidget) -> Optional[str]:
        """Format refresh interval for scheduler.

        Args:
            widget: ScriptWidget instance

        Returns:
            Formatted interval string (e.g., "5s", "1m") or None
        """
        if widget.refresh_interval is None:
            return None

        seconds = widget.refresh_interval

        if seconds >= 3600 and seconds % 3600 == 0:
            hours = seconds // 3600
            return f"{hours}h"
        elif seconds >= 60:
            minutes = seconds / 60
            if minutes.is_integer():
                return f"{int(minutes)}m"
            return f"{minutes}m"
        return f"{seconds}s"

    async def shutdown_refresh_scheduler(self) -> None:
        """Stop the refresh scheduler and clean up resources."""
        if not self._scheduler_running:
            return

        if self._scheduler:
            try:
                await self._scheduler.stop()
                logger.info("Script widget refresh scheduler stopped")
            except Exception as e:
                logger.warning(f"Error stopping scheduler: {e}")

        self._scheduler_running = False
        self._scheduler = None

    def parse_metadata(self, script_path: Path) -> Dict[str, Any]:
        """Parse metadata from script comments.

        Args:
            script_path: Path to the script file

        Returns:
            Dictionary of parsed metadata fields
        """
        return self._parse_metadata_from_script(script_path)

    # Private methods

    def _is_executable(self, script_path: Path) -> bool:
        """Check if a script file has a valid shebang and is executable.

        Args:
            script_path: Path to the script file

        Returns:
            True if script is executable, False otherwise
        """
        try:
            # Check for shebang
            with open(script_path, "rb") as f:
                first_bytes = f.read(2)
                if first_bytes != b"#!":
                    logger.debug(f"No shebang found in {script_path}")
                    return False

            # Check executable permission
            st = os.stat(script_path)
            is_executable = bool(
                st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            )

            if not is_executable:
                logger.debug(f"No execute permission on {script_path}")

            return is_executable

        except Exception as e:
            logger.warning(f"Error checking executable status for {script_path}: {e}")
            return False

    def _parse_widget_from_script(
        self, script_path: Path, scope: str
    ) -> Optional[ScriptWidget]:
        """Parse a widget definition from a script file.

        Args:
            script_path: Path to the script file
            scope: "global" or "local"

        Returns:
            ScriptWidget instance, or None if parsing failed
        """
        # First try JSON sidecar
        json_sidecar = script_path.with_suffix(".json")
        metadata = {}

        if json_sidecar.exists():
            try:
                with open(json_sidecar, "r") as f:
                    metadata = json.load(f)
                logger.debug(f"Loaded metadata from JSON sidecar: {json_sidecar}")
            except Exception as e:
                logger.warning(f"Failed to parse JSON sidecar {json_sidecar}: {e}")

        # Parse comment headers (overrides JSON sidecar for overlapping fields)
        comment_metadata = self._parse_metadata_from_script(script_path)
        metadata.update(comment_metadata)

        # Validate required fields
        if "id" not in metadata and "widget-id" not in metadata:
            logger.warning(f"Widget missing required 'widget-id' field: {script_path}")
            return None

        widget_id = metadata.get("id") or metadata["widget-id"]

        # Build ScriptWidget instance
        try:
            return ScriptWidget(
                id=widget_id,
                name=str(metadata.get("name", widget_id)),
                description=metadata.get("description", ""),
                category=metadata.get("category", "custom"),
                interactive=metadata.get("interactive", False),
                interaction_type=self._parse_interaction_type(
                    metadata.get("interaction-type")
                ),
                on_activate=metadata.get("on-activate"),
                refresh_type=self._parse_refresh_type(metadata.get("refresh")),
                refresh_interval=self._parse_refresh_interval(metadata.get("refresh")),
                hooks=self._parse_hooks(metadata.get("hooks")),
                min_width=int(
                    metadata.get("min_width") or metadata.get("min-width") or 10
                ),
                timeout=self._parse_timeout(metadata.get("timeout")),
                color=metadata.get("color", True),
                script_path=script_path,
                json_sidecar_path=json_sidecar if json_sidecar.exists() else None,
                extra_metadata=metadata,
            )
        except Exception as e:
            logger.error(f"Failed to create ScriptWidget for {widget_id}: {e}")
            return None

    def _parse_metadata_from_script(self, script_path: Path) -> Dict[str, Any]:
        """Parse metadata fields from script comment headers.

        Args:
            script_path: Path to the script file

        Returns:
            Dictionary of parsed metadata (with hyphenated keys preserved)
        """
        metadata = {}

        try:
            with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
                # Read first 50 lines for metadata
                for line in f:
                    line = line.strip()

                    # Stop at non-comment line (after shebang)
                    if not line.startswith("#"):
                        # Allow shebang line
                        if line.startswith("#!"):
                            continue
                        break

                    # Parse metadata fields
                    for field_name, pattern in self.METADATA_PATTERNS.items():
                        match = re.match(pattern, line[1:].strip())  # Skip '#'
                        if match:
                            value = match.group(1).strip()
                            metadata[field_name] = value
                            logger.debug(
                                f"Parsed metadata '{field_name}': {value} from {script_path}"
                            )
                            break

        except Exception as e:
            logger.warning(f"Error parsing metadata from {script_path}: {e}")

        return metadata

    def _parse_interaction_type(
        self, value: Optional[str]
    ) -> Optional[InteractionType]:
        """Parse interaction type from string.

        Args:
            value: Interaction type string

        Returns:
            InteractionType enum or None
        """
        if not value:
            return None
        try:
            return InteractionType(value.lower())
        except ValueError:
            logger.warning(f"Unknown interaction type: {value}")
            return None

    def _parse_refresh_type(self, value: Optional[str]) -> RefreshType:
        """Parse refresh strategy from string.

        Args:
            value: Refresh specification (e.g., "manual", "5s", "on-event", "once")

        Returns:
            RefreshType enum
        """
        if not value:
            return RefreshType.MANUAL

        value = value.lower().strip()

        # Check for explicit types
        if value == "manual":
            return RefreshType.MANUAL
        elif value == "once":
            return RefreshType.ONCE
        elif value == "on-event":
            return RefreshType.ON_EVENT

        # Check for time-based patterns (e.g., "5s", "1m", "30s")
        if re.match(r"^\d+[sm]?$", value):
            return RefreshType.TIME_BASED

        # Default to manual
        return RefreshType.MANUAL

    def _parse_refresh_interval(self, value: Optional[str]) -> Optional[int]:
        """Parse refresh interval from string.

        Args:
            value: Refresh specification (e.g., "5s", "1m")

        Returns:
            Interval in seconds, or None
        """
        if not value:
            return None

        value = value.lower().strip()

        # Parse time-based patterns
        match = re.match(r"^(\d+)([sm])?$", value)
        if match:
            num = int(match.group(1))
            unit = match.group(2) or "s"

            if unit == "m":
                return num * 60
            else:
                return num

        return None

    def _parse_hooks(self, value: Optional[str]) -> List[str]:
        """Parse hooks list from comma-separated string.

        Args:
            value: Comma-separated hook names (or list from JSON)

        Returns:
            List of hook names
        """
        if not value:
            return []

        # If already a list (from JSON)
        if isinstance(value, list):
            return [h.strip() for h in value if h.strip()]

        # Parse comma-separated string
        return [h.strip() for h in str(value).split(",") if h.strip()]

    def _parse_timeout(self, value: Optional[str]) -> int:
        """Parse timeout from string.

        Args:
            value: Timeout specification (e.g., "5s", "3s")

        Returns:
            Timeout in seconds
        """
        if not value:
            return self.default_timeout

        value = value.lower().strip()

        # Parse time pattern (e.g., "5s")
        match = re.match(r"^(\d+)s?$", value)
        if match:
            return int(match.group(1))

        logger.warning(f"Invalid timeout format: {value}, using default")
        return self.default_timeout

    def _execute_widget_script(self, widget: ScriptWidget) -> Optional[str]:
        """Execute a widget script with timeout protection.

        Args:
            widget: ScriptWidget instance to execute

        Returns:
            Script output string, or None if execution failed
        """
        if not widget.script_path or not widget.script_path.exists():
            logger.error(f"Script path does not exist: {widget.script_path}")
            return None

        try:
            # Run script with subprocess
            result = subprocess.run(
                [str(widget.script_path)],
                capture_output=True,
                text=True,
                timeout=widget.timeout,
                cwd=widget.script_path.parent,
                env=self._get_script_env(widget),
            )

            # Check for errors
            if result.returncode != 0:
                logger.warning(
                    f"Widget script '{widget.id}' exited with code {result.returncode}: "
                    f"{result.stderr}"
                )
                return None

            # Return stdout (strip trailing whitespace)
            output = result.stdout.rstrip("\n\r")
            logger.debug(f"Widget '{widget.id}' output: {repr(output)}")
            return output

        except subprocess.TimeoutExpired:
            logger.warning(
                f"Widget script '{widget.id}' timed out after {widget.timeout}s"
            )
            return None

        except Exception as e:
            logger.error(f"Error executing widget script '{widget.id}': {e}")
            return None

    def _get_script_env(self, widget: ScriptWidget) -> Dict[str, str]:
        """Get environment variables for script execution.

        Args:
            widget: ScriptWidget instance

        Returns:
            Environment variables dictionary
        """
        env = os.environ.copy()

        # Add widget-specific environment variables
        env["KOLLAB_WIDGET_ID"] = widget.id
        env["KOLLAB_WIDGET_DIR"] = (
            str(widget.script_path.parent) if widget.script_path else ""
        )

        return env


def register_script_widgets(
    registry,
    script_manager: Optional["ScriptWidgetManager"] = None,
    context: Optional[Any] = None,
) -> int:
    """Register discovered script widgets with the StatusWidgetRegistry.

    This function bridges the gap between script discovery and widget registration.
    It discovers all script-based widgets and registers them with the central
    widget registry so they appear in the widget picker and can be displayed.

    Args:
        registry: StatusWidgetRegistry instance to register widgets with
        script_manager: Optional ScriptWidgetManager instance (creates new if None)
        context: Optional context object for widget rendering (passed to render functions)

    Returns:
        Number of widgets successfully registered

    Example:
        registry = StatusWidgetRegistry()
        count = register_script_widgets(registry)
        print(f"Registered {count} script widgets")
    """
    from .script_action_handlers import execute_action_script_sync
    from .widget_registry import WidgetCategory, WidgetWidth

    if script_manager is None:
        script_manager = ScriptWidgetManager()

    # Discover all script widgets
    discovered_widgets = script_manager.discover_widgets(force=True)
    registered_count = 0

    for script_widget in discovered_widgets:
        try:
            # Create a render function that executes the script
            def make_render_fn(widget_id: str, manager: ScriptWidgetManager):
                """Factory function to create render closure with correct widget_id."""

                def render_fn(width: int, ctx: Any = None) -> str:
                    """Render script widget by executing its script."""
                    output = manager.execute_script(widget_id, use_cache=True)
                    if output is None:
                        return f"[{widget_id}:err]"
                    # Truncate to fit width using display width (not len())
                    # This handles unicode wide chars and strips ANSI codes for calculation
                    if _display_width_aware(output) > width:
                        # Truncate character by character until display width fits
                        # Preserve ANSI codes in the output
                        truncated = ""
                        for char in output:
                            test = truncated + char
                            if _display_width_aware(test) <= width:
                                truncated = test
                            else:
                                break
                        return truncated
                    return output

                return render_fn

            # Create on_activate handler for interactive widgets
            on_activate_fn = None
            if script_widget.interactive and script_widget.on_activate:
                # Create an async activation handler
                def make_activate_fn(
                    widget_id: str,
                    action_script_path: str,
                    manager: ScriptWidgetManager,
                ):
                    """Factory to create activation handler with correct closure."""

                    async def activate_handler(widget_id: str, context: Any) -> Any:
                        """Execute the action script and invalidate widget cache."""
                        import os

                        script_path = Path(os.path.expanduser(action_script_path))
                        logger.info(
                            f"Activating widget '{widget_id}' via {script_path}"
                        )

                        # Execute the action script (synchronous is fine for simple actions)
                        result = execute_action_script_sync(
                            script_path=script_path,
                            action_key="activate",  # Default action key
                            timeout=script_widget.timeout,
                        )

                        # Clear widget cache so updated state displays
                        manager.clear_cache(widget_id)

                        # Log result
                        if result.success:
                            logger.info(
                                f"Widget '{widget_id}' activated successfully: {result.message}"
                            )
                        else:
                            logger.warning(
                                f"Widget '{widget_id}' activation failed: {result.message}"
                            )

                        return result

                    return activate_handler

                on_activate_fn = make_activate_fn(
                    script_widget.id, script_widget.on_activate, script_manager
                )

            # Map interaction types
            interaction_type = None
            if script_widget.interactive:
                interaction_type = (
                    script_widget.interaction_type.value
                    if script_widget.interaction_type
                    else "action"
                )

            # Determine category from script widget category string
            # Script widgets use strings like "git", "docker", "custom"
            # Map to WidgetCategory enum
            category_str = script_widget.category.lower()
            if category_str in ("core", "plugin"):
                category = WidgetCategory(category_str)
            else:
                # All script widgets are treated as PLUGIN category
                category = WidgetCategory.PLUGIN

            # Parse width specification
            default_width = WidgetWidth.auto()
            if script_widget.min_width > 10:
                default_width = WidgetWidth.fixed(script_widget.min_width)

            # Register the widget
            registry.register(
                id=script_widget.id,
                name=script_widget.name,
                description=script_widget.description,
                render_fn=make_render_fn(script_widget.id, script_manager),
                category=category,
                default_width=default_width,
                min_width=script_widget.min_width,
                interactive=script_widget.interactive,
                interaction_type=interaction_type,
                on_activate=on_activate_fn,  # Now properly wired!
            )

            registered_count += 1
            logger.info(
                f"Registered script widget '{script_widget.id}' "
                f"(category={category.value}, interactive={script_widget.interactive})"
            )

        except Exception as e:
            logger.error(f"Failed to register script widget '{script_widget.id}': {e}")

    logger.info(
        f"Registered {registered_count}/{len(discovered_widgets)} script widgets"
    )
    return registered_count
