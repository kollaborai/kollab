"""Halt and Resume in the terminal, built from the ``connect-halt`` panel's describe().

Same pattern as ``voice_config_altview`` over ``VoicePanel``: the panel owns the
screen's data and actions; this file only draws it and maps keys. The halted
interactive launch (``halt_runtime.run_interactive``) drives the same
``HaltScreen`` with plain prints, since no app or renderer runs there.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kollabor.panels.connect import ConnectHaltNewPanel, ConnectHaltPanel
from kollabor_tui.altview.base import AltView, AltViewMetadata
from plugins.altview.connect_altview import _draw_lines, _fit

# toolbar action id -> key (the one-machine-only and opt-out keys are letters a person reads)
TOOLBAR_KEYS = {"halt_new": "n", "propose_resume": "p", "resume_local": "l", "no_count": "o", "count": "o"}


class HaltScreen:
    """State and keys of the halt view; no terminal, so it is testable and reusable."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.panel: dict = {}
        self.selected = 0
        self.armed = ""  # "<row id>:<action>" or "toolbar:<id>" waiting for a second press
        self.message = ""
        self.reason: str | None = None  # typing a halt reason when not None

    async def load(self) -> None:
        self.panel = await ConnectHaltPanel().describe(self.ctx, {})
        self.selected = max(0, min(self.selected, len(self.panel.get("rows") or []) - 1))

    def _row_action(self) -> tuple[dict, dict] | None:
        rows = self.panel.get("rows") or []
        if not rows:
            return None
        row = rows[self.selected]
        labels = {a["id"]: a for a in self.panel.get("row_actions") or []}
        ids = [a for a in row.get("actions") or [] if a in labels]
        return (row, labels[ids[0]]) if ids else None

    async def _run(self, key: str, action: dict, payload: dict, panel: Any = None) -> None:
        if action.get("confirm") and self.armed != key:
            self.armed = key
            self.message = f"press again to {action['label']}"
            return
        self.armed = ""
        result = await (panel or ConnectHaltPanel()).act(self.ctx, action["id"], payload)
        self.message = result.get("message") or ""
        nxt = result.get("panel") or result.get("open")
        if isinstance(nxt, dict) and nxt.get("kind") == "picker":
            self.panel = nxt
        else:
            await self.load()

    async def key(self, name: str, char: str = "") -> bool:
        """One key press; True when the view should close."""
        if self.reason is not None:
            return await self._reason_key(name, char)
        if name == "Escape":
            return True
        rows = self.panel.get("rows") or []
        if name == "ArrowUp":
            self.selected, self.armed = max(0, self.selected - 1), ""
        elif name == "ArrowDown":
            self.selected, self.armed = max(0, min(len(rows) - 1, self.selected + 1)), ""
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
                    else:
                        await self._run(f"toolbar:{action['id']}", action, {})
                    break
        return False

    async def _reason_key(self, name: str, char: str) -> bool:
        if name == "Escape":
            self.reason, self.armed, self.message = None, "", ""
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
        elif char and char.isprintable() and len(self.reason or "") < 200:
            self.reason, self.armed = (self.reason or "") + char, ""
        return False


def halt_view_lines(screen: HaltScreen, width: int) -> list[str]:
    """The view as plain lines: title, summary, rows, notice, message, key hint."""
    panel = screen.panel
    lines = [_fit(" Halt and Resume", width)]
    for item in panel.get("summary") or []:
        lines.append(_fit(f" {item['label']:<15}{item['value']}", width))
    rows = panel.get("rows") or []
    labels = {a["id"]: a["label"] for a in panel.get("row_actions") or []}
    group = None
    for index, row in enumerate(rows):
        if row.get("group") != group:
            group = row.get("group")
            lines += ["", _fit(f" {group}", width)]
        mark = ">" if index == screen.selected else " "
        lines.append(_fit(f" {mark} {row['label']}", width))
        if row.get("detail"):
            lines.append(_fit(f"     {row['detail']}", width))
        acts = [labels[a] for a in row.get("actions") or [] if a in labels]
        if acts and index == screen.selected:
            lines.append(_fit(f"     enter {acts[0]}", width))
    if panel.get("notice"):
        lines += ["", _fit(" " + panel["notice"], width)]
    if screen.reason is not None:
        lines += ["", _fit(f" Reason: {screen.reason}_", width)]
    if screen.message:
        lines += ["", _fit(" " + screen.message, width)]
    keys = ["up/down select"] if len(rows) > 1 else []
    if screen.reason is not None:
        keys = ["type the reason", "enter halt network", "esc cancel"]
    else:
        keys += [f"{TOOLBAR_KEYS[a['id']]} {a['label'].rstrip('…').lower()}"
                 for a in panel.get("toolbar_actions") or [] if a["id"] in TOOLBAR_KEYS]
        keys.append("esc close")
    return lines + ["", _fit(" " + "  ".join(keys), width)]


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

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
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
        lines = halt_view_lines(self.screen, width)
        if len(lines) > height - 3:
            lines = lines[: max(1, height - 4)] + [lines[-1]]
        _draw_lines(self._renderer, lines, width, "stop every agent on every machine")
        return True

    async def handle_input(self, key_press: Any) -> bool:
        try:
            done = await self.screen.key(key_press.name or "", key_press.char or "")
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
