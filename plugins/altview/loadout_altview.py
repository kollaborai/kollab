"""Loadout picker + form as AltView plugins.

A **loadout** = provider profile + model + params (temperature, effort,
max_tokens) -- a named preset a user can switch to instantly. The core data
model and persistence live in ``kollabor_ai.loadout_manager`` (built by
another agent in parallel); everything here is lazy-imported so this module
``py_compile``s cleanly whether or not that module has landed yet.

Two views:

    LoadoutListAltView   browse explicit loadouts + catalog ("implicit")
                         models grouped by provider, activate/create/edit/
                         delete.
    LoadoutFormAltView   create/edit a loadout, arrives fully pre-filled.

The `/loadout` command handler (``handlers/loadout.py``) orchestrates the
two: it pushes the list, and if the user asks to create/edit, pushes the
form as a *separate, sequential* top-level AltView push (never nested one
inside the other). Esc from the form returns to a freshly-pushed list, Enter
on a list row or a successful form save exits the whole flow. This keeps
every push a simple, proven single-level ``stack_mgr.push(..., reuse=False)``
call identical in shape to `/setup` and `/model`, rather than relying on
push-from-within-a-pushed-view, which nothing else in the codebase does yet
and the stack manager's pop path does not obviously support (it unconditionally
thaws the *main* app UI on pop, with no visible handling for "resume the
altview session underneath").

Known landmine worked around here: ``kollabor_tui.widgets.spin_box.SpinBoxWidget``
crashes unconditionally on construction (``get_value()`` reads
``self.current_value`` before ``__init__`` has set it -- verified empirically).
That widget class is out of scope for this change (do not touch
``packages/kollabor-tui/src/kollabor_tui/widgets/spin_box.py``), so "Max
Output Tokens" is a ``TextInputWidget`` with ``validation="integer"`` instead of a
``SpinBoxWidget`` -- functionally equivalent (direct numeric entry, empty
means "use the provider default"), just not literally that class. The
ChatGPT OAuth/Codex endpoint gets a read-only "backend default" label because
it rejects output-token overrides entirely.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Optional, Tuple

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


# -- shared helpers -----------------------------------------------------------

_PROVIDER_LABELS = {
    "openai_responses": "OpenAI (ChatGPT)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "gemini": "Gemini",
    "azure": "Azure OpenAI",
    "custom": "Custom",
}

_EFFORT_OPTIONS = ("default", "low", "medium", "high", "xhigh", "max", "ultra")


def _provider_display(provider: str) -> str:
    """Title-case display name for a provider type string."""
    if not provider:
        return "Provider"
    return _PROVIDER_LABELS.get(provider.lower(), provider.replace("_", " ").title())


def _resolve_provider_type(profile_manager: Any, provider_profile: str) -> str:
    """Best-effort provider type (e.g. ``anthropic``) for a profile name."""
    if not profile_manager or not provider_profile:
        return provider_profile or ""
    try:
        profile = profile_manager.get_profile(provider_profile)
    except Exception:
        return provider_profile
    if profile is not None and hasattr(profile, "get_provider"):
        try:
            return profile.get_provider() or provider_profile
        except Exception:
            return provider_profile
    return provider_profile


def _is_chatgpt_codex_profile(profile_manager: Any, provider_profile: str) -> bool:
    """Whether a profile targets the ChatGPT OAuth/Codex transport."""
    if not profile_manager or not provider_profile:
        return False
    try:
        profile = profile_manager.get_profile(provider_profile)
        endpoint = profile.get_endpoint() if profile is not None else ""
    except Exception:
        return False
    return "chatgpt.com" in str(endpoint or "").lower()


def _resolve_profile_entry(profile_manager: Any, entry: Any) -> Tuple[str, str]:
    """(profile_name, provider_type) from a ``provider_profiles()`` item.

    The item may be a profile name string or an ``LLMProfile``-like object --
    the manager's exact return shape isn't known here (it lands separately),
    so both are handled defensively.
    """
    if isinstance(entry, str):
        return entry, _resolve_provider_type(profile_manager, entry)
    name = getattr(entry, "name", "") or str(entry)
    provider = ""
    if hasattr(entry, "get_provider"):
        try:
            provider = entry.get_provider() or ""
        except Exception:
            provider = ""
    elif hasattr(entry, "provider"):
        provider = getattr(entry, "provider") or ""
    return name, provider or _resolve_provider_type(profile_manager, name)


def _model_registry_info(model: str) -> dict:
    """Registry dict for an exact model id, or ``{}`` when unknown."""
    if not model:
        return {}
    try:
        from kollabor_ai.model_registry import get_model_registry

        info = get_model_registry().get("models", {}).get(model)
        return info if isinstance(info, dict) else {}
    except Exception as exc:  # noqa: BLE001 - registry is best-effort
        logger.debug("loadout: model registry unavailable: %s", exc)
        return {}


def _model_note(model: str, provider: str = "") -> str:
    """``1M ctx • $5/$30`` style descriptor, matching ModelCommandHandler._registry_note."""
    parts: List[str] = []
    try:
        from kollabor_ai.model_registry import resolve_context_window

        window = resolve_context_window(model, provider or None)
        if isinstance(window, int) and window > 0:
            parts.append(
                f"{window / 1_000_000:.2f}M ctx".replace(".00M", "M")
                if window >= 1_000_000
                else f"{window // 1000}K ctx"
            )
    except Exception as exc:  # noqa: BLE001 - never block rendering
        logger.debug("loadout: context window lookup failed for %s: %s", model, exc)

    info = _model_registry_info(model)
    price_in, price_out = info.get("pricing_in"), info.get("pricing_out")
    if isinstance(price_in, (int, float)) and isinstance(price_out, (int, float)):
        parts.append(f"${price_in:g}/${price_out:g}")
    return " • ".join(parts)


def _loadout_note(loadout: Any) -> str:
    """Right-aligned dim row note: ``model • 1M ctx • $5/$30 [• overrides]``."""
    parts = [loadout.model]
    note = _model_note(loadout.model)
    if note:
        parts.append(note)
    if not loadout.implicit:
        overrides = []
        if loadout.temperature is not None:
            overrides.append(f"temp {loadout.temperature:g}")
        if loadout.effort:
            overrides.append(f"effort {loadout.effort}")
        if loadout.max_tokens:
            overrides.append(f"max {loadout.max_tokens:,}")
        if overrides:
            parts.append(" • ".join(overrides))
    return " • ".join(p for p in parts if p)


def _default_max_tokens(model: str) -> int:
    info = _model_registry_info(model)
    value = info.get("default_output") or info.get("max_output")
    if isinstance(value, int) and value > 0:
        # The registry value is a model hint/ceiling. Kollab's provider config
        # intentionally reserves 16K by default so a large output allowance
        # does not consume the context budget on every turn. Users can still
        # enter the registry ceiling explicitly for long generation.
        return min(value, 16384)
    return 16384


def _suggest_name(model: str, existing: set, style: str) -> str:
    """Suggested loadout name: bare model id, or ``-custom`` when branching."""
    base = model or "loadout"
    candidate = f"{base}-custom" if style == "custom" else base
    if candidate not in existing:
        return candidate
    i = 2
    while f"{candidate}-{i}" in existing:
        i += 1
    return f"{candidate}-{i}"


def _short_error(msg: str) -> str:
    msg = " ".join(msg.split())
    if len(msg) > 120:
        msg = msg[:117] + "..."
    return msg or "unknown error"


def build_active_profile_base(profile_manager: Any) -> Optional[Any]:
    """Synthesize an implicit ``Loadout`` base from the active profile, or None.

    Shared by the list view's "N with nothing selected" fallback and the
    `/loadout new` handler path.
    """
    if profile_manager is None:
        return None
    try:
        active = profile_manager.get_active_profile()
    except Exception:
        return None
    if not active:
        return None
    try:
        model = active.get_model() or ""
    except Exception:
        model = ""
    if not model:
        return None
    try:
        from kollabor_ai.loadout_manager import Loadout
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("loadout: Loadout dataclass unavailable: %s", exc)
        return None
    return Loadout(
        name="",
        provider_profile=getattr(active, "name", "") or "",
        model=model,
        implicit=True,
    )


def _render_widget_safe(widget: Any, width: int, position: str) -> List[str]:
    """Call ``render_modern()`` on a widget, never letting it crash the view.

    Mirrors ``ConfigAltView._render_widget_modern`` -- any widget's
    ``render_modern`` (an area with at least one confirmed landmine
    elsewhere -- see the module docstring) degrades to an inline error line
    instead of taking the whole AltView down with it.
    """
    try:
        if hasattr(widget, "render_modern"):
            output = widget.render_modern(width=width, position=position)
            lines: List[str] = []
            for block in output:
                for line in block.split("\n"):
                    if line:
                        lines.append(line)
            return lines if lines else [""]
        return widget.render()  # type: ignore[no-any-return]
    except Exception as exc:  # noqa: BLE001 - render must never crash the view
        logger.error("loadout: render_modern failed: %s", exc)
        label = widget.get_label() if hasattr(widget, "get_label") else "?"
        return [f"  {label}: (render error)"]


# -- list view ------------------------------------------------------------


class LoadoutListAltView(AltView):
    """Fullscreen picker: saved loadouts + catalog models grouped by provider."""

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="loadout-list",
            description="Browse and switch model loadouts",
            version="1.0.0",
            author="Kollabor",
            # internal: needs set_context() from the /loadout handler; a cold
            # palette invocation would open a broken view.
            category="internal",
            icon="[LOAD]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 12.0
        self.render_on_timer = True

        # injected services (set_context)
        self._manager: Any = None
        self._profile_manager: Any = None
        self._event_bus: Any = None

        # data model
        self._sections: List[Tuple[str, List[Any]]] = []
        self._view_sections: List[Tuple[str, List[Any], bool]] = []
        self._items: List[Any] = []

        # ui state
        self._selected: int = 0
        self._query: str = ""
        self._note: str = ""
        self._delete_armed: Optional[str] = None
        self._activating: bool = False
        self._activate_error: str = ""

        # spinner
        self._spinner = ["|", "/", "-", "\\"]
        self._spinner_idx = 0
        self._last_spin = 0.0

        # results read by the command handler
        self.result_cancelled = False
        self.result_activated = False
        self.result_loadout: Any = None
        self.result_open_form = False
        self.result_form_mode = "create"
        self.result_form_base: Any = None
        self.result_form_name_style = "plain"

    # -- context injection ---------------------------------------------------

    def set_context(
        self,
        manager: Any = None,
        profile_manager: Any = None,
        event_bus: Any = None,
    ) -> None:
        self._manager = manager
        self._profile_manager = profile_manager
        self._event_bus = event_bus

    # -- lifecycle -------------------------------------------------------

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._refresh()
        logger.info("LoadoutListAltView: entered with %d rows", len(self._items))

    async def render_frame(self, delta_time: float) -> bool:
        if not self._renderer:
            return False
        if self.result_cancelled or self.result_open_form:
            return False

        width, height = self._renderer.get_terminal_size()
        theme = T()
        self._renderer.clear_screen()
        self._render_header(width, theme)
        self._render_status(width, 2, theme)
        self._render_rows(width, 4, height, theme)
        self._render_footer(width, height, theme)
        return True

    # -- data -------------------------------------------------------------

    def _refresh(self) -> None:
        self._sections = self._build_sections()
        self._apply_filter()

    def _build_sections(self) -> List[Tuple[str, List[Any]]]:
        """(section_title, loadouts) pairs: explicit first, then per provider."""
        if self._manager is None:
            return [("Loadouts", [])]

        try:
            loadouts = list(self._manager.list_loadouts())
        except Exception as exc:
            logger.error("loadout: list_loadouts() failed: %s", exc)
            loadouts = []

        explicit = [entry for entry in loadouts if not entry.implicit]
        implicit = [entry for entry in loadouts if entry.implicit]

        sections: List[Tuple[str, List[Any]]] = [("Loadouts", explicit)]

        try:
            profiles = list(self._manager.provider_profiles() or [])
        except Exception as exc:
            logger.warning("loadout: provider_profiles() failed: %s", exc)
            profiles = []

        claimed: set = set()
        for entry in profiles:
            name, provider = _resolve_profile_entry(self._profile_manager, entry)
            if not name or name in claimed:
                continue
            claimed.add(name)
            rows = [m for m in implicit if m.provider_profile == name]
            if not rows:
                continue
            sections.append((f"Models — {_provider_display(provider or name)}", rows))

        # Defensive: keep any implicit loadout visible even if its
        # provider_profile wasn't covered by provider_profiles() above, so a
        # mismatch between the two manager methods never silently drops rows.
        leftover = [m for m in implicit if m.provider_profile not in claimed]
        if leftover:
            by_profile: dict = {}
            for entry in leftover:
                by_profile.setdefault(entry.provider_profile, []).append(entry)
            for profile_name, rows in by_profile.items():
                provider = _resolve_provider_type(self._profile_manager, profile_name)
                label = _provider_display(provider or profile_name)
                sections.append((f"Models — {label}", rows))

        return sections

    def _apply_filter(self) -> None:
        q = self._query.lower().strip()
        view_sections: List[Tuple[str, List[Any], bool]] = []
        items: List[Any] = []
        for idx, (title, rows) in enumerate(self._sections):
            visible = rows
            if q:
                visible = [
                    r for r in rows if q in r.name.lower() or q in r.model.lower()
                ]
            truly_empty = idx == 0 and not rows
            title_matches = bool(q) and q in title.lower()
            if not visible and not truly_empty and not title_matches:
                continue
            view_sections.append((title, visible, truly_empty))
            items.extend(visible)

        self._view_sections = view_sections
        self._items = items
        if self._selected >= len(items):
            self._selected = max(0, len(items) - 1)
        if self._selected < 0:
            self._selected = 0

    def _current_item(self) -> Optional[Any]:
        if not self._items:
            return None
        idx = max(0, min(self._selected, len(self._items) - 1))
        return self._items[idx]

    def _is_active(self, loadout: Any) -> bool:
        pm = self._profile_manager
        if pm is None:
            return False
        try:
            active = pm.get_active_profile()
        except Exception:
            return False
        if not active:
            return False
        try:
            active_model = active.get_model() or ""
        except Exception:
            active_model = ""
        return (
            getattr(active, "name", None) == loadout.provider_profile
            and active_model == loadout.model
        )

    # -- input --------------------------------------------------------------

    async def handle_input(self, key_press: KeyPress) -> bool:
        name = key_press.name or ""
        char = key_press.char or ""

        if self._activating:
            return False  # transient -- ignore input like SetupAltView's testing/saving

        if self.result_activated:
            return True  # confirmation already rendered -- any key exits

        if self._delete_armed is not None:
            item = self._current_item()
            if (
                char.lower() == "d"
                and item is not None
                and item.name == self._delete_armed
            ):
                self._confirm_delete(item)
            else:
                self._delete_armed = None
                self._note = ""
            return False

        if name == "Escape" or char == "\x1b":
            # Filter active: Esc clears it first (same pattern as /config).
            if self._query:
                self._query = ""
                self._apply_filter()
                self._note = ""
                return False
            self.result_cancelled = True
            return True

        if name in ("ArrowUp", "Up"):
            if self._items and self._selected > 0:
                self._selected -= 1
                self._note = ""
            return False
        if name in ("ArrowDown", "Down"):
            if self._items and self._selected < len(self._items) - 1:
                self._selected += 1
                self._note = ""
            return False

        if name == "Enter" or char in ("\r", "\n"):
            item = self._current_item()
            if item is not None:
                self._note = ""
                self._activate_error = ""
                self._activating = True
                self.spawn_background_task(
                    self._run_activate(item), name="loadout-activate"
                )
            return False

        # Command keys only while the filter is empty -- otherwise typing
        # "fable" or "sonnet" would trigger Edit/New/Delete mid-word.
        if not self._query:
            if char in ("n", "N"):
                self._trigger_new()
                return self.result_open_form
            if char in ("e", "E"):
                self._trigger_edit()
                return self.result_open_form
            if char in ("d", "D"):
                self._trigger_delete()
                return False

        if name in ("Backspace", "Delete"):
            if self._query:
                self._query = self._query[:-1]
                self._apply_filter()
                self._note = ""
            return False

        if char and char.isprintable() and len(char) == 1:
            self._query += char
            self._apply_filter()
            self._note = ""
            return False

        return False

    def _trigger_new(self) -> None:
        base = self._current_item() or build_active_profile_base(self._profile_manager)
        if base is None:
            self._note = "no model to start from — configure a provider with /setup."
            self.result_open_form = False
            return
        self.result_open_form = True
        self.result_form_mode = "create"
        self.result_form_base = base
        self.result_form_name_style = "plain"

    def _trigger_edit(self) -> None:
        item = self._current_item()
        if item is None:
            self.result_open_form = False
            return
        self.result_open_form = True
        self.result_form_base = item
        if item.implicit:
            self.result_form_mode = "create"
            self.result_form_name_style = "custom"
        else:
            self.result_form_mode = "edit"
            self.result_form_name_style = "plain"

    def _trigger_delete(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if item.implicit:
            self._note = (
                "catalog models can't be deleted — press N to save a customized copy."
            )
            return
        self._delete_armed = item.name
        self._note = f"press D again to delete '{item.name}', any other key cancels."

    def _confirm_delete(self, item: Any) -> None:
        try:
            ok = bool(self._manager.delete(item.name))
        except Exception as exc:
            ok = False
            logger.error("loadout: delete('%s') failed: %s", item.name, exc)
        self._delete_armed = None
        if ok:
            self._note = f"deleted '{item.name}'."
            self._refresh()
        else:
            self._note = f"could not delete '{item.name}'."

    async def _run_activate(self, loadout: Any) -> None:
        try:
            activated = await asyncio.wait_for(
                self._manager.activate(loadout, self._event_bus), timeout=20.0
            )
            if activated:
                self.result_activated = True
                self.result_loadout = activated
            else:
                self._activate_error = f"could not activate '{loadout.name}'."
        except asyncio.TimeoutError:
            self._activate_error = "activation timed out after 20s."
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._activate_error = _short_error(str(exc))
            logger.error("loadout: activate('%s') failed: %s", loadout.name, exc)
        finally:
            self._activating = False

    # -- rendering ------------------------------------------------------

    def _render_header(self, width: int, theme: Any) -> None:
        r = self._renderer
        title = "  Loadouts  "
        r.write_at(0, 0, solid_fg(str(C["half_bottom"]) * width, theme.primary[0]), "")
        r.write_at(
            0,
            1,
            solid(title.ljust(width), theme.primary[0], theme.text_dark, width),
            "",
        )

    def _render_status(self, width: int, top: int, theme: Any) -> None:
        if self._activating:
            now = time.monotonic()
            if now - self._last_spin > 0.12:
                self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner)
                self._last_spin = now
            text = f"  {self._spinner[self._spinner_idx]} activating..."
            fg = theme.text
        elif self.result_activated:
            loadout = self.result_loadout
            label = loadout.name if loadout is not None else ""
            text = f"  ✓ loadout active: {label} — press any key"
            fg = theme.success[0]
        elif self._activate_error:
            text = f"  ! {self._activate_error}"
            fg = theme.error[0]
        elif self._note:
            text = f"  {self._note}"
            fg = theme.text_dim
        elif self._query:
            text = f"  filter: {self._query}█  (Esc clears)"
            fg = theme.text
        else:
            text = "  type to filter  |  ↑↓ navigate"
            fg = theme.text_dim
        self._renderer.write_at(
            0, top, solid(text.ljust(width), theme.dark[0], fg, width), ""
        )

    def _render_rows(self, width: int, top: int, height: int, theme: Any) -> None:
        lines = self._flat_lines()
        avail = max(3, height - top - 3)

        sel_line = 0
        for i, entry in enumerate(lines):
            if entry[0] == "item" and entry[2] == self._selected:
                sel_line = i
                break

        start = max(0, min(sel_line - avail + 1, max(0, len(lines) - avail)))
        visible = lines[start : start + avail]

        y = top
        for entry in visible:
            if entry[0] == "header":
                self._renderer.write_at(
                    0,
                    y,
                    solid(
                        f"  {entry[1]}".ljust(width), theme.dark[1], theme.text, width
                    ),
                    "",
                )
            elif entry[0] == "empty":
                self._renderer.write_at(
                    0,
                    y,
                    solid(
                        f"    {entry[1]}".ljust(width),
                        theme.dark[0],
                        theme.text_dim,
                        width,
                    ),
                    "",
                )
            else:
                _, loadout, idx = entry
                self._render_item_row(y, width, loadout, idx == self._selected, theme)
            y += 1

    def _flat_lines(self) -> List[Tuple]:
        lines: List[Tuple] = []
        item_idx = 0
        for title, rows, truly_empty in self._view_sections:
            lines.append(("header", title))
            if truly_empty:
                lines.append(
                    (
                        "empty",
                        "No saved loadouts — press N to create one from any model.",
                    )
                )
            for loadout in rows:
                lines.append(("item", loadout, item_idx))
                item_idx += 1
        return lines

    def _render_item_row(
        self, y: int, width: int, loadout: Any, selected: bool, theme: Any
    ) -> None:
        marker = "[*]" if self._is_active(loadout) else "   "
        base = f"  {marker} {loadout.name}"
        note = _loadout_note(loadout)
        plain_len = len(base)
        room = width - plain_len - 3
        if note and room > len(note):
            pad = width - plain_len - len(note) - 1
            content = base + (" " * max(1, pad)) + note
        else:
            content = base

        bg = theme.primary[0] if selected else theme.dark[0]
        if selected:
            fg = theme.text_dark
        elif loadout.implicit:
            fg = theme.text_dim
        else:
            fg = theme.text
        self._renderer.write_at(0, y, solid(content.ljust(width), bg, fg, width), "")

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        r = self._renderer
        footer_y = height - 2
        r.write_at(
            0, footer_y, solid_fg(str(C["half_bottom"]) * width, theme.dark[1]), ""
        )
        if self._delete_armed is not None:
            hint = " d confirm delete | any other key cancels"
        elif self._query:
            hint = " up/down navigate | enter activate | esc clear filter"
        else:
            hint = " up/down navigate | enter activate | n new | e edit | d delete | esc close"
        r.write_at(
            0,
            footer_y + 1,
            solid(hint.ljust(width), theme.dark[1], theme.text_dim, width),
            "",
        )


# -- form view --------------------------------------------------------------


class LoadoutFormAltView(AltView):
    """Create/edit form for a loadout. Arrives fully pre-filled."""

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="loadout-form",
            description="Create or edit a model loadout",
            version="1.0.0",
            author="Kollabor",
            # internal: needs set_context() from the /loadout handler; a cold
            # palette invocation would open a broken view.
            category="internal",
            icon="[LOAD]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps = 12.0
        self.render_on_timer = True

        # injected services / seed (set_context)
        self._manager: Any = None
        self._profile_manager: Any = None
        self._event_bus: Any = None
        self._mode: str = "create"
        self._base: Any = None
        self._name_style: str = "plain"
        self._existing_names_seed: Optional[List[str]] = None
        self._existing_names_final: set = set()

        # widgets
        self._provider_widget: Any = None
        self._model_widget: Any = None
        self._name_widget: Any = None
        self._name_editable: bool = True
        self._temperature_widget: Any = None
        self._effort_widget: Any = None
        self._max_tokens_widget: Any = None
        self._max_tokens_supported: bool = True
        self._description_widget: Any = None
        self._fields: List[Any] = []
        self._focus_index: int = 0

        # stage: "edit" (normal), "saving" (background task in flight), "done"
        self._stage: str = "edit"
        self._error: str = ""

        # spinner
        self._spinner = ["|", "/", "-", "\\"]
        self._spinner_idx = 0
        self._last_spin = 0.0

        # results read by the command handler
        self.result_cancelled = False
        self.result_saved = False
        self.result_activated = False
        self.result_loadout: Any = None

    # -- context injection ---------------------------------------------------

    def set_context(
        self,
        manager: Any = None,
        profile_manager: Any = None,
        event_bus: Any = None,
        mode: str = "create",
        base: Any = None,
        name_style: str = "plain",
        existing_names: Optional[List[str]] = None,
    ) -> None:
        self._manager = manager
        self._profile_manager = profile_manager
        self._event_bus = event_bus
        self._mode = mode if mode in ("create", "edit") else "create"
        self._base = base
        self._name_style = name_style if name_style in ("plain", "custom") else "plain"
        self._existing_names_seed = list(existing_names) if existing_names else None

    # -- lifecycle -------------------------------------------------------

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._build_widgets()
        logger.info(
            "LoadoutFormAltView: entered (mode=%s, model=%s)",
            self._mode,
            getattr(self._base, "model", None),
        )

    async def render_frame(self, delta_time: float) -> bool:
        if not self._renderer:
            return False
        if self.result_cancelled:
            return False

        width, height = self._renderer.get_terminal_size()
        theme = T()
        self._renderer.clear_screen()
        self._render_header(width, theme)
        if self._stage == "saving":
            self._render_spinner(5, width, theme)
        elif self._stage == "done":
            self._render_done(5, width, theme)
        else:
            self._render_fields(5, width, theme)
        self._render_footer(width, height, theme)
        return True

    # -- widget construction --------------------------------------------

    def _build_widgets(self) -> None:
        from kollabor_tui.widgets import (
            DropdownWidget,
            LabelWidget,
            SliderWidget,
            TextInputWidget,
        )

        base = self._base
        provider_profile = base.provider_profile if base else ""
        model = base.model if base else ""

        # Two different sets on purpose: the manager's create() only refuses
        # a name that collides with an EXPLICIT loadout (shadowing an
        # implicit/catalog name is an intentional, documented feature of
        # LoadoutManager) -- that's what save-time validation checks. The
        # broader all-names set (explicit + implicit) is only used to steer
        # the *suggested* name away from an accidental shadow by default.
        all_names: set = set()
        explicit_names: set = set()
        if self._existing_names_seed is not None:
            all_names = set(self._existing_names_seed)
            explicit_names = all_names
        elif self._manager is not None:
            try:
                entries = list(self._manager.list_loadouts())
                all_names = {entry.name for entry in entries}
                explicit_names = {entry.name for entry in entries if not entry.implicit}
            except Exception as exc:
                logger.warning("loadout form: list_loadouts() failed: %s", exc)
        if self._mode == "edit" and base is not None:
            all_names.discard(base.name)
            explicit_names.discard(base.name)
        elif base is not None and base.implicit:
            # Branching a new loadout off an implicit (catalog) row: that
            # row's own name is always in all_names (list_loadouts()
            # includes it), which would otherwise make _suggest_name()
            # think the bare model id is taken and jump straight to "-2".
            # Branching off an EXPLICIT row must NOT get this treatment --
            # that name really is taken by the loadout being copied from.
            all_names.discard(base.name)
        self._existing_names_final = explicit_names

        # update() has no "name" field -- explicit loadouts can't be
        # renamed, so edit mode shows the name as a locked label instead of
        # an editable field (see LoadoutManager.update()'s allowed-fields set).
        name_editable = not (self._mode == "edit" and base is not None and base.name)
        if name_editable:
            suggested_name = _suggest_name(model, all_names, self._name_style)
        else:
            suggested_name = base.name

        temperature = (
            base.temperature if (base and base.temperature is not None) else 0.7
        )
        effort = (base.effort if base else "") or "default"
        if effort not in _EFFORT_OPTIONS:
            effort = "default"
        max_tokens = (
            base.max_tokens
            if (base and base.max_tokens)
            else _default_max_tokens(model)
        )
        description = (base.description if base else "") or ""

        provider_type = _resolve_provider_type(self._profile_manager, provider_profile)
        provider_display = _provider_display(provider_type)
        provider_value = (
            f"{provider_display} ({provider_profile})"
            if provider_profile
            else provider_display
        )

        self._provider_widget = LabelWidget(label="Provider", value=provider_value)
        self._model_widget = LabelWidget(label="Model", value=model or "(none)")

        self._name_editable = name_editable
        if name_editable:
            self._name_widget = TextInputWidget(
                {"label": "Name", "value": suggested_name}, ""
            )
        else:
            self._name_widget = LabelWidget(label="Name", value=suggested_name)

        self._temperature_widget = SliderWidget(
            {
                "label": "Temperature",
                "value": float(temperature),
                "min_value": 0.0,
                "max_value": 1.0,
                "step": 0.05,
                "decimal_places": 2,
            },
            "",
        )
        self._effort_widget = DropdownWidget(
            {"label": "Effort", "options": list(_EFFORT_OPTIONS), "value": effort}, ""
        )
        self._max_tokens_supported = not _is_chatgpt_codex_profile(
            self._profile_manager, provider_profile
        )
        if self._max_tokens_supported:
            # SpinBoxWidget is unusable here -- see module docstring. A
            # validated integer TextInputWidget gives the same "type an exact
            # number" behavior; empty means "use the provider default".
            self._max_tokens_widget = TextInputWidget(
                {
                    "label": "Max Output Tokens",
                    "value": str(int(max_tokens)),
                    "validation": "integer",
                    "placeholder": "model default",
                },
                "",
            )
        else:
            # The ChatGPT OAuth/Codex endpoint rejects both max_tokens and
            # max_output_tokens. Make that capability boundary visible instead
            # of presenting an editable value that the wire layer must ignore.
            self._max_tokens_widget = LabelWidget(
                label="Max Output Tokens",
                value="backend default (ChatGPT OAuth)",
                help_text="This endpoint does not accept an output-token override.",
            )
        self._description_widget = TextInputWidget(
            {"label": "Description", "value": description, "placeholder": "optional"},
            "",
        )

        self._fields = []
        if name_editable:
            self._fields.append(self._name_widget)
        self._fields.extend(
            [
                self._temperature_widget,
                self._effort_widget,
                self._max_tokens_widget,
                self._description_widget,
            ]
        )
        self._focus_index = 0
        self._fields[0].set_focus(True)

    # -- input ------------------------------------------------------------

    async def handle_input(self, key_press: KeyPress) -> bool:
        name = key_press.name or ""
        char = key_press.char or ""

        if self._stage == "saving":
            return False

        if self._stage == "done":
            return True  # any key exits, mirroring SetupAltView's STAGE_DONE

        if name == "Escape" or char == "\x1b":
            self.result_cancelled = True
            return True

        if name == "Ctrl+S":
            self._trigger_save()
            return False

        if name in ("ArrowUp", "Up", "Shift+Tab"):
            self._move_focus(-1)
            return False
        if name in ("ArrowDown", "Down", "Tab"):
            self._move_focus(1)
            return False

        widget = self._fields[self._focus_index] if self._fields else None
        if widget is not None:
            self._error = ""
            consumed = widget.handle_input(key_press)
            if not consumed and (name == "Enter" or char in ("\r", "\n")):
                self._trigger_save()
        return False

    def _move_focus(self, delta: int) -> None:
        if not self._fields:
            return
        self._fields[self._focus_index].set_focus(False)
        self._focus_index = (self._focus_index + delta) % len(self._fields)
        self._fields[self._focus_index].set_focus(True)

    def _resolve_save_name(self) -> str:
        """The name to save under -- locked to the base's own name in edit
        mode (LoadoutManager.update() has no "name" field, so an explicit
        loadout can't be renamed through this form)."""
        if not self._name_editable:
            return self._base.name if self._base is not None else ""
        if self._name_widget is None:
            return ""
        return str(self._name_widget.get_pending_value() or "").strip()

    def _trigger_save(self) -> None:
        if self._stage != "edit":
            return
        if self._manager is None:
            self._error = "loadout manager unavailable."
            return
        name = self._resolve_save_name()
        if not name:
            self._error = "name is required."
            return
        if self._name_editable and name in self._existing_names_final:
            self._error = f"name '{name}' is already in use."
            return
        self._error = ""
        self._stage = "saving"
        self.spawn_background_task(self._run_save(name), name="loadout-save")

    async def _run_save(self, name: str) -> None:
        try:
            temperature = float(self._temperature_widget.get_pending_value())

            effort_raw = self._effort_widget.get_pending_value()
            effort = (
                "" if (not effort_raw or effort_raw == "default") else str(effort_raw)
            )

            max_tokens = None
            if self._max_tokens_supported:
                max_tokens_raw = str(
                    self._max_tokens_widget.get_pending_value() or ""
                ).strip()
                try:
                    max_tokens = int(max_tokens_raw) if max_tokens_raw else None
                except ValueError:
                    max_tokens = None

                # Validate against the model's known output ceiling -- saving
                # 320000 for a 128K-output model would just 400 at request time.
                if max_tokens is not None:
                    if max_tokens < 1:
                        self._error = "Max Output Tokens must be at least 1."
                        self._stage = "edit"
                        return
                    model_name = self._base.model if self._base else ""
                    ceiling = _model_registry_info(model_name).get("max_output")
                    if (
                        isinstance(ceiling, int)
                        and ceiling > 0
                        and max_tokens > ceiling
                    ):
                        self._error = (
                            f"Max Output Tokens {max_tokens:,} exceeds the model limit "
                            f"({ceiling:,})."
                        )
                        self._stage = "edit"
                        return

            description = str(
                self._description_widget.get_pending_value() or ""
            ).strip()

            base = self._base
            provider_profile = base.provider_profile if base else ""
            model = base.model if base else ""

            if self._mode == "edit":
                ok = bool(
                    self._manager.update(
                        name,
                        temperature=temperature,
                        effort=effort,
                        max_tokens=max_tokens,
                        description=description,
                    )
                )
            else:
                ok = bool(
                    self._manager.create(
                        name,
                        provider_profile=provider_profile,
                        model=model,
                        temperature=temperature,
                        effort=effort,
                        max_tokens=max_tokens,
                        description=description,
                    )
                )

            if not ok:
                self._error = f"could not save loadout '{name}'."
                self._stage = "edit"
                return

            loadout = None
            try:
                loadout = self._manager.get(name)
            except Exception:
                loadout = None
            if loadout is None:
                # Manager lookup came back empty -- fall back to a locally
                # built record so the confirmation screen and the command
                # handler's result message always have something to show.
                from kollabor_ai.loadout_manager import Loadout

                loadout = Loadout(
                    name=name,
                    provider_profile=provider_profile,
                    model=model,
                    temperature=temperature,
                    effort=effort,
                    max_tokens=max_tokens,
                    description=description,
                )

            activated = None
            try:
                activated = await asyncio.wait_for(
                    self._manager.activate(loadout, self._event_bus), timeout=20.0
                )
            except asyncio.TimeoutError:
                logger.warning("loadout form: activate timed out for %s", name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("loadout form: activate failed for %s: %s", name, exc)

            self.result_saved = True
            self.result_activated = bool(activated)
            self.result_loadout = activated or loadout
            self._stage = "done"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = _short_error(str(exc))
            logger.error("loadout form: save failed: %s", exc)
            self._stage = "edit"

    # -- rendering ------------------------------------------------------

    def _render_header(self, width: int, theme: Any) -> None:
        title = "  Edit Loadout  " if self._mode == "edit" else "  New Loadout  "
        r = self._renderer
        r.write_at(0, 0, solid_fg(str(C["half_bottom"]) * width, theme.primary[0]), "")
        r.write_at(
            0,
            1,
            solid(title.ljust(width), theme.primary[0], theme.text_dark, width),
            "",
        )

    def _render_fields(self, top: int, width: int, theme: Any) -> None:
        y = top
        render_width = width - 4
        locked = [self._provider_widget, self._model_widget]
        if not self._name_editable and self._name_widget is not None:
            locked.insert(0, self._name_widget)
        for widget in locked:
            for line in _render_widget_safe(widget, render_width, "middle"):
                self._renderer.write_at(2, y, line, "")
                y += 1
        y += 1

        n = len(self._fields)
        for i, widget in enumerate(self._fields):
            if n == 1:
                position = "only"
            elif i == 0:
                position = "first"
            elif i == n - 1:
                position = "last"
            else:
                position = "middle"
            for line in _render_widget_safe(widget, render_width, position):
                self._renderer.write_at(2, y, line, "")
                y += 1

        if self._error:
            y += 1
            self._renderer.write_at(
                2, y, solid_fg(f"! {self._error}"[: width - 4], theme.error[0]), ""
            )

    def _render_spinner(self, top: int, width: int, theme: Any) -> None:
        now = time.monotonic()
        if now - self._last_spin > 0.12:
            self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner)
            self._last_spin = now
        spin = self._spinner[self._spinner_idx]
        self._renderer.write_at(2, top + 1, f"{spin} saving..."[: width - 4], "")

    def _render_done(self, top: int, width: int, theme: Any) -> None:
        self._renderer.write_at(
            2, top, solid_fg("✓ Loadout Saved", theme.success[0]), ""
        )
        loadout = self.result_loadout
        y = top + 2
        if loadout is not None:
            lines = [
                f"name:      {loadout.name}",
                f"model:     {loadout.model}",
                f"provider:  {loadout.provider_profile}",
                (
                    "active now."
                    if self.result_activated
                    else f"saved (activate with /llm {loadout.name})."
                ),
            ]
            for line in lines:
                self._renderer.write_at(2, y, line[: width - 4], "")
                y += 1
        self._renderer.write_at(2, y + 1, "press any key to continue.", "")

    def _render_footer(self, width: int, height: int, theme: Any) -> None:
        r = self._renderer
        footer_y = height - 2
        r.write_at(
            0, footer_y, solid_fg(str(C["half_bottom"]) * width, theme.dark[1]), ""
        )
        hints = {
            "saving": " saving...",
            "done": " any key to continue",
        }
        text = hints.get(
            self._stage,
            " tab/↑↓ field | ←→ adjust | enter/space toggle | ctrl+s save | esc back",
        )
        r.write_at(
            0,
            footer_y + 1,
            solid(text.ljust(width), theme.dark[1], theme.text_dim, width),
            "",
        )
