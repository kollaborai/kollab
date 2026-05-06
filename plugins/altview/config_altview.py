"""System configuration editor as an AltView plugin.

Full-screen config editor with sections, real TUI widget instances
(CheckboxWidget, SliderWidget, DropdownWidget, TextInputWidget,
LabelWidget) using TagBox render_modern() for styled output.

Keyboard:
    Up/Down      navigate widgets within a section
    Tab          next section
    Shift+Tab    previous section
    Left/Right   adjust sliders, cycle dropdowns
    Enter/Space  toggle checkboxes, confirm dropdown
    /            activate search filter
    Ctrl+S       save (prompts local vs global)
    Esc          exit (clears search if active, else exits)
"""

import logging
from typing import Any, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, S, T, TagBox, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AltView implementation
# ---------------------------------------------------------------------------


class ConfigAltView(AltView):
    """AltView for full-screen system configuration editing.

    Uses real TUI widget instances with render_modern() for the same
    visual polish as the old modal system (TagBox, gradients, icons).
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="config",
            description="Open system configuration panel",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[CFG]",
            aliases=["settings", "preferences"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 15.0

        # App / config references (injected via set_app)
        self.app: Any = None
        self.config_service: Any = None

        # Data model  --  real widget instances grouped by section
        self._sections: List[str] = []
        self._section_widgets: List[List[Any]] = []  # widgets per section

        # Navigation
        self._sel_section: int = 0
        self._sel_widget: int = 0  # index into visible widgets of current section
        self._scroll_offset: int = 0

        # Search
        self._search_active: bool = False
        self._search_query: str = ""

        # Save prompt
        self._save_prompt: bool = False

    # -- external setup -----------------------------------------------------

    def set_app(self, app: Any) -> None:
        """Set app reference for config access."""
        self.app = app
        if hasattr(app, "config_service"):
            self.config_service = app.config_service
        elif hasattr(app, "config"):
            self.config_service = app.config

    # -- lifecycle ----------------------------------------------------------

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._load_widgets()
        total = sum(len(ws) for ws in self._section_widgets)
        logger.info(
            "ConfigAltView: entered with %d widgets across %d sections",
            total,
            len(self._sections),
        )

    async def on_complete(self) -> None:
        await super().on_complete()

    # -- widget creation (mirrors modal_renderer._create_widget) ------------

    def _create_widget(self, widget_config: dict) -> Any:
        """Create a real TUI widget instance from a config definition dict."""
        from kollabor_tui.widgets.checkbox import CheckboxWidget
        from kollabor_tui.widgets.dropdown import DropdownWidget
        from kollabor_tui.widgets.label import LabelWidget
        from kollabor_tui.widgets.slider import SliderWidget
        from kollabor_tui.widgets.text_input import TextInputWidget

        wtype = widget_config.get("type", "label")
        config_path = widget_config.get("config_path", "")

        # Read current value from config service
        current_value = None
        if self.config_service and config_path:
            current_value = self.config_service.get(config_path)

        cfg = widget_config.copy()
        if current_value is not None:
            cfg["current_value"] = current_value

        if wtype == "checkbox":
            return CheckboxWidget(cfg, config_path, self.config_service)
        elif wtype == "dropdown":
            return DropdownWidget(cfg, config_path, self.config_service)
        elif wtype == "text_input":
            return TextInputWidget(cfg, config_path, self.config_service)
        elif wtype == "slider":
            return SliderWidget(cfg, config_path, self.config_service)
        elif wtype == "label":
            return LabelWidget(
                label=widget_config.get("label", ""),
                value=widget_config.get("value", ""),
                help_text=widget_config.get("help", ""),
                config_path=config_path,
                current_value=current_value or widget_config.get("value", ""),
            )
        else:
            logger.warning("ConfigAltView: unknown widget type %r, using label", wtype)
            return LabelWidget(
                label=widget_config.get("label", wtype),
                value="",
                config_path=config_path,
            )

    # -- data loading -------------------------------------------------------

    def _load_widgets(self) -> None:
        """Load config definition and create real widget instances."""
        from kollabor_tui.config_widgets import ConfigWidgetDefinitions

        defn = ConfigWidgetDefinitions.get_config_modal_definition()
        sections = defn.get("sections", [])

        self._sections = []
        self._section_widgets = []
        self._sel_section = 0
        self._sel_widget = 0
        self._scroll_offset = 0

        for section in sections:
            title = section.get("title", "Section")
            widgets = []
            for wdef in section.get("widgets", []):
                try:
                    w = self._create_widget(wdef)
                    widgets.append(w)
                except Exception as e:
                    logger.error("ConfigAltView: failed to create widget: %s", e)
            self._sections.append(title)
            self._section_widgets.append(widgets)

        # Set initial focus
        vis = self._visible_widgets()
        if vis:
            vis[0].set_focus(True)

    # -- filtered views -----------------------------------------------------

    def _visible_sections(self) -> List[int]:
        """Section indices visible after search filter."""
        if not self._search_query:
            return list(range(len(self._sections)))

        q = self._search_query.lower()
        result = []
        for idx, title in enumerate(self._sections):
            if q in title.lower():
                result.append(idx)
                continue
            for w in self._section_widgets[idx]:
                label = w.get_label() if hasattr(w, "get_label") else ""
                help_text = w.config.get("help", "") if hasattr(w, "config") else ""
                config_path = w.config_path if hasattr(w, "config_path") else ""
                if (
                    q in label.lower()
                    or q in help_text.lower()
                    or q in config_path.lower()
                ):
                    result.append(idx)
                    break
        return result

    def _visible_widgets(self) -> List[Any]:
        """Widgets in the currently selected section, filtered by search."""
        vis_secs = self._visible_sections()
        if not vis_secs:
            return []

        sec_idx = vis_secs[min(self._sel_section, len(vis_secs) - 1)]
        q = self._search_query.lower() if self._search_query else ""

        if not q or q in self._sections[sec_idx].lower():
            # Section title matches or no filter  -- show all widgets
            return list(self._section_widgets[sec_idx])

        # Filter individual widgets
        result = []
        for w in self._section_widgets[sec_idx]:
            label = w.get_label() if hasattr(w, "get_label") else ""
            help_text = w.config.get("help", "") if hasattr(w, "config") else ""
            config_path = w.config_path if hasattr(w, "config_path") else ""
            if q in label.lower() or q in help_text.lower() or q in config_path.lower():
                result.append(w)
        return result

    def _current_widget(self) -> Optional[Any]:
        """The currently focused widget, or None."""
        vis = self._visible_widgets()
        if not vis:
            return None
        idx = min(self._sel_widget, len(vis) - 1)
        return vis[idx]

    # -- focus management ---------------------------------------------------

    def _update_focus(self, old_widget: Optional[Any] = None) -> None:
        """Clear old focus, set new focus on currently selected widget."""
        if old_widget is not None:
            old_widget.set_focus(False)
        vis = self._visible_widgets()
        if vis:
            idx = min(self._sel_widget, len(vis) - 1)
            vis[idx].set_focus(True)

    # -- dirty tracking -----------------------------------------------------

    def _dirty_count(self) -> int:
        """Count widgets with unsaved changes."""
        count = 0
        for ws in self._section_widgets:
            for w in ws:
                if hasattr(w, "has_pending_changes") and w.has_pending_changes():
                    count += 1
        return count

    # -- rendering ----------------------------------------------------------

    async def render_frame(self, delta_time: float) -> bool:
        if not self._renderer:
            return False

        width, height = self._renderer.get_terminal_size()
        theme = T()

        self._renderer.clear_screen()
        self._render_header(width, theme)

        content_top = 3
        footer_height = 3
        content_height = height - content_top - footer_height

        self._render_sections(width, content_top, content_height, theme)
        self._render_footer(width, height, theme)

        return True

    def _render_header(self, width: int, theme: Any) -> None:
        """Render the title bar."""
        assert self._renderer is not None  # guarded by render_frame
        title = " System Configuration"
        dirty = self._dirty_count()
        if dirty:
            title += f"  ({dirty} unsaved)"

        self._renderer.write_at(
            0,
            0,
            solid_fg(str(C["half_bottom"]) * width, theme.primary[0]),
            "",
        )
        self._renderer.write_at(
            0,
            1,
            solid(
                f"{S.BOLD}{title}{S.RESET_BOLD}".ljust(width),
                theme.primary[0],
                theme.text_dark,
                width,
            ),
            "",
        )
        self._renderer.write_at(
            0,
            2,
            solid_fg(str(C["half_top"]) * width, theme.primary[0]),
            "",
        )

    def _render_sections(self, width: int, top: int, height: int, theme: Any) -> None:
        """Render section tabs and widget list."""
        assert self._renderer is not None  # guarded by render_frame
        vis_secs = self._visible_sections()
        if not vis_secs:
            self._renderer.write_at(2, top + 1, "No matching settings.", "")
            return

        # Clamp section selection
        if self._sel_section >= len(vis_secs):
            self._sel_section = len(vis_secs) - 1

        # -- Search bar (if active) --
        y = top
        if self._search_active:
            search_box = TagBox.render(
                lines=[f" {S.BOLD}Filter:{S.RESET_BOLD} {self._search_query}\u2588"],
                tag_bg=theme.primary[0],
                tag_fg=theme.text_dark,
                tag_width=3,
                content_colors=theme.dark[0],
                content_fg=theme.text,
                content_width=width - 7,
                tag_chars=[" / "],
                use_gradient=False,
            )
            for line in search_box.split("\n"):
                self._renderer.write_at(0, y, f"  {line}", "")
                y += 1

        # -- Section header with counter (replaces horizontal tab bar) --
        sec_idx = vis_secs[min(self._sel_section, len(vis_secs) - 1)]
        sec_title = self._sections[sec_idx]
        sec_counter = f"[{self._sel_section + 1}/{len(vis_secs)}]"
        header_box = TagBox.render(
            lines=[
                f" {S.BOLD}{sec_title}{S.RESET_BOLD}  {S.DIM}{sec_counter}  Tab/Shift+Tab to switch{S.RESET_DIM}"
            ],
            tag_bg=theme.primary[0],
            tag_fg=theme.text_dark,
            tag_width=3,
            content_colors=theme.dark[0],
            content_fg=theme.text,
            content_width=width - 7,
            tag_chars=[" \u25a0 "],
            use_gradient=False,
        )
        for line in header_box.split("\n"):
            self._renderer.write_at(0, y, f"  {line}", "")
            y += 1

        # -- Widgets for selected section via render_modern --
        vis_widgets = self._visible_widgets()
        if not vis_widgets:
            self._renderer.write_at(2, y + 1, "No settings in this section.", "")
            return

        # Clamp widget selection
        if self._sel_widget >= len(vis_widgets):
            self._sel_widget = len(vis_widgets) - 1

        # Each widget's render_modern returns multi-line TagBox output.
        # We pre-render to count lines, then paginate.
        rendered_widgets: List[List[str]] = []
        for wi, w in enumerate(vis_widgets):
            num_in_section = len(vis_widgets)
            if num_in_section == 1:
                position = "only"
            elif wi == 0:
                position = "first"
            elif wi == num_in_section - 1:
                position = "last"
            else:
                position = "middle"

            render_width = width - 4
            lines = self._render_widget_modern(w, render_width, position)
            rendered_widgets.append(lines)

        # Calculate available space and pagination
        available = height - (y - top) - 1
        widget_heights = [len(lines) for lines in rendered_widgets]

        # Scroll: ensure selected widget is visible
        if self._sel_widget < self._scroll_offset:
            self._scroll_offset = self._sel_widget

        # Calculate how many widgets fit from scroll_offset
        visible_height = 0
        last_visible = self._scroll_offset
        for i in range(self._scroll_offset, len(rendered_widgets)):
            h = widget_heights[i]
            if visible_height + h > available and i > self._scroll_offset:
                break
            visible_height += h
            last_visible = i

        if self._sel_widget > last_visible:
            # Scroll forward to make selected visible
            self._scroll_offset = self._sel_widget
            # Re-calculate from new offset
            visible_height = 0
            for i in range(self._scroll_offset, len(rendered_widgets)):
                h = widget_heights[i]
                if visible_height + h > available and i > self._scroll_offset:
                    break
                visible_height += h

        self._scroll_offset = max(0, self._scroll_offset)

        # Render visible widgets
        for wi in range(self._scroll_offset, len(rendered_widgets)):
            lines = rendered_widgets[wi]
            if y - top + len(lines) > available:
                break
            for line in lines:
                self._renderer.write_at(2, y, line, "")
                y += 1

        # Scroll indicator
        if len(vis_widgets) > last_visible - self._scroll_offset + 1:
            end_idx = min(last_visible + 1, len(vis_widgets))
            indicator = f" [{self._scroll_offset + 1}-{end_idx}/{len(vis_widgets)}]"
            self._renderer.write_at(
                width - len(indicator) - 1,
                top + 2,
                f"{S.DIM}{indicator}{S.RESET_DIM}",
                "",
            )

    def _render_widget_modern(
        self, widget: Any, width: int, position: str
    ) -> List[str]:
        """Call render_modern() on a real widget and return output lines."""
        try:
            if hasattr(widget, "render_modern"):
                output = widget.render_modern(width=width, position=position)
                # render_modern returns list of multi-line strings
                lines = []
                for block in output:
                    for line in block.split("\n"):
                        if line:
                            lines.append(line)
                return lines if lines else [""]
            else:
                # Fallback to basic render
                return widget.render()  # type: ignore[no-any-return]
        except Exception as e:
            logger.error("ConfigAltView: render_modern failed: %s", e)
            label = widget.get_label() if hasattr(widget, "get_label") else "?"
            return [f"  {label}: (render error)"]

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        """Render the footer bar."""
        assert self._renderer is not None  # guarded by render_frame
        footer_y = height - 3

        if self._save_prompt:
            warn = theme.warning[0] if hasattr(theme, "warning") else (200, 150, 0)
            self._renderer.write_at(
                0,
                footer_y,
                solid_fg(str(C["half_bottom"]) * width, warn),
                "",
            )
            prompt_text = " Save to: (L)ocal  (G)lobal  (Esc) cancel"
            self._renderer.write_at(
                0,
                footer_y + 1,
                solid(prompt_text.ljust(width), warn, theme.text_dark, width),
                "",
            )
            self._renderer.write_at(
                0,
                footer_y + 2,
                solid_fg(str(C["half_top"]) * width, warn),
                "",
            )
        else:
            self._renderer.write_at(
                0,
                footer_y,
                solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
                "",
            )
            if self._search_active:
                hint = " Type to filter | Esc exit search | Enter select"
            else:
                keys = (
                    "Up/Dn navigate  "
                    "Tab section  "
                    "Enter/Space toggle  "
                    "/ search  "
                    "Ctrl+S save  "
                    "Esc exit"
                )
                hint = f" {keys}"

            # Activity badge: show buffered message count from agent
            badge = self._get_activity_badge()
            if badge:
                # Truncate hint to make room for badge on the right
                max_hint = width - len(badge) - 2
                hint = hint[:max_hint]
                hint = hint + " " * (width - len(hint) - len(badge)) + badge

            self._renderer.write_at(
                0,
                footer_y + 1,
                solid(hint.ljust(width), theme.dark[1], theme.text_dim, width),
                "",
            )
            self._renderer.write_at(
                0,
                footer_y + 2,
                solid_fg(str(C["half_top"]) * width, theme.dark[1]),
                "",
            )

    def _get_activity_badge(self) -> str:
        """Return a badge string like '[3 new]' if messages are buffered."""
        try:
            if self.app and hasattr(self.app, "renderer"):
                coord = getattr(self.app.renderer, "message_coordinator", None)
                if coord:
                    count = coord.buffered_output_count
                    if count > 0:
                        return f"[{count} new]"
        except Exception:
            pass
        return ""

    # -- input handling -----------------------------------------------------

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle key input. Returns True to exit."""

        # Save prompt mode
        if self._save_prompt:
            return self._handle_save_prompt(key_press)

        # Search mode
        if self._search_active:
            return self._handle_search_input(key_press)

        # Normal navigation
        return self._handle_navigation(key_press)

    def _handle_save_prompt(self, kp: KeyPress) -> bool:
        """Handle input during save target prompt."""
        if kp.char and kp.char.lower() == "l":
            self._do_save("local")
            self._save_prompt = False
            return False
        elif kp.char and kp.char.lower() == "g":
            self._do_save("global")
            self._save_prompt = False
            return False
        elif kp.name == "Escape":
            self._save_prompt = False
            return False
        elif kp.name == "Enter":
            self._do_save("local")
            self._save_prompt = False
            return False
        return False

    def _handle_search_input(self, kp: KeyPress) -> bool:
        """Handle input during search filter mode."""
        if kp.name == "Escape":
            self._search_active = False
            self._search_query = ""
            self._sel_section = 0
            self._sel_widget = 0
            self._scroll_offset = 0
            self._update_focus()
            return False
        elif kp.name == "Enter":
            self._search_active = False
            return False
        elif kp.name == "Backspace":
            if self._search_query:
                self._search_query = self._search_query[:-1]
                self._sel_section = 0
                self._sel_widget = 0
                self._scroll_offset = 0
                self._update_focus()
            return False
        elif kp.char and len(kp.char) == 1 and ord(kp.char) >= 32:
            self._search_query += kp.char
            self._sel_section = 0
            self._sel_widget = 0
            self._scroll_offset = 0
            self._update_focus()
            return False
        return False

    def _handle_navigation(self, kp: KeyPress) -> bool:
        """Handle normal navigation input."""
        vis_widgets = self._visible_widgets()
        vis_sections = self._visible_sections()

        # Escape: exit
        if kp.name == "Escape":
            return True

        # Ctrl+S: save
        if kp.name == "Ctrl+S":
            self._save_prompt = True
            return False

        # / : search
        if kp.char == "/":
            self._search_active = True
            self._search_query = ""
            return False

        # Tab / Shift+Tab: section navigation
        if kp.name == "Shift+Tab":
            old = self._current_widget()
            self._sel_section = max(0, self._sel_section - 1)
            self._sel_widget = 0
            self._scroll_offset = 0
            self._update_focus(old)
            return False
        if kp.name == "Tab":
            old = self._current_widget()
            self._sel_section = min(len(vis_sections) - 1, self._sel_section + 1)
            self._sel_widget = 0
            self._scroll_offset = 0
            self._update_focus(old)
            return False

        # Up/Down: widget navigation
        if kp.name == "ArrowUp":
            if vis_widgets and self._sel_widget > 0:
                old = self._current_widget()
                self._sel_widget -= 1
                self._update_focus(old)
            return False
        if kp.name == "ArrowDown":
            if vis_widgets and self._sel_widget < len(vis_widgets) - 1:
                old = self._current_widget()
                self._sel_widget += 1
                self._update_focus(old)
            return False

        # Delegate remaining keys to the focused widget
        w = self._current_widget()
        if w and hasattr(w, "handle_input"):
            w.handle_input(kp)

        return False

    # -- save ---------------------------------------------------------------

    def _do_save(self, target: str) -> None:
        """Persist dirty widget values to config."""
        try:
            if not self.config_service:
                logger.warning("ConfigAltView: no config_service, cannot save")
                return

            saved = 0
            new_profile = None
            dirty_values: List[tuple[str, Any]] = []
            for ws in self._section_widgets:
                for w in ws:
                    if hasattr(w, "has_pending_changes") and w.has_pending_changes():
                        if hasattr(w, "config_path") and w.config_path:
                            val = w.get_pending_value()
                            self.config_service.set(w.config_path, val)
                            dirty_values.append((w.config_path, val))
                            saved += 1
                            logger.debug(
                                "ConfigAltView: set %s = %r", w.config_path, val
                            )
                            if w.config_path == "kollabor.llm.active_profile":
                                new_profile = val

            ok = True
            for key_path, val in dirty_values:
                if not self.config_service.save_key(
                    key_path, val, save_target=target
                ):
                    ok = False

            if ok:
                logger.info("ConfigAltView: saved %d changes to %s", saved, target)
                for ws in self._section_widgets:
                    for w in ws:
                        if (
                            hasattr(w, "has_pending_changes")
                            and w.has_pending_changes()
                        ):
                            w._pending_value = None
            else:
                logger.error("ConfigAltView: one or more config saves failed")

            # If profile changed, switch it at runtime so the running app updates
            if new_profile and self.app:
                llm = getattr(self.app, "llm_service", None)
                if llm and hasattr(llm, "switch_profile"):
                    import asyncio

                    asyncio.ensure_future(llm.switch_profile(new_profile))
                    logger.info(
                        "ConfigAltView: triggered runtime profile switch -> %s",
                        new_profile,
                    )

        except Exception as e:
            logger.error("ConfigAltView: save failed: %s", e, exc_info=True)
