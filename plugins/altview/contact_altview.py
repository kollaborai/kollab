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
from plugins.hub.device_names import device_key_fingerprint, short_fingerprint

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
        if char in "\n\t"
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
    """List received knocks; accept or reject the oldest one at a time.

    Every row is named and fingerprinted, never keyed: `N. <device-name>
    fingerprint <4 hex>…<4 hex>   "<introduction>"   [a]ccept [r]eject`, per
    the constitution's Story 5. `[a]`/`[r]` always act on the first (oldest) row;
    reviewing a specific later row individually is a follow-up, not built
    here.
    """

    def __init__(
        self,
        domain: str,
        on_load: ContactLoadCallback,
        on_decide: ContactDecisionCallback,
    ) -> None:
        super().__init__(_metadata("contact-review", "Review knocks locally"))
        self.domain = _filter(domain, _MAX_DOMAIN)
        self._on_load = on_load
        self._on_decide = on_decide
        self._renderer: Any = None
        self._requests: list[PendingContactRequest] = []
        self._stage = "loading"
        self._message = ""

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._requests.clear()
        self._stage = "loading"
        self._message = ""
        try:
            result = self._on_load()
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, list) or any(
                not isinstance(item, PendingContactRequest) for item in result
            ):
                raise ValueError("invalid contact inbox")
            self._requests = result[:32]
            self._stage = "review"
            self._message = "" if self._requests else "No pending knocks."
        except asyncio.CancelledError:
            self._clear_requests()
            raise
        except Exception:
            self._stage = "error"
            self._message = "Knock inbox is unavailable."

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
            solid(" Knocks ".ljust(width), T().dark[1], T().text, width),
            "",
        )
        if self._stage == "loading":
            self._write(2, top + 4, "Loading knocks…", width)
        elif self._stage == "error" or not self._requests:
            self._write_wrapped(top + 4, self._message, width)
            self._write(2, top + 8, "Enter or Esc: close", width)
        else:
            visible = max(1, height - (top + 10))
            shown = self._requests[:visible]
            for offset, request in enumerate(shown):
                self._write(
                    2, top + 3 + offset, self._row(request, offset + 1, width - 2), width
                )
            self._write_wrapped(top + 4 + len(shown), self._message, width)
            self._write(
                2,
                height - 4,
                "[a] accept the first knock   [r] reject the first knock",
                width,
            )
            self._write(2, height - 3, "Esc: close", width)
        return True

    @staticmethod
    def _row(request: PendingContactRequest, number: int, width: int) -> str:
        fingerprint = short_fingerprint(device_key_fingerprint(request.sender_key))
        prefix = f'{number}. {request.device_name}  fingerprint {fingerprint}   "'
        suffix = '"   [a]ccept [r]eject'
        budget = max(0, width - len(prefix) - len(suffix))
        introduction = _safe_display_text(request.introduction.reveal()).replace(
            "\n", " "
        )
        if len(introduction) > budget:
            introduction = introduction[: max(0, budget - 1)] + "…"
        return prefix + introduction + suffix

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "Escape":
            return True
        if self._stage != "review" or not self._requests:
            return key_press.name == "Enter"
        if key_press.char and key_press.char.lower() in {"a", "r"}:
            await self._decide("accept" if key_press.char.lower() == "a" else "reject")
        return False

    async def _decide(self, decision: str) -> None:
        request = self._requests[0]
        verb = "accept" if decision == "accept" else "reject"
        try:
            reason = self._on_decide(request, decision)
            if inspect.isawaitable(reason):
                reason = await reason
        except asyncio.CancelledError:
            raise
        except Exception:
            reason = "try again"
        if isinstance(reason, str) and reason:
            self._message = f"could not {verb} {request.device_name}: {reason}"
            self.request_render()
            return
        request.introduction.clear()
        self._requests.pop(0)
        self._message = (
            f"accepted {request.device_name}. it is a peer with agents trust; "
            f"nothing is allowed until /connect allow {request.device_name} <agent>."
            if decision == "accept"
            else f"rejected {request.device_name}."
        )
        self.request_render()

    def _clear_requests(self) -> None:
        for request in self._requests:
            request.introduction.clear()
        self._requests.clear()

    def _write_wrapped(self, y: int, text: str, width: int) -> None:
        for offset, line in enumerate(textwrap.wrap(text, max(1, width - 2), break_on_hyphens=False)[:3]):
            self._write(2, y + offset, line, width)

    def _write(self, x: int, y: int, text: str, width: int) -> None:
        if self._renderer is not None and x < width:
            self._renderer.write_at(x, y, text[: max(0, width - x)], "")
