"""Private code-entry view for device enrollment.

This view deliberately has no history, event, logging, telemetry, clipboard,
or persistence integration. The caller owns enrollment authority and receives
the code only through the short-lived ``ConnectSubmission`` callback value.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import textwrap
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

_MAX_DOMAIN_LENGTH = 253
_MAX_CODE_LENGTH = 4096
_RECEIPT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_REDACTED = "<redacted>"


class ConnectStatus(Enum):
    """The small, non-secret result vocabulary for a connect submission."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ERROR = "error"


class PrivateCode:
    """Short-lived, explicitly revealable code wrapper with a safe repr."""

    __slots__ = ("_value", "_cleared")

    def __init__(self, value: str | bytes | bytearray) -> None:
        if isinstance(value, str):
            raw = value.encode("utf-8")
        elif isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
        else:
            raise TypeError("private code must be text or bytes")
        if not raw:
            raise ValueError("private code must not be empty")
        self._value = bytearray(raw)
        self._cleared = False

    @classmethod
    def _from_chars(cls, chars: list[str]) -> PrivateCode:
        """Build from the input buffer without joining it into a string."""
        instance = cls.__new__(cls)
        instance._value = bytearray()
        instance._cleared = False
        for char in chars:
            instance._value.extend(char.encode("utf-8"))
        if not instance._value:
            raise ValueError("private code must not be empty")
        return instance

    def reveal(self) -> str:
        """Return the code for the immediate transport call only."""
        if self._cleared:
            raise RuntimeError("private code has been cleared")
        return self._value.decode("utf-8")

    def clear(self) -> None:
        """Best-effort overwrite of the wrapper's mutable byte buffer."""
        for index in range(len(self._value)):
            self._value[index] = 0
        self._value.clear()
        self._cleared = True

    def __repr__(self) -> str:
        return f"PrivateCode({_REDACTED})"

    def __str__(self) -> str:
        return _REDACTED


@dataclass(frozen=True, slots=True, repr=False)
class ConnectSubmission:
    """Values passed to the local enrollment callback; never a command string."""

    domain: str
    code: PrivateCode

    def __post_init__(self) -> None:
        if not isinstance(self.domain, str):
            raise TypeError("domain must be text")
        if len(self.domain) > _MAX_DOMAIN_LENGTH or any(
            not character.isprintable()
            or unicodedata.category(character).startswith("C")
            for character in self.domain
        ):
            raise ValueError("domain contains invalid display text")
        if not isinstance(self.code, PrivateCode):
            raise TypeError("code must be a PrivateCode")

    def __repr__(self) -> str:
        return f"ConnectSubmission(domain={self.domain!r}, code={_REDACTED})"


@dataclass(frozen=True, slots=True)
class ConnectOutcome:
    """A bounded result; pending requests expose only their receipt ID."""

    status: ConnectStatus
    receipt_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ConnectStatus):
            raise TypeError("status must be a ConnectStatus")
        if self.status is ConnectStatus.PENDING:
            if not isinstance(self.receipt_id, str) or not _RECEIPT_ID_RE.fullmatch(
                self.receipt_id
            ):
                raise ValueError("pending outcome requires a safe receipt ID")
        elif self.receipt_id is not None:
            raise ValueError("only pending outcomes may include a receipt ID")

    @classmethod
    def pending(cls, receipt_id: str) -> ConnectOutcome:
        return cls(ConnectStatus.PENDING, receipt_id)

    @classmethod
    def approved(cls) -> ConnectOutcome:
        return cls(ConnectStatus.APPROVED)

    @classmethod
    def rejected(cls) -> ConnectOutcome:
        return cls(ConnectStatus.REJECTED)

    @classmethod
    def error(cls) -> ConnectOutcome:
        return cls(ConnectStatus.ERROR)


