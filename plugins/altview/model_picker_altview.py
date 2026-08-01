"""Model picker as an AltView plugin.

Full-screen model selector for the active provider. Opened by `/model`.

Shows, for the active provider:
  * the current model (marked)
  * models from saved profiles that share the provider
  * the provider's live catalog when one exists (OpenAI OAuth, OpenRouter),
    merged in asynchronously so the view opens instantly

The filter line doubles as a free-form entry: type any model id and press
Enter to switch to it even if it isn't in the list. Arrow keys + Enter pick
a highlighted row instead.

After exit, the command handler reads `selected_model` (None if cancelled).
"""

import logging
import re
from typing import Any, Dict, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[^m]*m", "", text)


class ModelPickerAltView(AltView):
    """AltView for selecting the active provider's model.

    Usage:
        picker = ModelPickerAltView()
        picker.set_context(profile, known_models, current_model, provider_label)
        # push onto altview stack -- after exit, read picker.selected_model
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="model-picker",
            description="Select the active provider's model",
            version="1.0.0",
            author="Kollabor",
            category="internal",
            icon="[MODEL]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps: float = 15.0
        self.render_on_timer = True  # spinner + async catalog merge

        # Context (set before push)
        self._profile: Any = None
        self._current_model: str = ""
        self._provider_label: str = ""

        # Model data: list of {"id", "note", "current": bool, "source"}
        self._all_models: List[Dict[str, Any]] = []
        self._filtered: List[Dict[str, Any]] = []

        # UI state
        self._query: str = ""
        self._selected_index: int = 0
        self._scroll_offset: int = 0
        self._loading: bool = False
        self._catalog_done: bool = False

        # Result
        self._result: Optional[str] = None

    # -- public API --

    def set_context(
        self,
        profile: Any,
        known_models: List[Dict[str, Any]],
        current_model: str,
        provider_label: str,
    ) -> None:
        """Seed the picker before pushing onto the stack.

        Args:
            profile: Active LLMProfile (used for the live catalog fetch).
            known_models: Pre-known models ``[{"id","note"}]`` from saved
                profiles + the current model. Shown immediately.
            current_model: The currently active model id (marked in the list).
            provider_label: Human-readable provider name for the title.
        """
        self._profile = profile
        self._current_model = current_model or ""
        self._provider_label = provider_label or "provider"
        self._all_models = self._dedup(known_models)

    @property
    def selected_model(self) -> Optional[str]:
        """The model id the user chose, or None if cancelled."""
        return self._result

    # -- lifecycle --

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._query = ""
        self._selected_index = 0
        self._scroll_offset = 0
        self._result = None
        self._apply_filter()

        # Fetch the live catalog in the background and merge when it lands.
        self._loading = True
        self._catalog_done = False
        self.spawn_background_task(self._fetch_catalog(), name="model-catalog")
        logger.info(
            "ModelPickerAltView entered (provider=%s, current=%s, known=%d)",
            self._provider_label,
            self._current_model,
            len(self._all_models),
        )

    async def _fetch_catalog(self) -> None:
        """Fetch the provider catalog and merge it into the model list."""
        try:
            from kollabor_ai.model_catalog import list_provider_models

            catalog = await list_provider_models(self._profile)
            if catalog:
                self._all_models = self._dedup(self._all_models + catalog)
                self._apply_filter()
        except Exception as e:  # noqa: BLE001 - never break the view
            logger.warning("ModelPickerAltView: catalog fetch failed: %s", e)
        finally:
            self._loading = False
            self._catalog_done = True

    # -- rendering --

    async def render_frame(self, delta_time: float) -> bool:
        if not self.renderer:
            return False

        width, height = self.renderer.get_terminal_size()
        theme = T()
        row = 0

        # title bar
        self.renderer.write_at(
            0, row, solid_fg(C["half_bottom"] * width, theme.primary[0]), ""
        )
        row += 1
        title = f"  SELECT MODEL  --  {self._provider_label}"
        self.renderer.write_at(
            0,
            row,
            solid(title.ljust(width), theme.primary[0], theme.text_dark, width),
            "",
        )
        row += 1

        # filter / entry line
        filter_line = f"  filter: {self._query}_"
        self.renderer.write_at(
            0,
            row,
            solid(filter_line.ljust(width), theme.dark[0], theme.text, width),
            "",
        )
        row += 1
        hint = "  (type a model id, Enter to use it; ↑↓ to pick from the list)"
        self.renderer.write_at(
            0, row, solid(hint.ljust(width), theme.dark[0], theme.text_dim, width), ""
        )
        row += 1

        # model list
        list_height = max(3, height - row - 4)
        page_size = list_height
        total = len(self._filtered)
        start_idx = max(0, min(self._scroll_offset, max(0, total - page_size)))
        visible = self._filtered[start_idx : start_idx + page_size]

        for i, model in enumerate(visible):
            actual_idx = start_idx + i
            is_selected = actual_idx == self._selected_index
            marker = "*" if model.get("current") else " "
            model_id = str(model.get("id") or "")
            note = str(model.get("note") or "")

            prefix = (
                f"  {C['arrow_right']} {marker} " if is_selected else f"    {marker} "
            )
            base = prefix + model_id
            # right-align the note if it fits
            plain_len = len(_strip_ansi(base))
            note_room = width - plain_len - 2
            if note and note_room > len(note) + 1:
                pad = width - plain_len - len(note) - 1
                content = base + (" " * max(1, pad)) + note
            else:
                content = base

            if is_selected:
                self.renderer.write_at(
                    0,
                    row,
                    solid(
                        content.ljust(width), theme.primary[0], theme.text_dark, width
                    ),
                    "",
                )
            else:
                self.renderer.write_at(
                    0,
                    row,
                    solid(content.ljust(width), theme.dark[0], theme.text_dim, width),
                    "",
                )
            row += 1

        # fill remaining list rows
        for _ in range(page_size - len(visible)):
            self.renderer.write_at(
                0, row, solid(" " * width, theme.dark[0], theme.text, width), ""
            )
            row += 1

        # status line (count + loading)
        if total > page_size:
            status = (
                f"  ({start_idx + 1}-{min(start_idx + page_size, total)} of {total})"
            )
        else:
            status = f"  {total} model{'s' if total != 1 else ''}"
        if self._loading:
            status += "   (loading catalog...)"
        elif self._catalog_done and total == 0 and self._query:
            status += f"   press Enter to use '{self._query}'"
        self.renderer.write_at(
            0, row, solid(status.ljust(width), theme.dark[0], theme.text_dim, width), ""
        )
        row += 1

        # footer
        self.renderer.write_at(
            0, row, solid_fg(C["half_bottom"] * width, theme.dark[1]), ""
        )
        row += 1
        footer = (
            " Up/Down: Navigate | Enter: Select | Type: filter/enter id | Esc: Cancel"
        )
        self.renderer.write_at(
            0,
            row,
            solid(footer[:width].ljust(width), theme.dark[1], theme.text_dim, width),
            "",
        )
        return True

    # -- input --

    async def handle_input(self, key_press: KeyPress) -> bool:
        name = key_press.name or ""
        char = key_press.char or ""

        if name == "Escape":
            self._result = None
            return True

        if name == "Enter":
            typed = self._query.strip()
            if self._filtered and 0 <= self._selected_index < len(self._filtered):
                self._result = str(self._filtered[self._selected_index]["id"])
            elif typed.startswith("/"):
                # A slash command typed into the filter is a mistake, not a
                # model id -- accepting it would set the profile's model to
                # something like "/model effort". Cancel instead.
                logger.info("ModelPickerAltView: ignoring command-like entry %r", typed)
                self._result = None
            elif typed:
                # free-form entry: use exactly what was typed
                self._result = typed
            else:
                self._result = None
            logger.info("ModelPickerAltView: selected %r", self._result)
            return True

        if name in ("ArrowUp", "Up"):
            if self._selected_index > 0:
                self._selected_index -= 1
                self._update_scroll()
            return False
        if name in ("ArrowDown", "Down"):
            if self._selected_index < len(self._filtered) - 1:
                self._selected_index += 1
                self._update_scroll()
            return False
        if name == "PageUp":
            self._selected_index = max(0, self._selected_index - self._get_page_size())
            self._update_scroll()
            return False
        if name == "PageDown":
            self._selected_index = min(
                len(self._filtered) - 1,
                self._selected_index + self._get_page_size(),
            )
            self._update_scroll()
            return False
        if name == "Home":
            self._selected_index = 0
            self._update_scroll()
            return False
        if name == "End":
            self._selected_index = max(0, len(self._filtered) - 1)
            self._update_scroll()
            return False

        if name in ("Backspace", "Delete"):
            if self._query:
                self._query = self._query[:-1]
                self._apply_filter()
            return False

        if char and char.isprintable() and len(char) == 1:
            self._query += char
            self._apply_filter()
            return False

        return False

    # -- helpers --

    def _dedup(self, models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Dedup by id, keep first occurrence, mark the current model.

        Order is preserved so callers control priority (current model first,
        then saved profiles, then catalog).
        """
        seen: set = set()
        out: List[Dict[str, Any]] = []
        for m in models:
            mid = str(m.get("id") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append(
                {
                    "id": mid,
                    "note": m.get("note") or "",
                    "current": mid == self._current_model,
                }
            )
        return out

    def _apply_filter(self) -> None:
        q = self._query.lower().strip()
        if q:
            self._filtered = [m for m in self._all_models if q in m["id"].lower()]
        else:
            self._filtered = list(self._all_models)
        if self._selected_index >= len(self._filtered):
            self._selected_index = max(0, len(self._filtered) - 1)
        self._update_scroll()

    def _update_scroll(self) -> None:
        page = self._get_page_size()
        if self._selected_index < self._scroll_offset:
            self._scroll_offset = self._selected_index
        elif self._selected_index >= self._scroll_offset + page:
            self._scroll_offset = self._selected_index - page + 1

    def _get_page_size(self) -> int:
        if not self.renderer:
            return 10
        _, height = self.renderer.get_terminal_size()
        # title(2) + filter(1) + hint(1) + status(1) + footer(2) = 7
        return max(3, int(height) - 7)
