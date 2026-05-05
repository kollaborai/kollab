"""MCP server selection wizard as an AltView plugin.

Replaces the manual alternate-buffer management in MCPSetupWizard with
the standard AltView lifecycle. The command handler creates an instance,
sets config data via set_config(), pushes the view onto the stack, and
reads selected_servers after exit.

Keyboard:
    Up/Down    navigate server list
    Space      toggle server enabled/disabled
    Enter      confirm selection
    Esc        cancel (returns empty dict)
"""

import logging
from typing import Any, Dict, List

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class McpWizardAltView(AltView):
    """AltView for interactive MCP server selection.

    After the view exits, read ``selected_servers`` for the result.
    An empty dict means the user cancelled.
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="mcp-wizard",
            description="MCP server selection wizard",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[MCP]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 15.0

        # Config data -- set via set_config() before push
        self._example_config: Dict[str, Any] = {}
        self._current_config: Dict[str, Any] = {}

        # UI state
        self._server_list: List[Dict[str, Any]] = []
        self._current_index: int = 0
        self._cancelled: bool = False
        self._confirmed: bool = False

        # Result -- read by the command handler after exit
        self.selected_servers: Dict[str, Dict] = {}

    def set_config(
        self,
        example_config: Dict[str, Any],
        current_config: Dict[str, Any],
    ) -> None:
        """Provide the example and current MCP configs before pushing.

        Args:
            example_config: Example configuration with all available servers.
            current_config: Current user configuration.
        """
        self._example_config = example_config
        self._current_config = current_config

    # -- lifecycle --

    async def on_enter(self, renderer: Any) -> None:
        """Build the server list from the provided configs."""
        self._renderer = renderer
        self._cancelled = False
        self._confirmed = False
        self.selected_servers = {}

        example_servers = self._example_config.get("servers", {})
        current_servers = self._current_config.get("servers", {})

        self._server_list = []
        for name, config in sorted(example_servers.items()):
            description = config.get("description", "No description")
            currently_enabled = current_servers.get(name, {}).get("enabled", False)
            self._server_list.append(
                {
                    "name": name,
                    "description": description,
                    "enabled": currently_enabled,
                    "config": config,
                }
            )

        self._current_index = 0
        logger.info("McpWizardAltView: entered with %d servers", len(self._server_list))

    async def render_frame(self, delta_time: float) -> bool:
        """Render the server selection list."""
        if not self._renderer:
            return False

        if self._cancelled or self._confirmed:
            return False

        width, height = self._renderer.get_terminal_size()
        theme = T()

        self._renderer.clear_screen()
        self._render_wizard(width, height, theme)

        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle navigation and selection keys.

        Returns True when the view should exit.
        """
        if not self._server_list:
            # nothing to select -- any key exits
            self._cancelled = True
            return True

        # escape -- cancel
        if key_press.name == "Escape" or key_press.char == "\x1b":
            self._cancelled = True
            self.selected_servers = {}
            logger.info("McpWizardAltView: cancelled")
            return True

        # enter -- confirm
        if key_press.name == "Enter" or key_press.char in ("\r", "\n"):
            self._collect_selected()
            self._confirmed = True
            logger.info(
                "McpWizardAltView: confirmed, %d servers selected",
                len(self.selected_servers),
            )
            return True

        # space -- toggle
        if key_press.char == " ":
            entry = self._server_list[self._current_index]
            entry["enabled"] = not entry["enabled"]
            return False

        # arrow up
        if key_press.name == "ArrowUp":
            self._current_index = max(0, self._current_index - 1)
            return False

        # arrow down
        if key_press.name == "ArrowDown":
            self._current_index = min(
                len(self._server_list) - 1, self._current_index + 1
            )
            return False

        # page up -- jump 5
        if key_press.name == "PageUp":
            self._current_index = max(0, self._current_index - 5)
            return False

        # page down -- jump 5
        if key_press.name == "PageDown":
            self._current_index = min(
                len(self._server_list) - 1, self._current_index + 5
            )
            return False

        # home
        if key_press.name == "Home":
            self._current_index = 0
            return False

        # end
        if key_press.name == "End":
            self._current_index = max(0, len(self._server_list) - 1)
            return False

        return False

    async def on_complete(self) -> None:
        """Clean up."""
        await super().on_complete()

    # -- result collection --

    def _collect_selected(self) -> None:
        """Populate selected_servers from the current toggle state."""
        self.selected_servers = {}
        for entry in self._server_list:
            if entry["enabled"]:
                self.selected_servers[entry["name"]] = entry["config"].copy()
                self.selected_servers[entry["name"]]["enabled"] = True

    # -- rendering --

    def _render_wizard(self, width: int, height: int, theme: Any) -> None:
        """Draw the full wizard UI."""
        assert self._renderer is not None  # guarded by render_frame
        # -- header bar --
        header_text = " MCP Server Setup Wizard "
        self._renderer.write_at(
            0,
            0,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        self._renderer.write_at(
            0,
            1,
            solid(header_text.ljust(width), theme.dark[1], theme.text, width),
            "",
        )

        y = 3

        # -- instructions --
        instructions = "Select MCP Servers to Enable"
        self._renderer.write_at(2, y, instructions, "")
        y += 1
        self._renderer.write_at(2, y, str(C["line_h"]) * min(50, width - 4), "")
        y += 2

        # -- server list (scrollable) --
        list_height = height - y - 4  # leave room for footer
        visible_count = max(1, list_height // 3)  # each entry ~3 lines

        # scroll offset so current_index is always visible
        scroll_offset = 0
        if self._current_index >= visible_count:
            scroll_offset = self._current_index - visible_count + 1

        visible_servers = self._server_list[
            scroll_offset : scroll_offset + visible_count
        ]

        for i, entry in enumerate(visible_servers):
            actual_idx = scroll_offset + i
            is_selected = actual_idx == self._current_index

            checkbox = "[x]" if entry["enabled"] else "[ ]"
            name = entry["name"]
            desc = entry["description"]

            # truncate description to fit
            max_desc = width - len(name) - 14
            if max_desc > 0 and len(desc) > max_desc:
                desc = desc[: max_desc - 1] + str(C["ellipsis"])

            if is_selected:
                # highlighted row: primary bg
                row_text = f" > {checkbox} {name}"
                padded = row_text.ljust(width)
                self._renderer.write_at(
                    0,
                    y,
                    solid(
                        padded,
                        theme.primary[0],
                        theme.text_dark,
                        width,
                    ),
                    "",
                )
                y += 1

                # description on next line, still highlighted
                desc_text = f"       {desc}"
                padded_desc = desc_text.ljust(width)
                self._renderer.write_at(
                    0,
                    y,
                    solid(
                        padded_desc,
                        theme.dark[0],
                        theme.text_dim,
                        width,
                    ),
                    "",
                )
                y += 1

                # env vars hint
                env_vars = list(entry["config"].get("env", {}).keys())
                if env_vars:
                    env_text = f"       requires: {', '.join(env_vars)}"
                    padded_env = env_text.ljust(width)
                    self._renderer.write_at(
                        0,
                        y,
                        solid(
                            padded_env,
                            theme.dark[0],
                            theme.text_dim,
                            width,
                        ),
                        "",
                    )
                    y += 1
            else:
                # normal row
                row_text = f"   {checkbox} {name} - {desc}"
                if len(row_text) > width - 2:
                    row_text = row_text[: width - 3] + str(C["ellipsis"])
                self._renderer.write_at(2, y, row_text, "")
                y += 1

            # small gap between entries
            if y < height - 4:
                y += 1 if not is_selected else 0

        # -- scroll indicator --
        total = len(self._server_list)
        if total > visible_count:
            indicator = f" {scroll_offset + 1}-{min(scroll_offset + visible_count, total)} of {total} "
            self._renderer.write_at(width - len(indicator) - 2, 3, indicator, "")

        # -- footer --
        footer_y = height - 2
        self._renderer.write_at(
            0,
            footer_y,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )

        enabled_count = sum(1 for s in self._server_list if s["enabled"])
        footer_left = f" {enabled_count} selected"
        footer_right = (
            "Up/Down: navigate | Space: toggle | Enter: confirm | Esc: cancel "
        )
        # trim right side if it doesn't fit
        available = width - len(footer_left) - 2
        if len(footer_right) > available:
            footer_right = footer_right[:available]

        footer_text = footer_left + footer_right.rjust(width - len(footer_left))
        self._renderer.write_at(
            0,
            footer_y + 1,
            solid(
                footer_text[:width],
                theme.dark[1],
                theme.text_dim,
                width,
            ),
            "",
        )
