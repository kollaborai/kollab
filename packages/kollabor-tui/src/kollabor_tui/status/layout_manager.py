"""Status layout manager for loading and saving status area configuration.

Manages the persistent layout configuration that defines how widgets
are arranged in the status area.
"""

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .widget_registry import WidgetWidth

logger = logging.getLogger(__name__)


@dataclass
class WidgetConfig:
    """Configuration for a single widget in a row.

    Attributes:
        id: Widget identifier
        width: Width specification
        color: Background color (none|dark0|dark1|primary0|secondary0)
        effect: Visual effect (none|shimmer|pulse)
        config: Widget-specific configuration (e.g., label text, toggle state)
    """

    id: str
    width: WidgetWidth = field(default_factory=WidgetWidth.auto)
    color: str = "none"
    effect: str = "none"
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "id": self.id,
            "width": self.width.to_dict(),
            "color": self.color,
            "effect": self.effect,
        }
        if self.config:
            result["config"] = self.config
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WidgetConfig":
        """Create from dictionary."""
        # Map old color names to new ones for backward compatibility
        old_color = data.get("color", "none")
        color_map = {
            "default": "none",
            "primary": "primary0",
            "accent": "secondary0",
            "dark": "dark0",
        }
        color = color_map.get(old_color, old_color)

        return cls(
            id=data["id"],
            width=WidgetWidth.from_dict(data.get("width", {"type": "auto"})),
            color=color,
            effect=data.get("effect", "none"),
            config=data.get("config", {}),
        )


@dataclass
class RowConfig:
    """Configuration for a single row in the status area.

    Attributes:
        id: Row number (1-6)
        visible: Whether this row is rendered
        widgets: List of widgets in this row
    """

    id: int
    visible: bool = True
    widgets: List[WidgetConfig] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "visible": self.visible,
            "widgets": [w.to_dict() for w in self.widgets],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RowConfig":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            visible=data.get("visible", True),
            widgets=[WidgetConfig.from_dict(w) for w in data.get("widgets", [])],
        )


