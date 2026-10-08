"""The knock screen (`/connect knocks`, docs/specs/agent-network-simple-flow.md, Story 5).

Ringing knocks, the calls this device placed, the missed list, blocked routes,
the contact list and who may knock. It reloads a snapshot from the process that
owns the relay (plugins/hub/knocks.py) every two seconds and acts through one
callback. Names, short fingerprints and routes only, never keys. A stranger's
text shows here and nowhere a model reads.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.key_parser import KeyPress
from plugins.altview.connect_style import draw_header, paint_keys, paint_row, paint_value
from plugins.hub.device_names import clip_display, display_name, request_row

_POLL_SECONDS = 2.0
_LABEL_WIDTH = 15
_TAGLINE = "who can reach your agents from outside"
_LABELS = frozenset({"knocks", "ringing", "knocking", "missed", "blocked", "contacts", "who may knock"})
_MAX_TEXT_BYTES = 2048
MODES = ("everyone", "contacts", "nobody")

# What each kind of row answers to: (key, action, hint).
ROW_KEYS = {
    "ringing": (("a", "accept", "[a]ccept"), ("r", "reject", "[r]eject"), ("b", "block", "[b]lock")),
    "call": (("s", "stop", "[s]top"),),
    "missed": (
        ("k", "knock_back", "[k]nock back"),
        ("b", "block", "[b]lock"),
        ("d", "delete", "[d]elete"),
    ),
    "blocked": (("u", "unblock", "[u]nblock"),),
    "contact": (("x", "unexpect", "[x] remove"),),
}

LoadCallback = Callable[[], Awaitable[dict] | dict]
ActCallback = Callable[[str, dict], Awaitable[str] | str]


def _metadata() -> AltViewMetadata:
    return AltViewMetadata(
        plugin_type="knocks",
        description="Knocks: ringing, missed, who may knock",
        version="1.0.0",
        author="Kollabor",
        category="internal",
        icon="[LINK]",
        aliases=[],
        supports_named_sessions=False,
        supports_background=False,
    )


def _plain(value: Any) -> str:
    """One printable line of sender-chosen text."""
    text = str(value).replace("\n", " ").replace("\t", " ")
    return "".join(
        char for char in text if char.isprintable() and not unicodedata.category(char).startswith("C")
    )


def _clock(seconds: Any) -> str:
    seconds = max(0, int(seconds)) if isinstance(seconds, int) else 0
    return f"{seconds // 60}:{seconds % 60:02d}"


def _until(epoch: int) -> str:
    left = max(0, epoch - int(time.time()))
    hours, minutes = divmod(left // 60, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def selectable_rows(snapshot: dict | None) -> list[tuple[str, dict]]:
    """Every row a key can act on, top to bottom: (kind, row)."""
    if not snapshot:
        return []
    return (
        [("ringing", row) for row in snapshot.get("ringing", [])]
        + [("call", row) for row in snapshot.get("calls", [])]
        + [("missed", row) for row in snapshot.get("missed", [])]
        + [("blocked", row) for row in snapshot.get("blocked", [])]
        + [("contact", row) for row in snapshot.get("contacts", [])]
    )


def _row_id(kind: str, row: dict) -> tuple[str, str]:
    return kind, str(row.get("id") or row.get("route") or "")


def _hint(kind: str) -> str:
    return " ".join(hint for _key, _action, hint in ROW_KEYS[kind])


def _hint_lines(kind: str, room: int, indent: str) -> list[str]:
    """A row's keys on lines of their own, wrapped between keys, never inside one."""
    lines = [indent]
    for _key, _action, token in ROW_KEYS[kind]:
        if lines[-1] != indent and len(lines[-1]) + 1 + len(token) > room:
            lines.append(indent)
        lines[-1] += token if lines[-1] == indent else " " + token
    return lines


