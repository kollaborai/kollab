"""Private entry and human review views for unknown-agent contact requests."""

from __future__ import annotations

import asyncio
import inspect
import re
import textwrap
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress
from plugins.hub.contact_requests import PendingContactRequest, PrivateMessage

_HEX_KEY = re.compile(r"[0-9a-fA-F]{64}\Z")
_MAX_DOMAIN = 253
_MAX_INTRO_CHARS = 2048
_RECEIPT = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True, slots=True, repr=False)
class ContactSubmission:
    domain: str
    recipient_key: str
    introduction: PrivateMessage = field(repr=False)

    def __repr__(self) -> str:
        return (
            "ContactSubmission("
            f"domain={self.domain!r}, recipient_key=<public-key>, "
            "introduction=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class ContactSubmissionOutcome:
    receipt_id: str | None = None

    def __post_init__(self) -> None:
        if self.receipt_id is not None and not _RECEIPT.fullmatch(self.receipt_id):
            raise ValueError("invalid contact receipt")


ContactSubmitCallback = Callable[
    [ContactSubmission], ContactSubmissionOutcome | Awaitable[ContactSubmissionOutcome]
]
ContactLoadCallback = Callable[
    [], list[PendingContactRequest] | Awaitable[list[PendingContactRequest]]
]
ContactDecisionCallback = Callable[[str, str], Any | Awaitable[Any]]


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


class ContactRequestAltView(AltView):
    """Collect a selected relay route, recipient key and private introduction."""

    def __init__(
        self,
        domain: str = "",
        on_submit: ContactSubmitCallback | None = None,
    ) -> None:
        super().__init__(
            _metadata("contact-request", "Privately request contact with an agent")
        )
        self._domain_chars = list(self._filter(domain, _MAX_DOMAIN))
        self._key_chars: list[str] = []
        self._intro_chars: list[str] = []
        self._cursor = {"domain": len(self._domain_chars), "key": 0, "intro": 0}
        self._focus = "domain"
        self._on_submit = on_submit
        self._renderer: Any = None
        self._stage = "entry"
        self._receipt_id: str | None = None
        self._error = ""
        self.cancelled = False

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._clear_intro()
        self._stage = "entry"
        self._receipt_id = None
        self._error = ""
        self.cancelled = False

    async def on_complete(self) -> None:
        self._clear_intro()
        await super().on_complete()

    async def render_frame(self, delta_time: float) -> bool:
        if self._renderer is None:
            return False
        width, height = self._renderer.get_terminal_size()
        if width <= 0 or height <= 0:
            return True
        self._renderer.clear_screen()
        top = max(0, (height - 14) // 2)
        self._renderer.write_at(
            0, top, solid_fg(str(C["half_bottom"]) * width, T().dark[1]), ""
        )
        self._renderer.write_at(
            0,
            top + 1,
            solid(" Request agent contact ".ljust(width), T().dark[1], T().text, width),
            "",
        )
        if self._stage == "entry":
            self._render_entry(top + 3, width)
        elif self._stage == "submitting":
            self._write(2, top + 5, "Submitting encrypted contact request…", width)
        else:
            message = (
                f"Request queued. Receipt: {self._receipt_id}"
                if self._receipt_id
                else "Could not submit the contact request."
            )
            self._write(2, top + 5, message, width)
            self._write(2, top + 8, "Enter or Esc: close", width)
        return True

    def _render_entry(self, y: int, width: int) -> None:
        domain = "".join(self._domain_chars) or "(enter relay domain)"
        recipient = "".join(self._key_chars) or "(enter full 64-hex recipient key)"
        intro = "".join(self._intro_chars)
        preview = _safe_display_text(intro).replace("\n", " ⏎ ")
        if not preview:
            preview = "(enter a short introduction privately)"
        elif len(preview) > max(20, width - 24):
            preview = preview[: max(0, width - 27)] + "…"
        self._write(
            2,
            y,
            f"{'>' if self._focus == 'domain' else ' '} Relay domain: {domain}",
            width,
        )
        self._write(
            2,
            y + 1,
            f"{'>' if self._focus == 'key' else ' '} Recipient key: {recipient}",
            width,
        )
        self._write(
            2,
            y + 2,
            f"{'>' if self._focus == 'intro' else ' '} Private introduction: {preview}",
            width,
        )
        self._write(2, y + 4, "Destination identity is ed25519:<recipient key>.", width)
        self._write(2, y + 5, "The relay stores ciphertext for up to 24 hours.", width)
        self._write(
            2, y + 6, "This is only a contact request, never a task or grant.", width
        )
        if self._error:
            self._write(2, y + 8, self._error, width)
        self._write(2, y + 10, "Tab: next field   Enter: submit   Esc: cancel", width)

    async def handle_input(self, key_press: KeyPress) -> bool:
        if self._stage == "entry":
            if key_press.name == "Escape":
                self.cancelled = True
                self._clear_intro()
                return True
            if key_press.name == "Tab":
                self._focus = {
                    "domain": "key",
                    "key": "intro",
                    "intro": "domain",
                }[self._focus]
                self.request_render()
                return False
            if key_press.name == "Enter":
                await self._submit()
                return False
            self._edit(key_press)
            return False
        return key_press.name in {"Escape", "Enter"}

    def _edit(self, key_press: KeyPress) -> None:
        field = {
            "domain": self._domain_chars,
            "key": self._key_chars,
            "intro": self._intro_chars,
        }[self._focus]
        cursor = self._cursor[self._focus]
        max_len = {
            "domain": _MAX_DOMAIN,
            "key": 64,
            "intro": _MAX_INTRO_CHARS,
        }[self._focus]
        if key_press.name == "Backspace" and cursor > 0:
            del field[cursor - 1]
            cursor -= 1
        elif key_press.name == "Delete" and cursor < len(field):
            del field[cursor]
        elif key_press.name in {"ArrowLeft", "Left"}:
            cursor = max(0, cursor - 1)
        elif key_press.name in {"ArrowRight", "Right"}:
            cursor = min(len(field), cursor + 1)
        elif key_press.name in {"Home", "Ctrl+A"}:
            cursor = 0
        elif key_press.name in {"End", "Ctrl+E"}:
            cursor = len(field)
        elif key_press.char and key_press.char.isprintable() and not key_press.ctrl:
            char = key_press.char
            if (
                len(field) < max_len
                and not unicodedata.category(char).startswith("C")
                and (self._focus != "intro" or char not in "\r\x1b")
            ):
                field.insert(cursor, char)
                cursor += 1
        self._cursor[self._focus] = cursor
        self._error = ""
        self.request_render()

    async def _submit(self) -> None:
        domain = "".join(self._domain_chars).strip()
        recipient_key = "".join(self._key_chars).lower()
        intro = "".join(self._intro_chars)
        if (
            not domain
            or len(domain) > _MAX_DOMAIN
            or not _HEX_KEY.fullmatch(recipient_key)
        ):
            self._error = "Enter a relay domain and full 64-hex recipient key."
            self.request_render()
            return
        if not intro.strip() or len(intro.encode("utf-8")) > 2048:
            self._error = "Enter an introduction under 2048 UTF-8 bytes."
            self.request_render()
            return
        private = PrivateMessage(intro)
        self._clear_intro()
        submission = ContactSubmission(domain, recipient_key, private)
        self._stage = "submitting"
        self.request_render()
        try:
            result = self._on_submit(submission) if self._on_submit else None
            if inspect.isawaitable(result):
                result = await result
            self._receipt_id = (
                result.receipt_id if type(result) is ContactSubmissionOutcome else None
            )
        except asyncio.CancelledError:
            self._stage = "outcome"
            raise
        except Exception:
            self._receipt_id = None
        finally:
            private.clear()
        self._stage = "outcome"
        self.request_render()

    def _clear_intro(self) -> None:
        for index in range(len(self._intro_chars)):
            self._intro_chars[index] = "\0"
        self._intro_chars.clear()
        self._cursor["intro"] = 0

    @staticmethod
    def _filter(value: str, limit: int) -> str:
        if not isinstance(value, str):
            raise TypeError("domain must be text")
        return "".join(
            char
            for char in value
            if char.isprintable() and not unicodedata.category(char).startswith("C")
        )[:limit]

    def _write(self, x: int, y: int, text: str, width: int) -> None:
        if self._renderer is not None and x < width:
            self._renderer.write_at(x, y, text[: max(0, width - x)], "")


class ContactReviewAltView(AltView):
    """Show one decrypted introduction at a time for explicit local review."""

    def __init__(
        self,
        domain: str,
        on_load: ContactLoadCallback,
        on_decide: ContactDecisionCallback,
    ) -> None:
        super().__init__(
            _metadata("contact-review", "Review unknown agent contact requests locally")
        )
        self.domain = ContactRequestAltView._filter(domain, _MAX_DOMAIN)
        self._on_load = on_load
        self._on_decide = on_decide
        self._renderer: Any = None
        self._requests: list[PendingContactRequest] = []
        self._stage = "loading"
        self._message = ""
        self._scroll = 0

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
            self._message = "" if self._requests else "No pending contact requests."
        except asyncio.CancelledError:
            self._clear_requests()
            raise
        except Exception:
            self._stage = "error"
            self._message = "Contact inbox is unavailable."

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
            solid(
                " Review contact requests ".ljust(width), T().dark[1], T().text, width
            ),
            "",
        )
        if self._stage == "loading":
            self._write(2, top + 4, "Loading encrypted contact inbox…", width)
        elif self._stage == "error" or not self._requests:
            self._write(2, top + 4, self._message, width)
            self._write(2, top + 7, "Enter or Esc: close", width)
        else:
            request = self._requests[0]
            self._write(2, top + 3, f"Receipt: {request.receipt_id}", width)
            self._write(
                2,
                top + 4,
                f"Sender identity: ed25519:{request.sender_key[:16]}…",
                width,
            )
            self._write(2, top + 5, f"Expires: {request.expires_at}", width)
            lines = textwrap.wrap(
                _safe_display_text(request.introduction.reveal()),
                width=max(1, width - 6),
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [""]
            visible = max(1, height - (top + 13))
            for offset, line in enumerate(lines[self._scroll : self._scroll + visible]):
                self._write(2, top + 7 + offset, line, width)
            self._write(
                2, height - 5, "A: accept this request   R: reject this request", width
            )
            self._write(2, height - 4, "↑/↓: scroll   Esc: close", width)
            self._write(
                2,
                height - 3,
                "No membership, workspace/tool grant or model run is created.",
                width,
            )
            if self._message:
                self._write(2, height - 2, self._message, width)
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "Escape":
            return True
        if self._stage != "review" or not self._requests:
            return key_press.name == "Enter"
        if key_press.name in {"ArrowDown", "Down"}:
            self._scroll += 1
            self.request_render()
        elif key_press.name in {"ArrowUp", "Up"}:
            self._scroll = max(0, self._scroll - 1)
            self.request_render()
        elif key_press.name == "PageDown":
            self._scroll += 8
            self.request_render()
        elif key_press.name == "PageUp":
            self._scroll = max(0, self._scroll - 8)
            self.request_render()
        elif key_press.char and key_press.char.lower() in {"a", "r"}:
            await self._decide("accept" if key_press.char.lower() == "a" else "reject")
        return False

    async def _decide(self, decision: str) -> None:
        request = self._requests[0]
        try:
            result = self._on_decide(request.receipt_id, decision)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            self._message = "Decision could not be recorded; request remains pending."
            self.request_render()
            return
        request.introduction.clear()
        self._requests.pop(0)
        self._scroll = 0
        self._message = f"Request {request.receipt_id} {decision}ed."
        if decision == "accept":
            self._message = f"Request {request.receipt_id} accepted."
        self.request_render()

    def _clear_requests(self) -> None:
        for request in self._requests:
            request.introduction.clear()
        self._requests.clear()

    def _write(self, x: int, y: int, text: str, width: int) -> None:
        if self._renderer is not None and x < width:
            self._renderer.write_at(x, y, text[: max(0, width - x)], "")
