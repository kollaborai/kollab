"""Recent updates AltView."""

from __future__ import annotations

import logging
import textwrap
from typing import Any, Optional

from kollabor.updates import release_notes
from kollabor.updates.release_notes import ReleaseNote
from kollabor.version import get_kollabor_version
from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class UpdatesAltView(AltView):
    """AltView for browsing recent Kollab release notes."""

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="updates",
            description="Open recent Kollab updates",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[UPD]",
            aliases=["changelog", "release-notes"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 15.0
        self._notes: list[ReleaseNote] = []
        self._selected_index = 0
        self._list_scroll_offset = 0
        self._detail_scroll_offset = 0
        self._status_message = ""
        self._current_version = get_kollabor_version()

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._load_notes()

    async def on_resume(self) -> None:
        await super().on_resume()
        self._load_notes()

    async def handle_input(self, key_press: KeyPress) -> bool:
        name = key_press.name
        char = key_press.char or ""

        if name == "Escape" or char == "\x1b":
            return True
        if char.lower() == "r":
            self._load_notes()
            return False
        if name == "ArrowUp" or char.lower() == "k":
            self._selected_index = max(0, self._selected_index - 1)
            self._detail_scroll_offset = 0
            return False
        if name == "ArrowDown" or char.lower() == "j":
            self._selected_index = min(
                max(0, len(self._notes) - 1), self._selected_index + 1
            )
            self._detail_scroll_offset = 0
            return False
        if name == "PageUp" or name == "ArrowLeft":
            self._detail_scroll_offset = max(0, self._detail_scroll_offset - 8)
            return False
        if name == "PageDown" or name == "ArrowRight":
            self._detail_scroll_offset += 8
            return False
        if name == "Home":
            self._selected_index = 0
            self._detail_scroll_offset = 0
            return False
        if name == "End":
            self._selected_index = max(0, len(self._notes) - 1)
            self._detail_scroll_offset = 0
            return False
        return False

    async def render_frame(self, delta_time: float) -> bool:
        if not self._renderer:
            return False
        width, height = self._renderer.get_terminal_size()
        theme = T()
        self._renderer.clear_screen()
        self._render_header(width, theme)
        self._render_body(width, height, theme)
        self._render_footer(width, height, theme)
        return True

    def _load_notes(self) -> None:
        self._current_version = get_kollabor_version()
        self._notes = release_notes.load_recent_release_notes(limit=8)
        self._selected_index = min(self._selected_index, max(0, len(self._notes) - 1))
        self._list_scroll_offset = min(self._list_scroll_offset, self._selected_index)
        self._detail_scroll_offset = 0
        if self._notes:
            self._status_message = f"loaded {len(self._notes)} releases"
        else:
            self._status_message = "no local changelog found"

    def _current_note(self) -> Optional[ReleaseNote]:
        if not self._notes:
            return None
        return self._notes[min(self._selected_index, len(self._notes) - 1)]

    def _render_header(self, width: int, theme: Any) -> None:
        title = f" Kollab Updates  current:{self._current_version} "
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
                self._fit(title, width).ljust(width),
                theme.primary[0],
                theme.text_dark,
                width,
            ),
            "",
        )
        self._renderer.write_at(
            0,
            2,
            solid(
                self._fit(f" {self._status_message}", width).ljust(width),
                theme.dark[0],
                theme.text_dim,
                width,
            ),
            "",
        )

    def _render_body(self, width: int, height: int, theme: Any) -> None:
        left_width = max(18, min(28, width // 3))
        divider_x = left_width
        right_x = divider_x + 2
        right_width = max(20, width - right_x)
        top = 4
        body_height = max(4, height - top - 3)

        self._render_versions(0, top, left_width, body_height, theme)
        self._render_divider(divider_x, top, body_height, theme)
        self._render_release_notes(right_x, top, right_width, body_height, theme)

    def _render_versions(
        self, x: int, y: int, width: int, height: int, theme: Any
    ) -> None:
        self._renderer.write_at(
            x,
            y,
            solid(" versions".ljust(width), theme.dark[1], theme.text, width),
            "",
        )
        y += 1
        list_height = max(1, height - 1)

        if self._selected_index < self._list_scroll_offset:
            self._list_scroll_offset = self._selected_index
        if self._selected_index >= self._list_scroll_offset + list_height:
            self._list_scroll_offset = self._selected_index - list_height + 1

        visible = self._notes[
            self._list_scroll_offset : self._list_scroll_offset + list_height
        ]
        for offset, note in enumerate(visible):
            index = self._list_scroll_offset + offset
            cursor = ">" if index == self._selected_index else " "
            date = f" {note.date}" if note.date else ""
            line = self._fit(f"{cursor} {note.version}{date}", width)
            bg = theme.primary[0] if index == self._selected_index else theme.dark[0]
            fg = theme.text_dark if index == self._selected_index else theme.text
            self._renderer.write_at(
                x,
                y + offset,
                solid(line.ljust(width), bg, fg, width),
                "",
            )

        for offset in range(len(visible), list_height):
            self._renderer.write_at(
                x,
                y + offset,
                solid(" " * width, theme.dark[0], theme.text, width),
                "",
            )

    def _render_divider(self, x: int, y: int, height: int, theme: Any) -> None:
        for offset in range(height):
            self._renderer.write_at(x, y + offset, solid_fg(" ", theme.dark[1]), "")

    def _render_release_notes(
        self, x: int, y: int, width: int, height: int, theme: Any
    ) -> None:
        note = self._current_note()
        title = " release notes"
        if note is not None:
            title = f" {note.version}"
            if note.date:
                title += f"  {note.date}"
        self._renderer.write_at(
            x,
            y,
            solid(
                self._fit(title, width).ljust(width), theme.dark[1], theme.text, width
            ),
            "",
        )
        y += 1

        lines = self._detail_lines(note, width)
        visible_height = max(1, height - 1)
        max_scroll = max(0, len(lines) - visible_height)
        self._detail_scroll_offset = min(self._detail_scroll_offset, max_scroll)
        visible = lines[
            self._detail_scroll_offset : self._detail_scroll_offset + visible_height
        ]

        for offset, line in enumerate(visible):
            self._renderer.write_at(
                x,
                y + offset,
                solid(
                    self._fit(f" {line}", width).ljust(width),
                    theme.dark[0],
                    theme.text_dim,
                    width,
                ),
                "",
            )

        for offset in range(len(visible), visible_height):
            self._renderer.write_at(
                x,
                y + offset,
                solid(" " * width, theme.dark[0], theme.text_dim, width),
                "",
            )

    def _detail_lines(self, note: Optional[ReleaseNote], width: int) -> list[str]:
        if note is None:
            return [
                "no release notes found",
                "source: CHANGELOG.md",
                "reload: press r",
            ]

        lines: list[str] = []
        for section in note.sections:
            if lines:
                lines.append("")
            lines.append(section.title)
            for item in section.items:
                wrapped = self._wrap(item, max(8, width - 4))
                if not wrapped:
                    continue
                lines.append(f"- {wrapped[0]}")
                lines.extend(f"  {line}" for line in wrapped[1:])
        lines.append("")
        lines.append(self._status_message)
        return lines

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        footer_y = height - 2
        self._renderer.write_at(
            0,
            footer_y,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        footer = " up/down versions | left/right notes | r reload | esc exit "
        self._renderer.write_at(
            0,
            footer_y + 1,
            solid(
                self._fit(footer, width).ljust(width),
                theme.dark[1],
                theme.text_dim,
                width,
            ),
            "",
        )

    def _fit(self, text: str, width: int) -> str:
        if len(text) <= width:
            return text
        if width <= 1:
            return text[:width]
        return text[: width - 1] + str(C["ellipsis"])

    def _wrap(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(
            text,
            width=max(1, width),
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=True,
        )
