"""Human review view for knock (sealed introduction) contact requests.

The send side (`/connect knock <route> "text"`) is a plain command, not a
form: a route is public and copied, never typed, so it needs no private
entry screen. Only the review side stays here.
"""

from __future__ import annotations

import asyncio
import inspect
import textwrap
import unicodedata
from typing import Any, Awaitable, Callable

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress
from plugins.hub.contact_requests import PendingContactRequest
from plugins.hub.device_names import (
    clip_display,
    device_key_fingerprint,
    display_name,
    request_row,
    short_fingerprint,
)

_MAX_DOMAIN = 253

ContactLoadCallback = Callable[
    [], list[PendingContactRequest] | Awaitable[list[PendingContactRequest]]
]
ContactDecisionCallback = Callable[[PendingContactRequest, str], Any | Awaitable[Any]]


def _metadata(plugin_type: str, description: str) -> AltViewMetadata:
    return AltViewMetadata(
        plugin_type=plugin_type,
        description=description,
        version="1.0.0",
        author="Kollabor",
        category="internal",
        icon="[LINK]",
        aliases=[],
        supports_named_sessions=False,
        supports_background=False,
    )


def _safe_display_text(value: str) -> str:
    return "".join(
        char
        for char in value
        if char == "\n"
        or (char.isprintable() and not unicodedata.category(char).startswith("C"))
    )