def knock_screen_lines(
    snapshot: dict | None,
    selected: int,
    width: int,
    *,
    message: str = "",
    prompt: tuple[str, str] | None = None,
    error: str = "",
) -> list[str]:
    """The knock screen as plain lines, each at most `width` columns.

    Pure: snapshot in, strings out. The first line is the title. `prompt` is
    (device, text typed so far) while a knock back is being written.
    """
    lines = [clip_display(" Knocks", width)]
    if snapshot is None:
        lines += _block("knocks", [error or "loading…"], width)
        return lines + ["", clip_display(" esc close", width)]
    rows = selectable_rows(snapshot)
    current = rows[selected] if 0 <= selected < len(rows) else None
    room = max(20, width - 1 - _LABEL_WIDTH)

    def entry(kind: str, row: dict, head: str, tail: str, quote: str = "") -> list[str]:
        here = current is not None and current[1] is row
        lead = "> " if here else "  "
        hint = _hint(kind) if here else ""
        indent = " " * (len(lead) + (3 if kind in ("ringing", "missed") else 0))  # past "1. "
        quote = _plain(quote)
        out = request_row(lead + head, tail, room, hint=hint, quote=quote, indent=indent)
        if len(out) > 1 or f'"{quote}"' not in out[0]:
            # Too wide for one line, or the words were cut: they and the keys get lines of their own.
            out = request_row(lead + head, tail, room, indent=indent)
            if quote:
                out.append(f'{indent}"{clip_display(quote, room - len(indent) - 2)}"')
            if hint:
                out += _hint_lines(kind, room, indent)
        return [line.rstrip() for line in out]

    ringing = []
    for number, row in enumerate(snapshot["ringing"], 1):
        ringing += entry(
            "ringing",
            row,
            f"{number}. {display_name(row['device'])}",
            f"device ID {row['fingerprint']}   {_clock(row['left'])}",
            row["text"],
        )
    lines += _block("ringing", ringing or ["none"], width)
    if snapshot["calls"]:
        calls = []
        for row in snapshot["calls"]:
            state = (
                f"ringing {_clock(row['left'])}"
                if row["state"] == "ringing"
                else f"redialing, next in {_clock(row['left'])}"
            )
            calls += entry("call", row, row["target"], state)
        lines += _block("knocking", calls, width)
    missed = [f"{len(snapshot['missed'])} of {snapshot['missed_limit']}"]
    if snapshot["missed"]:
        missed[0] += "   [c] clear all"
    for number, row in enumerate(snapshot["missed"], 1):
        when = time.strftime("%b %d %H:%M", time.localtime(row["at"]))
        missed += entry(
            "missed",
            row,
            f"{number}. {display_name(row['device'])}",
            f"device ID {row['fingerprint']}   {when}",
            row["text"],
        )
    lines += _block("missed", missed, width)
    blocked = [str(len(snapshot["blocked"]))]
    for row in snapshot["blocked"]:
        blocked += entry("blocked", row, row["route"], display_name(row["device"]) if row["device"] else "")
    lines += _block("blocked", blocked, width)
    contacts = [str(len(snapshot["contacts"]))]
    for row in snapshot["contacts"]:
        contacts += entry(
            "contact", row, row["route"], "first knock accepted" if row["expected"] else ""
        )
    lines += _block("contacts", contacts, width)
    mode = snapshot["mode"]
    if snapshot["mode_until"]:
        mode += f" for {_until(snapshot['mode_until'])}"
    lines += _block("who may knock", [f"{mode}   [w] change"], width)
    if not snapshot["online"]:
        lines += ["", clip_display(" this device is offline: knocks cannot reach it", width)]
    if message:
        lines += ["", clip_display(" " + _plain(message), width)]
    lines.append("")
    if prompt is not None:
        device, text = prompt
        lines.append(
            clip_display(f" knock back to {display_name(device)}: {_plain(text)}_", width)
        )
        lines.append(clip_display(" enter send   esc cancel", width))
    else:
        keys = ("up/down select   " if len(rows) > 1 else "") + "w who may knock   esc close"
        lines.append(clip_display(" " + keys, width))
    return lines


def _block(label: str, values: list[str], width: int) -> list[str]:
    return [
        clip_display(" " + (label if index == 0 else "").ljust(_LABEL_WIDTH) + value, width)
        for index, value in enumerate(values)
    ]


def _valid_snapshot(value: Any) -> bool:
    """Every field the screen reads, with the type it reads it as."""

    def rows(name: str, fields: dict[str, type]) -> bool:
        items = value.get(name)
        return isinstance(items, list) and len(items) <= 256 and all(
            isinstance(row, dict)
            and all(type(row.get(key)) is kind for key, kind in fields.items())
            for row in items
        )

    knock = {"id": str, "device": str, "fingerprint": str, "route": str, "text": str}
    return (
        isinstance(value, dict)
        and type(value.get("online")) is bool
        and value.get("mode") in MODES
        and type(value.get("mode_until")) is int
        and type(value.get("missed_limit")) is int
        and rows("ringing", {**knock, "left": int})
        and rows("missed", {**knock, "at": int})
        and rows("calls", {"route": str, "target": str, "state": str, "left": int})
        and rows("blocked", {"route": str, "device": str})
        and rows("contacts", {"route": str, "expected": bool})
    )


