"""Halt and Resume in the terminal, built from the ``connect-halt`` panel's describe().

Same pattern as ``voice_config_altview`` over ``VoicePanel``: the panel owns the
screen's data and actions; this file only draws it and maps keys. The halted
interactive launch (``halt_runtime.run_interactive``) drives the same
``HaltScreen`` with plain prints, since no app or renderer runs there.
"""

from __future__ import annotations

import asyncio
import copy
import textwrap
from typing import Any

from kollabor.panels.connect import ConnectHaltNewPanel, ConnectHaltPanel
from kollabor_tui.altview.base import AltView, AltViewMetadata
from plugins.altview.connect_altview import _draw_lines, _fit
from plugins.hub.device_names import display_width

# toolbar action id -> key (the one-machine-only and opt-out keys are letters a person reads)
TOOLBAR_KEYS = {"halt_new": "n", "propose_resume": "p", "resume_local": "l", "no_count": "o", "count": "o"}


class HaltScreen:
    """State and keys of the halt view; no terminal, so it is testable and reusable."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.panel: dict = {}
        self.selected = 0
        self.armed = ""  # "<row id>:<action>" or "toolbar:<id>" waiting for a second press
        self._armed_context: Any = None
        self.message = ""
        self.reason: str | None = None  # typing a halt reason when not None
        self.scroll = 0
        self.follow = True
        self._generation = 0
        self._action_active = False

    async def load(self) -> None:
        if self._action_active:
            return
        self._generation += 1
        generation = self._generation
        panel = await ConnectHaltPanel().describe(self.ctx, {})
        if not self._action_active and generation == self._generation:
            self._adopt_panel(panel)

    def _context(self) -> tuple:
        return self.panel, self.selected, self.reason

    def _adopt_panel(self, panel: dict) -> None:
        self.panel = panel
        self.selected = max(0, min(self.selected, len(self.panel.get("rows") or []) - 1))
        if self.armed and self._armed_context != self._context():
            self.armed, self.message, self._armed_context = "", "", None

    def _row_action(self) -> tuple[dict, dict] | None:
        rows = self.panel.get("rows") or []
        if not rows:
            return None
        row = rows[self.selected]
        labels = {a["id"]: a for a in self.panel.get("row_actions") or []}
        ids = [a for a in row.get("actions") or [] if a in labels]
        return (row, labels[ids[0]]) if ids else None

    async def _run(self, key: str, action: dict, payload: dict, panel: Any = None) -> None:
        if self._action_active:
            return
        self.follow = True
        if action.get("confirm") and (self.armed != key or self._armed_context != self._context()):
            self.armed = key
            self._armed_context = copy.deepcopy(self._context())
            self.message = f"press again to {action['label']}"
            return
        self.armed = ""
        self._armed_context = None
        # past the arm step the person has pressed twice (or the action needs no second press)
        self._action_active = True
        self._generation += 1  # invalidate refreshes already in flight
        try:
            result = await (panel or ConnectHaltPanel()).act(self.ctx, action["id"], {**payload, "confirmed": True})
            self.message = result.get("message") or ""
            nxt = result.get("panel") or result.get("open")
            if not (isinstance(nxt, dict) and nxt.get("kind") == "picker"):
                nxt = await ConnectHaltPanel().describe(self.ctx, {})
            self._adopt_panel(nxt)
        except BaseException:
            # The action may have taken effect even if its response was lost.
            self._adopt_panel({})
            raise
        finally:
            self._generation += 1
            self._action_active = False

    async def key(self, name: str, char: str = "") -> bool:
        """One key press; True when the view should close."""
        if self.reason is not None:
            return await self._reason_key(name, char)
        if name == "Escape":
            return True
        rows = self.panel.get("rows") or []
        if name == "ArrowUp":
            self.selected, self.armed = max(0, self.selected - 1), ""
            self.message, self.follow = "", True
        elif name == "ArrowDown":
            self.selected, self.armed = max(0, min(len(rows) - 1, self.selected + 1)), ""
            self.message, self.follow = "", True
        elif name in ("PageUp", "PageDown"):
            self.scroll += -5 if name == "PageUp" else 5
            self.follow = False
        elif name == "Enter":
            picked = self._row_action()
            if picked:
                row, action = picked
                await self._run(f"{row['id']}:{action['id']}", action, {"id": row["id"]})
        else:
            for action in self.panel.get("toolbar_actions") or []:
                if TOOLBAR_KEYS.get(action["id"]) == char.lower():
                    if action["id"] == "halt_new":
                        self.reason, self.armed, self.message = "", "", ""
                        self.follow = True
                    else:
                        await self._run(f"toolbar:{action['id']}", action, {})
                    break
        return False

    async def _reason_key(self, name: str, char: str) -> bool:
        if name in ("PageUp", "PageDown"):
            self.scroll += -5 if name == "PageUp" else 5
            self.follow = False
        elif name == "Escape":
            self.reason, self.armed, self.message = None, "", ""
            self.follow = True
        elif name == "Enter":
            reason = self.reason or ""
            await self._run(
                "halt", {"id": "halt", "label": "Halt Network", "confirm": True}, {"reason": reason},
                panel=ConnectHaltNewPanel(),
            )
            if not self.armed:
                self.reason = None
        elif name == "Backspace":
            self.reason, self.armed = (self.reason or "")[:-1], ""
            self.message, self.follow = "", True
        elif char and char.isprintable() and len(self.reason or "") < 200:
            self.reason, self.armed = (self.reason or "") + char, ""
            self.message, self.follow = "", True
        return False


def _wrap(text: str, width: int, indent: str = " ", *, preserve_spaces: bool = False) -> list[str]:
    """Wrap text without splitting terminal cells or discarding long words."""
    room = max(1, width - len(indent))
    result = []
    for line in textwrap.wrap(text, width=room, break_long_words=True, break_on_hyphens=False,
                              drop_whitespace=not preserve_spaces, replace_whitespace=not preserve_spaces) or [""]:
        while display_width(line) > room:
            cut = len(line)
            while cut > 1 and display_width(line[:cut]) > room:
                cut -= 1
            result.append(indent + line[:cut])
            line = line[cut:]
        result.append(indent + line)
    return result


def _halt_parts(
    screen: HaltScreen, width: int, *, viewport: bool = True
) -> tuple[list[str], list[str], tuple[int, int]]:
    panel = screen.panel
    lines: list[str] = []
    for item in panel.get("summary") or []:
        lines += _wrap(f"{item['label']:<15}{item['value']}", width)
    rows = panel.get("rows") or []
    labels = {a["id"]: a["label"] for a in panel.get("row_actions") or []}
    group = None
    selected = (0, 0)
    for index, row in enumerate(rows):
        if row.get("group") != group:
            group = row.get("group")
            lines += ["", *_wrap(str(group), width)]
        mark = ">" if index == screen.selected else " "
        start = len(lines)
        lines += _wrap(f"{mark} {row['label']}", width)
        if row.get("detail"):
            lines += _wrap(str(row["detail"]), width, "     ")
        acts = [labels[a] for a in row.get("actions") or [] if a in labels]
        if acts and index == screen.selected:
            lines += _wrap(f"enter {acts[0]}", width, "     ")
        if index == screen.selected:
            selected = (start, len(lines))
    if panel.get("notice"):
        lines += ["", *_wrap(panel["notice"], width)]
    if screen.reason is not None:
        start = len(lines)
        lines += ["", *_wrap(f"Reason: {screen.reason}_", width, preserve_spaces=True)]
        selected = (start, len(lines))
    keys = ["up/down select"] if len(rows) > 1 else []
    if screen.reason is not None:
        keys = ["type the reason", "enter halt network", "esc cancel"]
    else:
        keys += [f"{TOOLBAR_KEYS[a['id']]} {a['label'].rstrip('…').lower()}"
                 for a in panel.get("toolbar_actions") or [] if a["id"] in TOOLBAR_KEYS]
        keys.append("esc close")
    if viewport:
        keys.append("pgup/pgdn scroll")
    footer: list[str] = []
    current = ""
    for key in keys:
        candidate = f"{current}  {key}" if current else key
        if display_width(candidate) <= max(1, width - 1):
            current = candidate
        else:
            if current:
                footer += _wrap(current, width)
            current = key
    if current:
        footer += _wrap(current, width)
    if screen.message:
        footer = _wrap(screen.message, width) + footer
    return lines, footer, selected


def halt_view_lines(screen: HaltScreen, width: int) -> list[str]:
    """Full plain-text view for the halted waiter and other non-viewport callers."""
    body, footer, _ = _halt_parts(screen, width, viewport=False)
    return [_fit(" Halt and Resume", width), *body, "", *footer]


def halt_viewport(screen: HaltScreen, width: int, height: int, header_rows: int) -> tuple[list[str], bool]:
    """Keep the complete action context visible, or refuse actions until resize."""
    lines, safe, _ = _halt_viewport_access(screen, width, height, header_rows)
    return lines, safe


def _halt_viewport_access(
    screen: HaltScreen, width: int, height: int, header_rows: int
) -> tuple[list[str], bool, bool]:
    """Return the frame, whether controls fit, and whether row actions fit."""
    if width < 8 or height < header_rows:
        return [], False, False
    body, footer, selected = _halt_parts(screen, width)
    target: list[str] = []
    confirmation_footer = footer
    if screen.reason is not None:
        target = _wrap(f"Reason: {screen.reason}_", width, preserve_spaces=True)
    else:
        rows = screen.panel.get("rows") or []
        if rows:
            row = rows[screen.selected]
            target = _wrap(f"> {row['label']}", width)
            if row.get("detail"):
                target += _wrap(str(row["detail"]), width, "     ")
            picked = screen._row_action()
            if picked:
                # The panel's names and abbreviated fingerprints can collide. The
                # full action ID is the exact target sent to the panel backend.
                target += _wrap(f"Target ID: {row['id']}", width)
                target += _wrap(f"enter {picked[1]['label']}", width)
                if picked[1].get("confirm"):
                    message_lines = _wrap(screen.message, width) if screen.message else []
                    confirmation_footer = (
                        _wrap(f"press again to {picked[1]['label']}", width)
                        + footer[len(message_lines):]
                    )
    fixed = target + footer
    required = max(len(fixed), len(target) + len(confirmation_footer))
    if required + header_rows > height:
        if screen.reason is not None:
            return [], False, False
        independent = [a for a in screen.panel.get("toolbar_actions") or []
                       if a.get("id") in ("halt_new", "resume_local")]
        if not independent:
            return [], False, False
        limited = _wrap("Resize to use row actions", width)
        message_lines = []
        if screen.message and (not screen.armed or screen.armed == "toolbar:resume_local"):
            message_lines = _wrap(screen.message, width)
            limited += message_lines
        for action in independent:
            limited += _wrap(f"{TOOLBAR_KEYS[action['id']]} {action['label']}", width)
        limited += _wrap("esc close", width)
        prospective = max(
            (len(_wrap(f"press again to {action['label']}", width))
             for action in independent if action.get("confirm")),
            default=0,
        )
        required = len(limited) - len(message_lines) + max(len(message_lines), prospective)
        if required + header_rows > height:
            return [], False, False
        return limited, True, False
    capacity = height - header_rows - len(fixed)
    if screen.follow:
        start, end = selected
        if end > screen.scroll + capacity:
            screen.scroll = end - capacity
        if start < screen.scroll:
            screen.scroll = start
    screen.scroll = max(0, min(screen.scroll, max(0, len(body) - capacity)))
    return [*body[screen.scroll:screen.scroll + capacity], *fixed], True, True


def halt_input_allowed(screen: HaltScreen, width: int, height: int, header_rows: int,
                       name: str, char: str) -> bool:
    """Only accept controls whose complete context fits in the current frame."""
    _, safe, row_safe = _halt_viewport_access(screen, width, height, header_rows)
    if not safe:
        return False
    if row_safe or screen.reason is not None:
        return True
    if name == "Enter":
        return False
    if char:
        return any(a.get("id") in ("halt_new", "resume_local") and
                   TOOLBAR_KEYS[a["id"]] == char.lower()
                   for a in screen.panel.get("toolbar_actions") or [])
    return True


def halt_frame_state(screen: HaltScreen) -> tuple:
    """Identify the action context actually presented to the operator."""
    return (repr(screen.panel), screen.selected, screen.reason, screen.armed, screen.message, screen.scroll)


class ConnectHaltAltView(AltView):
    """`h` on the Connect screen: the connect-halt panel, drawn like the Connect screen."""

    def __init__(self, ctx: Any = None) -> None:
        if ctx is None:  # discovery builds every AltView with no arguments
            import os

            from plugins.hub.halt_context import HaltContext

            ctx = HaltContext(os.getcwd())
        super().__init__(AltViewMetadata(
            plugin_type="connect-halt",
            description="Halt and resume the agent network",
            version="1.0.0",
            author="Kollabor",
            category="internal",
            icon="[HALT]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        ))
        self.target_fps = 1.0
        self.render_on_timer = True
        self.screen = HaltScreen(ctx)
        self._renderer: Any = None
        self._visible_frame: tuple | None = None

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        try:
            await self.screen.load()  # fresh data before the first frame, never an earlier snapshot
        except Exception:  # the panel itself reports an unreachable network
            pass
        self.spawn_background_task(self._poll(), "halt-poll")

    async def _poll(self) -> None:
        while True:
            try:
                await self.screen.load()
            except asyncio.CancelledError:
                raise
            except Exception:  # the panel itself reports an unreachable network
                pass
            self.request_render()
            await asyncio.sleep(5.0)

    async def render_frame(self, delta_time: float) -> bool:
        if self._renderer is None:
            return False
        width, height = self._renderer.get_terminal_size()
        if width <= 0 or height <= 0:
            return True
        body, safe = halt_viewport(self.screen, width, height, 4)
        if safe:
            _draw_lines(self._renderer, [_fit(" Halt and Resume", width), *body], width,
                        "stop every agent on every machine")
        else:
            self._renderer.clear_screen()
            for y, line in enumerate(_wrap("Resize terminal to use controls", width)[:height]):
                self._renderer.write_at(0, y, _fit(line, width), "")
        self._visible_frame = (width, height, halt_frame_state(self.screen)) if safe else None
        return True

    async def handle_input(self, key_press: Any) -> bool:
        name, char = key_press.name or "", key_press.char or ""
        size = self._renderer.get_terminal_size() if self._renderer else (0, 0)
        fresh = self._visible_frame == (*size, halt_frame_state(self.screen))
        if not fresh and (name == "Enter" or (self.screen.reason is None and char)):
            self.request_render()
            return False
        if (name == "Enter" or (self.screen.reason is None and char)) and not halt_input_allowed(
            self.screen, *size, 4, name, char
        ):
            self.request_render()
            return False
        self._visible_frame = None
        try:
            done = await self.screen.key(name, char)
        except Exception as exc:  # noqa: BLE001 - halt errors are operator-safe text
            self.screen.message = str(exc)
            done = False
        self.request_render()
        return done

    async def on_suspend(self) -> None:
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await super().on_suspend()