def _filter(value: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError("domain must be text")
    return "".join(
        char
        for char in value
        if char.isprintable() and not unicodedata.category(char).startswith("C")
    )[:limit]


class ContactReviewAltView(AltView):
    """List received knocks; accept or reject the selected one.

    Every row is named and fingerprinted, never keyed: `N. <device-name>
    fingerprint <4 hex>…<4 hex>   "<introduction>"   [a]ccept [r]eject`, per
    the constitution's Story 5. Up/down move the selection (like the Connect
    screen) and `[a]`/`[r]` act on that row only; the hint is printed on it.
    """

    def __init__(
        self,
        domain: str = "kollabor.ai",
        on_load: ContactLoadCallback | None = None,
        on_decide: ContactDecisionCallback | None = None,
    ) -> None:
        # Defaults let the AltView command integrator build a bare instance to
        # read its metadata; without callbacks the view is an empty inbox.
        super().__init__(_metadata("contact-review", "Review knocks locally"))
        self.domain = _filter(domain, _MAX_DOMAIN)
        self._on_load = on_load
        self._on_decide = on_decide
        self._renderer: Any = None
        self._requests: list[PendingContactRequest] = []
        self._stage = "loading"
        self._message = ""
        self._selected = 0
        # Cleared by a decision, set again once the redraw shows the next
        # row, so a held or double-tapped key cannot decide a knock unseen.
        self._armed = True

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._requests.clear()
        self._stage = "loading"
        self._message = ""
        self._selected = 0
        self._armed = True
        try:
            result = self._on_load() if self._on_load is not None else []
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, list) or any(
                not isinstance(item, PendingContactRequest) for item in result
            ):
                raise ValueError("invalid contact inbox")
            self._requests = result[:32]
            self._stage = "review"
            self._message = "" if self._requests else "no pending knocks"
        except asyncio.CancelledError:
            self._clear_requests()
            raise
        except Exception:
            self._stage = "error"
            self._message = "knock inbox is unavailable"

    async def on_complete(self) -> None:
        self._clear_requests()
        await super().on_complete()

    async def render_frame(self, delta_time: float) -> bool:
        if self._renderer is None:
            return False
        width, height = self._renderer.get_terminal_size()
        if width <= 0 or height <= 0:
            return True
        self._renderer.clear_screen()
        top = max(0, (height - min(height, 18)) // 2)
        self._renderer.write_at(
            0, top, solid_fg(str(C["half_bottom"]) * width, T().dark[1]), ""
        )
        self._renderer.write_at(
            0,
            top + 1,
            solid(" Knocks".ljust(width), T().dark[1], T().text, width),
            "",
        )
        if self._stage == "loading":
            self._write(2, top + 4, "loading knocks…", width)
        elif self._stage == "error" or not self._requests:
            self._write_wrapped(top + 4, self._message, width)
            self._write(2, top + 8, "enter/esc close", width)
        else:
            room = max(1, height - (top + 10))
            many = len(self._requests) > 1
            blocks = [
                self._row(request, index + 1, width - 2, index == self._selected, many)
                for index, request in enumerate(self._requests)
            ]
            first = 0
            while first < self._selected and (
                sum(map(len, blocks[first : self._selected + 1])) > room
            ):
                first += 1
            y = top + 3
            for block in blocks[first:]:
                if y - (top + 3) + len(block) > room:
                    break
                for line in block:
                    self._write(2, y, line, width)
                    y += 1
            self._write_wrapped(y + 1, self._message, width)
            self._write(2, height - 3, self._footer(many), width)
        self._armed = True
        return True

    @staticmethod
    def _footer(many: bool) -> str:
        return ("up/down select  " if many else "") + "a accept  r reject  esc close"

    @staticmethod
    def _row(
        request: PendingContactRequest,
        number: int,
        width: int,
        current: bool = True,
        many: bool = False,
    ) -> list[str]:
        """One knock as one or two lines, each at most `width` columns."""
        fingerprint = short_fingerprint(device_key_fingerprint(request.sender_key))
        lead = f"{('> ' if current else '  ') if many else ''}{number}. "
        return request_row(
            lead + display_name(request.device_name),
            f"fingerprint {fingerprint}",
            width,
            hint="[a]ccept [r]eject" if current else "",
            quote=_safe_display_text(request.introduction.reveal()).replace("\n", " "),
            indent=" " * len(lead),
            gap="  ",
        )

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "Escape":
            return True
        if self._stage != "review" or not self._requests:
            return key_press.name == "Enter"
        if key_press.name == "ArrowUp":
            self._selected = max(0, self._selected - 1)
            self.request_render()
        elif key_press.name == "ArrowDown":
            self._selected = min(len(self._requests) - 1, self._selected + 1)
            self.request_render()
        elif (
            self._armed
            and key_press.char
            and key_press.char.lower() in {"a", "r"}
        ):
            await self._decide("accept" if key_press.char.lower() == "a" else "reject")
        return False

    async def _decide(self, decision: str) -> None:
        request = self._requests[self._selected]
        verb = "accept" if decision == "accept" else "reject"
        who = display_name(request.device_name)
        if self._on_decide is None:
            self._message = f"could not {verb} {who}: knock review is not connected"
            self.request_render()
            return
        try:
            reason = self._on_decide(request, decision)
            if inspect.isawaitable(reason):
                reason = await reason
        except asyncio.CancelledError:
            raise
        except Exception:
            reason = "try again"
        if isinstance(reason, str) and reason:
            self._message = f"could not {verb} {who}: {reason}"
            self.request_render()
            return
        request.introduction.clear()
        self._requests.pop(self._selected)
        self._selected = max(0, min(self._selected, len(self._requests) - 1))
        self._armed = False
        self._message = (
            f"accepted {who}. it is a peer with agents trust; "
            f"nothing is allowed until /connect allow {who} <agent>."
            if decision == "accept"
            else f"rejected {who}."
        )
        self.request_render()

    def _clear_requests(self) -> None:
        for request in self._requests:
            request.introduction.clear()
        self._requests.clear()

    def _write_wrapped(self, y: int, text: str, width: int) -> None:
        lines = textwrap.wrap(text, max(1, width - 2), break_on_hyphens=False)
        if len(lines) > 3:  # a whole device name can push it past three lines
            lines[2] += "…"
        for offset, line in enumerate(lines[:3]):
            self._write(2, y + offset, line, width)

    def _write(self, x: int, y: int, text: str, width: int) -> None:
        if self._renderer is not None and x < width:
            self._renderer.write_at(x, y, clip_display(text, width - x), "")