class KnockScreenAltView(AltView):
    """The knock screen. Up/down select a row; its keys are printed on it."""

    def __init__(self, on_load: LoadCallback | None = None, on_act: ActCallback | None = None):
        # Defaults let the AltView command integrator build a bare instance to
        # read its metadata; without callbacks the screen is empty and inert.
        super().__init__(_metadata())
        self._on_load = on_load
        self._on_act = on_act
        self._renderer: Any = None
        self._snapshot: dict | None = None
        self._error = ""
        self._selected = 0
        self._message = ""
        self._prompt: tuple[dict, str] | None = None  # (missed row, text so far)
        # Cleared by an action, set again once a redraw shows the result, so a
        # held or double-tapped key cannot act on a row the human has not seen.
        self._armed = True

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._snapshot, self._error, self._message, self._prompt = None, "", "", None
        self._selected, self._armed = 0, True
        if self._on_load is None:
            self._snapshot = {
                "online": False, "mode": "everyone", "mode_until": 0, "missed_limit": 20,
                "ringing": [], "calls": [], "missed": [], "blocked": [], "contacts": [],
            }
            return
        self.spawn_background_task(self._poll(), "poll")

    async def _poll(self) -> None:
        while True:
            await self._refresh()
            await asyncio.sleep(_POLL_SECONDS)

    async def _refresh(self) -> None:
        """Reload; on a failure keep what is on screen (or say it is unavailable)."""
        keep = None
        rows = selectable_rows(self._snapshot)
        if 0 <= self._selected < len(rows):
            keep = _row_id(*rows[self._selected])
        try:
            result = self._on_load()
            if inspect.isawaitable(result):
                result = await result
            if not _valid_snapshot(result):
                raise ValueError("unexpected knock snapshot")
            self._snapshot = result
            rows = selectable_rows(result)
            ids = [_row_id(*row) for row in rows]
            self._selected = ids.index(keep) if keep in ids else min(self._selected, max(0, len(rows) - 1))
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._snapshot is None:
                self._error = "knocks are unavailable"
        self.request_render()

    async def render_frame(self, delta_time: float) -> bool:
        if self._renderer is None:
            return False
        width, height = self._renderer.get_terminal_size()
        if width <= 0 or height <= 0:
            return True
        prompt = None
        if self._prompt is not None:
            prompt = (self._prompt[0]["device"], self._prompt[1])
        lines = knock_screen_lines(
            self._snapshot,
            self._selected,
            width,
            message=self._message,
            prompt=prompt,
            error=self._error,
        )
        renderer = self._renderer
        renderer.clear_screen()
        # The Connect screen's look (connect_style), so the two siblings match.
        top = draw_header(renderer, 0, width, lines[0].strip(), _TAGLINE) + 1
        body = lines[1:]
        if len(body) > height - top:  # keep the footer; cut the middle
            body = body[: max(0, height - top - 3)] + ["  …"] + body[-2:]
        for offset, line in enumerate(body):
            if offset == len(body) - 1:
                painted = paint_keys(line)
            else:
                painted = paint_row(line, 1 + _LABEL_WIDTH, _LABELS) or paint_value(line)
            renderer.write_at(0, top + offset, painted, "")
        self._armed = True
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if self._prompt is not None:
            return await self._prompt_input(key_press)
        if key_press.name == "Escape":
            return True
        rows = selectable_rows(self._snapshot)
        if key_press.name == "ArrowUp":
            self._selected = max(0, self._selected - 1)
        elif key_press.name == "ArrowDown":
            self._selected = max(0, min(len(rows) - 1, self._selected + 1))
        elif self._armed and key_press.char and self._snapshot is not None:
            await self._key(key_press.char.lower(), rows)
        self.request_render()
        return False

    async def _key(self, char: str, rows: list[tuple[str, dict]]) -> None:
        if char == "w":
            following = MODES[(MODES.index(self._snapshot["mode"]) + 1) % len(MODES)]
            await self._act("mode", {"mode": following, "minutes": 0})
            return
        if char == "c" and self._snapshot["missed"]:
            await self._act("clear", {})
            return
        if not 0 <= self._selected < len(rows):
            return
        kind, row = rows[self._selected]
        for key, action, _hint in ROW_KEYS[kind]:
            if char != key:
                continue
            if action == "knock_back":
                self._prompt = (row, "")
            elif kind in ("ringing", "missed"):
                await self._act(action, {"id": row["id"]})
            else:
                await self._act(action, {"route": row["route"]})
            return

    async def _prompt_input(self, key_press: KeyPress) -> bool:
        row, text = self._prompt
        if key_press.name == "Escape":
            self._prompt = None
        elif key_press.name == "Enter":
            if text.strip():
                self._prompt = None
                await self._act("knock_back", {"id": row["id"], "text": text.strip()})
        elif key_press.name in ("Backspace", "Delete"):
            self._prompt = (row, text[:-1])
        elif key_press.char and all(c.isprintable() for c in key_press.char):
            grown = text + key_press.char
            if len(grown.encode("utf-8")) <= _MAX_TEXT_BYTES:
                self._prompt = (row, grown)
        self.request_render()
        return False

    async def _act(self, action: str, args: dict) -> None:
        if self._on_act is None:
            self._message = "knocks are not connected here"
            return
        self._armed = False
        try:
            line = self._on_act(action, args)
            if inspect.isawaitable(line):
                line = await line
        except asyncio.CancelledError:
            raise
        except Exception:
            line = "connect: that did not work; try again"
        self._message = line if isinstance(line, str) else ""
        await self._refresh()

    async def on_suspend(self) -> None:
        await self._stop()
        invalidate = getattr(self._renderer, "invalidate_render_cache", None)
        if callable(invalidate):
            invalidate()
        await super().on_suspend()

    async def on_complete(self) -> None:
        await self._stop()
        await super().on_complete()

    async def _stop(self) -> None:
        tasks = self.background_tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._snapshot, self._prompt = None, None