ConnectSubmitCallback = Callable[
    [ConnectSubmission], ConnectOutcome | Awaitable[ConnectOutcome]
]
EnrollmentOfferCallback = Callable[[str], dict[str, str] | Awaitable[dict[str, str]]]


class ConnectAltView(AltView):
    """Private local form for submitting a device enrollment code.

    The code is held in a private in-memory character list while editing and is
    rendered as a fixed mask. On submission the view clears that list, passes a
    ``PrivateCode`` wrapper to the callback, and clears the wrapper afterwards.
    ``outcome`` is ``None`` on cancellation and otherwise contains only one of
    the typed, bounded ``ConnectOutcome`` values.
    """

    def __init__(
        self,
        domain: str = "kollabor.ai",
        on_submit: ConnectSubmitCallback | None = None,
    ) -> None:
        metadata = AltViewMetadata(
            plugin_type="connect",
            description="Privately enter a device enrollment code",
            version="1.0.0",
            author="Kollabor",
            category="internal",
            icon="[LINK]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)
        self._domain = self._filter_text(domain, _MAX_DOMAIN_LENGTH)
        self._on_submit = on_submit
        self._renderer: Any = None
        self._code_chars: list[str] = []
        self._domain_cursor = len(self.domain)
        self._code_cursor = 0
        self._focus = "domain"
        self._stage = "entry"
        self._validation_error = ""
        self._outcome: ConnectOutcome | None = None
        self.cancelled = False
        self._paste_active = False

    @property
    def outcome(self) -> ConnectOutcome | None:
        """The submitted status, or ``None`` when the user cancelled."""
        return self._outcome

    @property
    def domain(self) -> str:
        """The visible discovery domain (not an enrollment credential)."""
        return self._domain

    @domain.setter
    def domain(self, value: str) -> None:
        self._domain = self._filter_text(value, _MAX_DOMAIN_LENGTH)

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._clear_code_input()
        self._outcome = None
        self.cancelled = False
        self._validation_error = ""
        self._focus = "domain"
        self._stage = "entry"
        self._paste_active = False

    async def render_frame(self, delta_time: float) -> bool:
        renderer = self._renderer
        if renderer is None:
            return False
        width, height = renderer.get_terminal_size()
        if width <= 0 or height <= 0:
            return True

        renderer.clear_screen()
        theme = T()
        top = max(0, (height - 10) // 2)
        renderer.write_at(
            0,
            top,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        renderer.write_at(
            0,
            top + 1,
            solid(" Connect device ".ljust(width), theme.dark[1], theme.text, width),
            "",
        )

        if self._stage == "entry":
            self._render_entry(top + 3, width)
        elif self._stage == "submitting":
            self._write_line(2, top + 4, "Submitting connect request…", width)
        else:
            self._render_outcome(top + 4, width)
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "BracketedPasteStart":
            self._paste_active = True
            return False
        if key_press.name == "BracketedPasteEnd":
            self._paste_active = False
            self.request_render()
            return False

        if self._stage == "entry":
            if key_press.name == "Escape":
                self._paste_active = False
                self._clear_code_input()
                self.cancelled = True
                self._stage = "cancelled"
                return True
            if self._paste_active:
                pasted_character = key_press.char
                if (
                    not pasted_character
                    or not pasted_character.isprintable()
                    or key_press.ctrl
                    or (key_press.modifiers or {}).get("alt", False)
                ):
                    return False
            if key_press.name == "Tab":
                self._focus = "code" if self._focus == "domain" else "domain"
                self._validation_error = ""
                self.request_render()
                return False
            if key_press.name == "Enter":
                await self._submit()
                return False
            self._edit_focused_field(key_press)
            return False

        if key_press.name in ("Escape", "Enter"):
            return True
        return False

    async def on_suspend(self) -> None:
        """Discard private entry state whenever this view leaves the stack."""
        self._paste_active = False
        self._clear_code_input()
        await super().on_suspend()

    async def on_complete(self) -> None:
        self._paste_active = False
        self._clear_code_input()
        await super().on_complete()

    def _render_entry(self, y: int, width: int) -> None:
        domain_marker = ">" if self._focus == "domain" else " "
        code_marker = ">" if self._focus == "code" else " "
        self._write_line(
            2, y, f"{domain_marker} Domain: {self._visible_domain()}", width
        )
        code_display = "********" if self._code_chars else "(enter privately)"
        self._write_line(2, y + 1, f"{code_marker} Private code: {code_display}", width)
        if self._validation_error:
            self._write_line(2, y + 3, self._validation_error, width)
        self._write_line(2, y + 5, "Tab: switch   Enter: submit   Esc: cancel", width)
        self._write_line(
            2, y + 7, "No code? Run /connect offer on a device that is already connected.", width
        )

    def _render_outcome(self, y: int, width: int) -> None:
        outcome = self._outcome
        if outcome is None:
            message = "Connect request cancelled."
        elif outcome.status is ConnectStatus.PENDING:
            self._write_line(2, y, "Request pending.", width)
            self._write_line(2, y + 1, f"Receipt: {outcome.receipt_id}", width)
            message = ""
        elif outcome.status is ConnectStatus.APPROVED:
            message = "Connection approved."
        elif outcome.status is ConnectStatus.REJECTED:
            message = "Connection request rejected."
        else:
            message = "Could not submit the connect request."

        if message:
            self._write_line(2, y, message, width)
        self._write_line(2, y + 3, "Enter or Esc: close", width)

    def _edit_focused_field(self, key_press: KeyPress) -> None:
        field = self._domain_chars() if self._focus == "domain" else self._code_chars
        cursor_attr = "_domain_cursor" if self._focus == "domain" else "_code_cursor"
        cursor = getattr(self, cursor_attr)
        max_length = _MAX_DOMAIN_LENGTH if self._focus == "domain" else _MAX_CODE_LENGTH

        if key_press.name in ("Backspace", "Delete"):
            if key_press.name == "Backspace" and cursor > 0:
                del field[cursor - 1]
                cursor -= 1
            elif key_press.name == "Delete" and cursor < len(field):
                del field[cursor]
        elif key_press.name in ("ArrowLeft", "Left"):
            cursor = max(0, cursor - 1)
        elif key_press.name in ("ArrowRight", "Right"):
            cursor = min(len(field), cursor + 1)
        elif key_press.name == "Home" or key_press.name == "Ctrl+A":
            cursor = 0
        elif key_press.name == "End" or key_press.name == "Ctrl+E":
            cursor = len(field)
        elif key_press.name in ("Ctrl+U", "Ctrl+K"):
            if key_press.name == "Ctrl+U":
                del field[:cursor]
                cursor = 0
            else:
                del field[cursor:]
        elif (
            key_press.char
            and key_press.char.isprintable()
            and not any(
                unicodedata.category(character).startswith("C")
                for character in key_press.char
            )
            and not key_press.ctrl
            and len(field) < max_length
        ):
            field.insert(cursor, key_press.char)
            cursor += 1

        if self._focus == "domain":
            text = "".join(field)
            marker = text.find("K1-")
            if marker >= 0:
                # A code typed or pasted into the domain field moves to the
                # private field so it is never rendered in clear text.
                self._clear_code_input()
                self._code_chars.extend(list(text[marker:])[:_MAX_CODE_LENGTH])
                self._code_cursor = len(self._code_chars)
                for index in range(marker, len(field)):
                    field[index] = "\0"
                text = text[:marker]
                cursor = min(cursor, len(text))
                self._focus = "code"
            self.domain = text
        setattr(self, cursor_attr, cursor)
        self._validation_error = ""
        self.request_render()

    async def _submit(self) -> None:
        if not self.domain.strip() or not self._code_chars:
            self._validation_error = "Enter a domain and private code."
            self.request_render()
            return

        secret = PrivateCode._from_chars(self._code_chars)
        self._clear_code_input()
        submission = ConnectSubmission(self.domain, secret)
        self._stage = "submitting"
        self._validation_error = ""
        self.request_render()

        try:
            if self._on_submit is None:
                result: Any = ConnectOutcome.error()
            else:
                result = self._on_submit(submission)
                if inspect.isawaitable(result):
                    result = await result
            self._outcome = (
                result if type(result) is ConnectOutcome else ConnectOutcome.error()
            )
        except asyncio.CancelledError:
            self._outcome = ConnectOutcome.error()
            self._stage = "outcome"
            raise
        except Exception:
            # Do not log or display callback exceptions; they may contain input.
            self._outcome = ConnectOutcome.error()
        finally:
            secret.clear()

        self._stage = "outcome"
        self.request_render()

    def _domain_chars(self) -> list[str]:
        return list(self.domain)

    def _visible_domain(self) -> str:
        if not self.domain:
            return "(enter domain)"
        return self.domain

    def _clear_code_input(self) -> None:
        for index in range(len(self._code_chars)):
            self._code_chars[index] = "\0"
        self._code_chars.clear()
        self._code_cursor = 0

    def _write_line(self, x: int, y: int, text: str, width: int) -> None:
        if self._renderer is not None and x < width:
            self._renderer.write_at(x, y, text[: max(0, width - x)], "")

    @staticmethod
    def _filter_text(value: str, limit: int) -> str:
        if not isinstance(value, str):
            raise TypeError("domain must be text")
        return "".join(
            character
            for character in value
            if character.isprintable()
            and not unicodedata.category(character).startswith("C")
        )[:limit]


class ConnectOfferAltView(AltView):
    """Privately display a one-time device code after an explicit human action."""

    def __init__(
        self,
        domain: str = "kollabor.ai",
        on_create: EnrollmentOfferCallback | None = None,
    ) -> None:
        metadata = AltViewMetadata(
            plugin_type="connect-offer",
            description="Create a private one-time device enrollment code",
            version="1.0.0",
            author="Kollabor",
            category="internal",
            icon="[LINK]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)
        self._domain = ConnectAltView._filter_text(domain, _MAX_DOMAIN_LENGTH)
        self._on_create = on_create
        self._renderer: Any = None
        self._stage = "confirm"
        self._private_code: PrivateCode | None = None
        self._offer_id = ""
        self._expires_at = 0
        self.cancelled = False

    @property
    def domain(self) -> str:
        return self._domain

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._stage = "confirm"
        self._clear_code()
        self.cancelled = False

    async def render_frame(self, delta_time: float) -> bool:
        if self._renderer is None:
            return False
        width, height = self._renderer.get_terminal_size()
        if width <= 0 or height <= 0:
            return True
        self._renderer.clear_screen()
        theme = T()
        top = max(0, (height - 10) // 2)
        self._renderer.write_at(
            0,
            top,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        self._renderer.write_at(
            0,
            top + 1,
            solid(
                " Create device code ".ljust(width), theme.dark[1], theme.text, width
            ),
            "",
        )
        if self._stage == "confirm":
            self._write_line(2, top + 3, f"Network: {self._domain}", width)
            self._write_line(
                2, top + 5, "This authorizes one new device for five minutes.", width
            )
            self._write_line(
                2, top + 6, "May include one provider profile credential.", width
            )
            self._write_line(
                2,
                top + 7,
                "Review its exact scope before accepting.",
                width,
            )
            self._write_line(
                2,
                top + 8,
                "Network removal does not revoke copied credentials.",
                width,
            )
            self._write_line(2, top + 10, "Enter: create code   Esc: cancel", width)
        elif self._stage == "creating":
            self._write_line(2, top + 4, "Creating one-time code…", width)
        elif self._stage == "code":
            self._render_code(top + 3, width)
        else:
            self._write_line(2, top + 4, "Could not create a device code.", width)
            self._write_line(
                2, top + 5, f"Check that /connect status shows {self._domain} online, then retry.", width
            )
            self._write_line(2, top + 7, "Enter or Esc: close", width)
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if self._stage == "confirm":
            if key_press.name == "Escape":
                self.cancelled = True
                self._stage = "cancelled"
                return True
            if key_press.name == "Enter":
                await self._create()
                return False
            return False
        if self._stage == "creating":
            return False
        if key_press.name in ("Escape", "Enter"):
            return True
        return False

    async def on_suspend(self) -> None:
        """Discard the displayed offer code on every AltView exit."""
        self._clear_code()
        invalidate_render_cache = getattr(
            self._renderer, "invalidate_render_cache", None
        )
        if callable(invalidate_render_cache):
            invalidate_render_cache()
        await super().on_suspend()

    async def on_complete(self) -> None:
        self._clear_code()
        await super().on_complete()

    async def _create(self) -> None:
        self._stage = "creating"
        self.request_render()
        try:
            if self._on_create is None:
                raise ValueError("enrollment offer handler unavailable")
            result = self._on_create(self._domain)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict) or set(result) != {
                "status",
                "offer_id",
                "expires_at",
                "code",
            }:
                raise ValueError("invalid enrollment offer")
            offer_id = result["offer_id"]
            code = result["code"]
            expires_at = result["expires_at"]
            if (
                result["status"] != "offered"
                or not isinstance(offer_id, str)
                or not re.fullmatch(r"[0-9a-f]{32}", offer_id)
                or not isinstance(expires_at, str)
                or not expires_at.isdigit()
                or int(expires_at) <= int(time.time())
                or not isinstance(code, str)
                or not re.fullmatch(
                    rf"K1-{offer_id}-[0-9A-HJKMNP-TV-Z]{{4}}(?:-[0-9A-HJKMNP-TV-Z]{{4}}){{4}}",
                    code,
                )
            ):
                raise ValueError("invalid enrollment offer")
            self._private_code = PrivateCode(code)
            self._offer_id = offer_id
            self._expires_at = int(expires_at)
            self._stage = "code"
        except asyncio.CancelledError:
            self._clear_code()
            self._stage = "error"
            raise
        except Exception:
            self._clear_code()
            self._stage = "error"
        self.request_render()

    def _render_code(self, y: int, width: int) -> None:
        self._write_line(2, y, f"Offer: {self._offer_id}", width)
        if self._private_code is None:
            self._write_line(2, y + 1, "This code is no longer available.", width)
            self._write_line(2, y + 3, "Enter or Esc: close", width)
            return
        remaining = self._expires_at - int(time.time())
        if remaining <= 0:
            self._clear_code()
            self._write_line(2, y + 1, "This code has expired.", width)
            self._write_line(2, y + 3, "Enter or Esc: close", width)
            return
        self._write_line(
            2, y + 1, "Private code (send it only to the new device):", width
        )
        try:
            full_code = self._private_code.reveal() if self._private_code else ""
        except RuntimeError:
            full_code = ""
        code_lines = textwrap.wrap(
            full_code,
            width=max(1, width - 4),
            break_long_words=True,
            break_on_hyphens=False,
        )
        code_y = y + 3
        for index, line in enumerate(code_lines):
            self._write_line(2, code_y + index, line, width)
        expiry_y = code_y + len(code_lines) + 1
        self._write_line(2, expiry_y, f"Expires in {remaining} seconds.", width)
        self._write_line(2, expiry_y + 2, "Enter or Esc: close", width)

    def _clear_code(self) -> None:
        if self._private_code is not None:
            self._private_code.clear()
            self._private_code = None

    def _write_line(self, x: int, y: int, text: str, width: int) -> None:
        if self._renderer is not None and x < width:
            self._renderer.write_at(x, y, text[: max(0, width - x)], "")