@dataclass
class StatusLayout:
    """Complete status area layout configuration.

    Attributes:
        version: Configuration version for migrations
        rows: List of row configurations
        backup: Previous configuration for restore
    """

    version: int = 1
    rows: List[RowConfig] = field(default_factory=list)
    backup: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization.

        Note: backup is intentionally excluded from serialization.
        It is a runtime-only field used for in-session restore, not
        persisted to config.json.
        """
        return {
            "version": self.version,
            "rows": [r.to_dict() for r in self.rows],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StatusLayout":
        """Create from dictionary."""
        return cls(
            version=data.get("version", 1),
            rows=[RowConfig.from_dict(r) for r in data.get("rows", [])],
            backup=data.get("backup"),
        )

    def get_visible_rows(self) -> List[RowConfig]:
        """Get only visible rows."""
        return [r for r in self.rows if r.visible]

    def get_row(self, row_id: int) -> Optional[RowConfig]:
        """Get a row by ID."""
        for row in self.rows:
            if row.id == row_id:
                return row
        return None

    def add_row(self) -> Optional[RowConfig]:
        """Add a new row (up to 6 max).

        Returns:
            New RowConfig if added, None if at max
        """
        if len(self.rows) >= 6:
            return None

        new_id = len(self.rows) + 1
        new_row = RowConfig(id=new_id, visible=False, widgets=[])
        self.rows.append(new_row)
        return new_row


class StatusLayoutManager:
    """Manages status layout configuration.

    Handles loading, saving, and manipulating the status area layout
    configuration stored in the application config.
    """

    CONFIG_KEY = "status_layout"

    def __init__(self, config_service: Any = None):
        """Initialize the layout manager.

        Args:
            config_service: Application config service for persistence
        """
        self._config_service = config_service
        self._config_manager = (
            getattr(config_service, "config_manager", None) if config_service else None
        )
        self._config_loader = (
            getattr(config_service, "config_loader", None) if config_service else None
        )
        self._layout: Optional[StatusLayout] = None
        self._dirty = False
        logger.info("StatusLayoutManager initialized")

    def set_config_manager(self, config_manager: Any) -> None:
        """Set the config manager for persistence."""
        self._config_manager = config_manager

    def reload(self) -> StatusLayout:
        """Force reload layout from config.

        Returns:
            Fresh StatusLayout instance
        """
        self._layout = None
        return self.load()

    def load(self) -> StatusLayout:
        """Load layout from config, layout JSON file, or hardcoded default.

        Resolution order:
          1. config.json status_layout key (user has saved via /config or tab+E)
          2. ~/.kollab/layouts/default.json (bundled seed or user-edited)
          3. Hardcoded Python default

        Returns:
            StatusLayout instance
        """
        if self._layout is not None:
            return self._layout

        # Try to load from config
        if self._config_manager:
            try:
                data = self._config_manager.get(self.CONFIG_KEY)
                if data:
                    self._layout = StatusLayout.from_dict(data)
                    logger.info(
                        f"Loaded status layout from config with {len(self._layout.rows)} rows"
                    )
                    self._ensure_six_rows()
                    return self._layout
            except Exception as e:
                logger.error(f"Error loading status layout from config: {e}")

        # Try to load from layout JSON file
        loaded = self._load_from_layout_file()
        if loaded:
            self._layout = loaded
            self._ensure_six_rows()
            return self._layout

        # Create default layout
        self._layout = self._create_default_layout()
        logger.info("Created default status layout (hardcoded fallback)")
        return self._layout

    def _load_from_layout_file(self) -> Optional[StatusLayout]:
        """Try to load layout from ~/.kollab/layouts/default.json.

        Returns:
            StatusLayout if file exists and is valid, None otherwise.
        """
        import json
        from kollabor_config.config_utils import resolve_global_path

        layout_file = resolve_global_path("layouts", "default.json")
        if not layout_file.exists():
            return None

        try:
            data = json.loads(layout_file.read_text(encoding="utf-8"))
            layout = StatusLayout.from_dict(data)
            logger.info(f"Loaded status layout from {layout_file}")
            return layout
        except Exception as e:
            logger.warning(f"Failed to load layout from {layout_file}: {e}")
            return None

    def _ensure_six_rows(self) -> None:
        """Ensure layout has all 6 rows (adds missing rows as hidden/empty).

        This handles migration from older configs that only had 4 rows.
        """
        if not self._layout:
            return

        existing_ids = {row.id for row in self._layout.rows}

        for row_id in range(1, 7):
            if row_id not in existing_ids:
                new_row = RowConfig(id=row_id, visible=False, widgets=[])
                self._layout.rows.append(new_row)
                logger.info(f"Added missing row {row_id} during migration")

        # Sort rows by ID to maintain order
        self._layout.rows.sort(key=lambda r: r.id)

    def _create_default_layout(self) -> StatusLayout:
        """Create the default status layout.

        Returns:
            Default StatusLayout with standard widgets
        """
        # Default row 1: directory and git
        row1 = RowConfig(
            id=1,
            visible=True,
            widgets=[
                WidgetConfig(id="cwd", width=WidgetWidth.auto()),
                WidgetConfig(id="git-branch", width=WidgetWidth.auto()),
                WidgetConfig(id="git-status", width=WidgetWidth.auto()),
            ],
        )

        # Default row 2: LLM status
        row2 = RowConfig(
            id=2,
            visible=True,
            widgets=[
                WidgetConfig(id="profile", width=WidgetWidth.auto()),
                WidgetConfig(id="model", width=WidgetWidth.auto()),
                WidgetConfig(id="status", width=WidgetWidth.auto()),
                WidgetConfig(id="stats", width=WidgetWidth.auto()),
            ],
        )

        # Default row 3: agent/session/connectivity
        row3 = RowConfig(
            id=3,
            visible=True,
            widgets=[
                WidgetConfig(id="hub", width=WidgetWidth.auto()),
                WidgetConfig(id="agent", width=WidgetWidth.auto()),
                WidgetConfig(id="skills", width=WidgetWidth.auto()),
                WidgetConfig(id="session", width=WidgetWidth.auto()),
                WidgetConfig(id="mcp", width=WidgetWidth.auto()),
            ],
        )

        # Default row 4: tasks/activity/system
        row4 = RowConfig(
            id=4,
            visible=True,
            widgets=[
                WidgetConfig(id="tasks", width=WidgetWidth.auto()),
                WidgetConfig(id="tmux", width=WidgetWidth.auto()),
                WidgetConfig(id="bg-tasks", width=WidgetWidth.auto()),
                WidgetConfig(id="deep-thought", width=WidgetWidth.auto()),
                WidgetConfig(id="altview", width=WidgetWidth.auto()),
                WidgetConfig(id="token-io", width=WidgetWidth.auto()),
                WidgetConfig(id="sysmon", width=WidgetWidth.auto()),
            ],
        )

        # Default row 5: empty (hidden) - available for user customization
        row5 = RowConfig(id=5, visible=False, widgets=[])

        # Default row 6: empty (hidden) - available for user customization
        row6 = RowConfig(id=6, visible=False, widgets=[])

        return StatusLayout(
            version=1,
            rows=[row1, row2, row3, row4, row5, row6],
        )

    def save(self) -> bool:
        """Save current layout to config.

        Returns:
            True if saved successfully
        """
        if not self._config_service:
            logger.warning("No config service - cannot save layout")
            return False

        if not self._layout:
            logger.warning("No layout to save")
            return False

        try:
            # Backup current config before saving
            current_data = self._config_service.get(self.CONFIG_KEY)
            if current_data:
                self._layout.backup = current_data

            # Update in-memory config
            self._config_service.set(self.CONFIG_KEY, self._layout.to_dict())

            # Persist to disk using config_loader (handles layered config paths)
            if self._config_loader:
                success = self._config_loader.save_merged_config(
                    self._config_service.config_manager.config
                )
            else:
                # Fallback to config_manager if config_loader not available
                success = (
                    self._config_manager.save_config_file(self._config_manager.config)
                    if self._config_manager
                    else False
                )

            if success:
                self._dirty = False
                logger.info("Status layout saved")
                return True
            return False
        except Exception as e:
            logger.error(f"Error saving status layout: {e}")
            return False

    def restore_backup(self) -> bool:
        """Restore layout from backup.

        Returns:
            True if restored successfully
        """
        if not self._layout or not self._layout.backup:
            logger.warning("No backup to restore")
            return False

        try:
            backup_data = self._layout.backup
            self._layout = StatusLayout.from_dict(backup_data)
            self._layout.backup = None  # Clear backup reference
            self._dirty = True
            logger.info("Status layout restored from backup")
            return True
        except Exception as e:
            logger.error(f"Error restoring backup: {e}")
            return False

    def reset_to_default(self) -> None:
        """Reset layout to default configuration."""
        # Backup current before reset
        if self._layout:
            self._layout.backup = self._layout.to_dict()

        self._layout = self._create_default_layout()
        self._dirty = True
        logger.info("Status layout reset to default")

    def get_layout(self) -> StatusLayout:
        """Get current layout (loads if needed)."""
        if self._layout is None:
            return self.load()
        return self._layout

    def is_dirty(self) -> bool:
        """Check if layout has unsaved changes."""
        return self._dirty

    def mark_dirty(self) -> None:
        """Mark layout as having unsaved changes."""
        self._dirty = True

    # =========================================================================
    # LAYOUT MANIPULATION METHODS
    # =========================================================================

    def add_widget_to_row(
        self, row_id: int, widget_id: str, width: Optional[WidgetWidth] = None
    ) -> bool:
        """Add a widget to a row.

        Automatically makes the row visible if it was hidden.

        Args:
            row_id: Row to add widget to
            widget_id: Widget identifier
            width: Optional width specification

        Returns:
            True if added successfully
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            logger.warning(f"Row {row_id} not found")
            return False

        if width is None:
            width = WidgetWidth.auto()

        widget = WidgetConfig(id=widget_id, width=width)
        row.widgets.append(widget)

        # Auto-show row when widget is added
        if not row.visible:
            row.visible = True
            logger.info(f"Made row {row_id} visible when adding widget '{widget_id}'")

        self._dirty = True
        logger.info(f"Added widget '{widget_id}' to row {row_id}")
        return True

    def insert_widget_at_position(
        self,
        row_id: int,
        widget_id: str,
        position: int,
        width: Optional[WidgetWidth] = None,
    ) -> bool:
        """Insert a widget at a specific position in a row.

        Automatically makes the row visible if it was hidden.

        Args:
            row_id: Row to insert widget into
            widget_id: Widget identifier
            position: Position to insert at (0 = before first widget, 1 = after first widget, etc.)
            width: Optional width specification

        Returns:
            True if inserted successfully
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            logger.warning(f"Row {row_id} not found")
            return False

        if position < 0 or position > len(row.widgets):
            logger.warning(f"Position {position} out of range (0-{len(row.widgets)})")
            return False

        if width is None:
            width = WidgetWidth.auto()

        widget = WidgetConfig(id=widget_id, width=width)
        row.widgets.insert(position, widget)

        # Auto-show row when widget is added
        if not row.visible:
            row.visible = True
            logger.info(f"Made row {row_id} visible when adding widget '{widget_id}'")

        self._dirty = True
        logger.info(
            f"Inserted widget '{widget_id}' at position {position} in row {row_id}"
        )
        return True

    def remove_widget_from_row(self, row_id: int, widget_index: int) -> bool:
        """Remove a widget from a row by index.

        Args:
            row_id: Row to remove widget from
            widget_index: Index of widget in row

        Returns:
            True if removed successfully
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            logger.warning(f"Row {row_id} not found")
            return False

        if widget_index < 0 or widget_index >= len(row.widgets):
            logger.warning(f"Widget index {widget_index} out of range")
            return False

        removed = row.widgets.pop(widget_index)
        self._dirty = True
        logger.info(f"Removed widget '{removed.id}' from row {row_id}")
        return True

    def set_widget_color(self, row_id: int, widget_index: int, color: str) -> bool:
        """Set the background color for a widget.

        Args:
            row_id: Row containing the widget
            widget_index: Index of widget in row
            color: Color value ("none", "dark0", "dark1", "primary0", "secondary0")

        Returns:
            True if color was set successfully
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            logger.warning(f"Row {row_id} not found")
            return False

        if widget_index < 0 or widget_index >= len(row.widgets):
            logger.warning(f"Widget index {widget_index} out of range")
            return False

        widget_config = row.widgets[widget_index]
        widget_config.color = color
        self._dirty = True
        logger.info(f"Set widget '{widget_config.id}' color to {color}")
        return True

    def move_widget_in_row(self, row_id: int, from_index: int, to_index: int) -> bool:
        """Move a widget within a row.

        Args:
            row_id: Row containing the widget
            from_index: Current position
            to_index: New position

        Returns:
            True if moved successfully
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            return False

        if from_index < 0 or from_index >= len(row.widgets):
            return False

        if to_index < 0:
            to_index = 0
        if to_index >= len(row.widgets):
            to_index = len(row.widgets) - 1

        widget = row.widgets.pop(from_index)
        row.widgets.insert(to_index, widget)
        self._dirty = True
        logger.info(
            f"Moved widget '{widget.id}' from {from_index} to {to_index} in row {row_id}"
        )
        return True

    def set_row_visibility(self, row_id: int, visible: bool) -> bool:
        """Set row visibility.

        Args:
            row_id: Row to modify
            visible: Whether row should be visible

        Returns:
            True if changed successfully
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            return False

        # Don't allow hiding rows with widgets
        if not visible and row.widgets:
            logger.warning(f"Cannot hide row {row_id} - has widgets")
            return False

        row.visible = visible
        self._dirty = True
        logger.info(f"Set row {row_id} visibility to {visible}")
        return True

    def set_widget_width(
        self, row_id: int, widget_index: int, width: WidgetWidth
    ) -> bool:
        """Set widget width.

        Args:
            row_id: Row containing widget
            widget_index: Widget index in row
            width: New width specification

        Returns:
            True if changed successfully
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            return False

        if widget_index < 0 or widget_index >= len(row.widgets):
            return False

        row.widgets[widget_index].width = width
        self._dirty = True
        logger.info(f"Set widget width in row {row_id} at index {widget_index}")
        return True

    def toggle_widget_color(self, row_id: int, widget_idx: int) -> bool:
        """Toggle widget background color.

        Cycles through: none -> dark0 -> dark1 -> primary0 -> secondary0 -> none

        Args:
            row_id: Row containing widget
            widget_idx: Widget index in row

        Returns:
            True if toggled successfully, False otherwise
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            return False

        if widget_idx < 0 or widget_idx >= len(row.widgets):
            return False

        # Color cycle: none -> dark0 -> dark1 -> primary0 -> secondary0 -> none
        from .constants import WIDGET_BG_COLOR_NAMES

        color_cycle = WIDGET_BG_COLOR_NAMES
        widget = row.widgets[widget_idx]

        # Find current color index, default to 0 if not found
        try:
            current_idx = color_cycle.index(widget.color)
        except ValueError:
            current_idx = 0

        # Cycle to next color
        next_idx = (current_idx + 1) % len(color_cycle)
        widget.color = color_cycle[next_idx]

        self._dirty = True
        logger.info(
            f"Toggled widget color to '{widget.color}' in row {row_id} at index {widget_idx}"
        )
        return True

    def cycle_widget_effect(self, row_id: int, widget_idx: int) -> bool:
        """Cycle widget effect to next value.

        Args:
            row_id: Row ID containing the widget
            widget_idx: Widget index in the row

        Returns:
            True if cycled successfully, False otherwise
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            return False

        if widget_idx < 0 or widget_idx >= len(row.widgets):
            return False

        # Effect cycle: none -> ultra -> shimmer -> pulse -> none
        from .constants import WIDGET_EFFECT_NAMES

        effect_cycle = WIDGET_EFFECT_NAMES
        widget = row.widgets[widget_idx]

        # Find current effect index, default to 0 if not found
        try:
            current_idx = effect_cycle.index(widget.effect)
        except ValueError:
            current_idx = 0

        # Cycle to next effect
        next_idx = (current_idx + 1) % len(effect_cycle)
        widget.effect = effect_cycle[next_idx]

        self._dirty = True
        logger.info(
            f"Toggled widget effect to '{widget.effect}' in row {row_id} at index {widget_idx}"
        )
        return True

    def update_widget_config(
        self, row_id: int, widget_index: int, key: str, value: Any
    ) -> bool:
        """Update a widget's custom configuration.

        Args:
            row_id: Row containing the widget
            widget_index: Index of widget in row
            key: Config key to update
            value: New value

        Returns:
            True if updated, False otherwise
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            logger.warning(f"Row {row_id} not found")
            return False

        if widget_index < 0 or widget_index >= len(row.widgets):
            logger.warning(f"Widget index {widget_index} out of range in row {row_id}")
            return False

        widget = row.widgets[widget_index]
        widget.config[key] = value

        self._dirty = True
        logger.info(f"Updated widget '{widget.id}' config: {key}={value}")
        return True

    def get_widget_config(
        self, row_id: int, widget_index: int, key: str, default: Any = None
    ) -> Any:
        """Get a widget's custom configuration value.

        Args:
            row_id: Row containing the widget
            widget_index: Index of widget in row
            key: Config key to retrieve
            default: Default value if not found

        Returns:
            Config value or default
        """
        layout = self.get_layout()
        row = layout.get_row(row_id)

        if not row:
            return default

        if widget_index < 0 or widget_index >= len(row.widgets):
            return default

        widget = row.widgets[widget_index]
        return widget.config.get(key, default)

    def add_new_row(self) -> Optional[int]:
        """Add a new row to the layout.

        Returns:
            New row ID if added, None if at max
        """
        layout = self.get_layout()
        new_row = layout.add_row()

        if new_row:
            self._dirty = True
            logger.info(f"Added new row {new_row.id}")
            return new_row.id

        logger.warning("Cannot add row - at maximum (6)")
        return None

    def get_config_snapshot(self) -> Dict[str, Any]:
        """Get deep copy of current config for undo snapshot.

        Returns:
            Deep copy of layout config as dictionary
        """

        layout = self.get_layout()
        return copy.deepcopy(layout.to_dict())

    def restore_config_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Restore config from undo snapshot.

        Args:
            snapshot: Deep copy of layout config to restore
        """

        self._layout = StatusLayout.from_dict(copy.deepcopy(snapshot))
        self._dirty = True
        logger.info("Layout restored from snapshot")
