"""Private views for device enrollment: the code-entry form and the Connect screen.

These views deliberately have no history, event, logging, telemetry,
clipboard, or persistence integration. The caller owns enrollment authority
and receives an entered code only through the short-lived
``ConnectSubmission`` callback value; a code we issue is shown only here.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import textwrap
import time
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Awaitable, Callable

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress
from plugins.hub.relay_commands import ConnectSnapshot, JoinRequestRow, network_label

_MAX_DOMAIN_LENGTH = 253
_MAX_CODE_LENGTH = 4096
_RECEIPT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_SHORT_CODE_START_RE = re.compile(
    r"[0-9A-HJKMNP-TV-Z]{4}-?[0-9A-HJKMNP-TV-Z]{4}", re.IGNORECASE
)
_REDACTED = "<redacted>"
_LABEL_WIDTH = 13
_POLL_SECONDS = 2.0
_SHORT_CODE_RE = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}")
_OFFER_ID_RE = re.compile(r"[0-9a-f]{32}")


class ConnectStatus(Enum):
    """The small, non-secret result vocabulary for a connect submission."""

    PENDING = "pending"
    APPROVED = "approved"
    CONNECTED = "connected"
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
    # One line of non-secret text shown in place of the generic one, e.g.
    # `joined marco-home as alzan-prod-home. trust: open`.
    detail: str = ""

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
        if self.detail and (
            self.status not in (ConnectStatus.APPROVED, ConnectStatus.CONNECTED)
            or not isinstance(self.detail, str)
            or len(self.detail) > 200
            or ConnectAltView._filter_text(self.detail, 200) != self.detail
        ):
            raise ValueError("outcome detail must be one short printable line")

    @classmethod
    def pending(cls, receipt_id: str) -> ConnectOutcome:
        return cls(ConnectStatus.PENDING, receipt_id)

    @classmethod
    def approved(cls, detail: str = "") -> ConnectOutcome:
        return cls(ConnectStatus.APPROVED, detail=detail)

    @classmethod
    def connected(cls, detail: str = "") -> ConnectOutcome:
        return cls(ConnectStatus.CONNECTED, detail=detail)

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
# Starts a network on a directory when the code is left empty; true on success.
ConnectAttachCallback = Callable[[str], bool | Awaitable[bool]]


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
        on_attach: ConnectAttachCallback | None = None,
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
        self._on_attach = on_attach
        self._renderer: Any = None
        self._code_chars: list[str] = []
        self._domain_cursor = len(self.domain)
        self._code_cursor = 0
        # The domain is prefilled, so the first thing a person types is the code.
        self._focus = "code" if self.domain else "domain"
        self._stage = "entry"
        self._validation_error = ""
        self._outcome: ConnectOutcome | None = None
        self.cancelled = False
        self._paste_active = False
        self._paste_from = 0

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
        self._focus = "code" if self.domain else "domain"
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
        top = max(0, (height - 12) // 2)
        renderer.write_at(
            0,
            top,
            solid_fg(str(C["half_bottom"]) * width, theme.dark[1]),
            "",
        )
        renderer.write_at(
            0,
            top + 1,
            solid(" Connect".ljust(width), theme.dark[1], theme.text, width),
            "",
        )

        if self._stage == "entry":
            self._render_entry(top + 3, width)
        elif self._stage == "submitting":
            self._write_line(2, top + 4, "submitting…", width)
        else:
            self._render_outcome(top + 4, width)
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "BracketedPasteStart":
            self._paste_active = True
            self._paste_from = self._domain_cursor
            return False
        if key_press.name == "BracketedPasteEnd":
            self._paste_active = False
            self._move_pasted_code()
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
        self._write_line(1, y, "network".ljust(_LABEL_WIDTH) + "none", width)
        self._write_line(1, y + 2, self._field("domain", self._visible_domain()), width)
        code_display = "********" if self._code_chars else "(enter privately)"
        self._write_line(1, y + 3, self._field("join code", code_display), width)
        if self._validation_error:
            self._write_line(2, y + 5, self._validation_error, width)
        self._write_line(2, y + 7, "tab switch  enter submit  esc cancel", width)
        hints = ["no code? run /connect code on a device already on the network"]
        if self._on_attach is not None:
            hints.append(
                f"first device? an empty code plus enter starts a network on {self.domain}"
            )
        row = y + 9
        for hint in hints:
            for line in textwrap.wrap(hint, max(10, width - 3)):
                self._write_line(2, row, line, width)
                row += 1

    def _field(self, label: str, value: str) -> str:
        focused = self._focus == ("domain" if label == "domain" else "code")
        return f"{'>' if focused else ' '} {label}".ljust(_LABEL_WIDTH) + value

    def _render_outcome(self, y: int, width: int) -> None:
        outcome = self._outcome
        if outcome is None:
            message = "cancelled"
        elif outcome.status is ConnectStatus.PENDING:
            message = (
                f"request sent to {self.domain}; "
                "waiting for approval on another device"
            )
        elif outcome.status is ConnectStatus.APPROVED:
            message = outcome.detail or f"joined {self.domain}"
        elif outcome.status is ConnectStatus.CONNECTED:
            message = outcome.detail or f"connected to {self.domain}"
        elif outcome.status is ConnectStatus.REJECTED:
            message = "join request rejected"
        else:
            message = "could not submit the join request"

        self._write_line(2, y, message, width)
        self._write_line(2, y + 3, "enter/esc close", width)

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
            self.domain = "".join(field)
        setattr(self, cursor_attr, cursor)
        self._validation_error = ""
        self.request_render()

    def _move_pasted_code(self) -> None:
        """A code pasted into the domain field moves to the private field.

        Only what was just pasted moves, and only when it starts with a code
        and has no dot, so it is never left in clear text and a pasted
        domain such as `team-share.example.com` is never taken for a code.
        """
        if self._focus != "domain":
            return
        text, start, end = self.domain, self._paste_from, self._domain_cursor
        chunk = text[start:end]
        if "." in chunk or _SHORT_CODE_START_RE.match(chunk) is None:
            return
        self._clear_code_input()
        self._code_chars.extend(list(chunk)[:_MAX_CODE_LENGTH])
        self._code_cursor = len(self._code_chars)
        self.domain = text[:start] + text[end:]
        self._domain_cursor = start
        self._focus = "code"

    async def _attach(self) -> None:
        """Empty code: start a network on the domain, the first-device path."""
        self._stage = "submitting"
        self._validation_error = ""
        self.request_render()
        try:
            result = self._on_attach(self.domain) if self._on_attach else False
            if inspect.isawaitable(result):
                result = await result
            self._outcome = (
                ConnectOutcome.connected() if result is True else ConnectOutcome.error()
            )
        except asyncio.CancelledError:
            self._outcome = ConnectOutcome.error()
            self._stage = "outcome"
            raise
        except Exception:
            self._outcome = ConnectOutcome.error()
        self._stage = "outcome"
        self.request_render()

    async def _submit(self) -> None:
        if not self.domain.strip():
            self._validation_error = "enter a domain"
            self.request_render()
            return
        if not self._code_chars:
            if self._on_attach is not None:
                await self._attach()
                return
            self._validation_error = "enter a domain and join code"
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


@dataclass(frozen=True, slots=True)
class ConnectScreenState:
    """Everything ``connect_screen_lines`` needs; no terminal, no callbacks."""

    snapshot: ConnectSnapshot | None = None
    code: str = ""
    code_remaining: int = 0
    code_status: str = "creating"  # creating | active | expired | failed
    selected: int = 0
    notice: tuple[str, ...] = ()
    code_only: bool = False


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: width - 1] + "…"


def _block(label: str, values: list[str], width: int) -> list[str]:
    """`label` on the first row, blank label under it, values in one column."""
    return [
        _fit(" " + (label if index == 0 else "").ljust(_LABEL_WIDTH) + value, width)
        for index, value in enumerate(values)
    ]


def _code_value(state: ConnectScreenState) -> str:
    if state.code_status == "active":
        minutes, seconds = divmod(max(0, state.code_remaining), 60)
        return f"{state.code}   one device, expires in {minutes}:{seconds:02d}"
    if state.code_status == "expired":
        return "expired   press c for a new code"
    if state.code_status == "creating":
        return "creating…"
    unreachable = state.snapshot is not None and not state.snapshot.relay_online
    reason = "relay unreachable" if unreachable else "could not create a code"
    return f"{reason}   press c to try again"


def _request_rows(state: ConnectScreenState, width: int) -> list[str]:
    requests = state.snapshot.requests if state.snapshot else ()
    if not requests:
        return ["none"]
    room = width - 1 - _LABEL_WIDTH
    rows: list[str] = []
    for index, request in enumerate(requests):
        marker = ("> " if index == state.selected else "  ") if len(requests) > 1 else ""
        who = f"{marker}{request.device or 'unknown device'} wants to join"
        tail = f"fingerprint {request.fingerprint}   [a]ccept [r]eject"
        if len(who) + 3 + len(tail) <= room:
            rows.append(f"{who}   {tail}")
        else:
            rows += [who, " " * len(marker) + tail]
    return rows


def _footer(state: ConnectScreenState) -> str:
    keys = []
    requests = state.snapshot.requests if state.snapshot and not state.code_only else ()
    if len(requests) > 1:
        keys.append("up/down select")
    if requests:
        keys += ["a accept", "r reject"]
    keys += ["c new code", "esc close"]
    return " " + "  ".join(keys)


def _clip(lines: list[str], width: int, max_lines: int | None) -> list[str]:
    if max_lines is None or len(lines) <= max_lines:
        return lines
    return lines[: max(1, max_lines - 1)] + [_fit(" …", width)]


def connect_screen_lines(
    state: ConnectScreenState, width: int, max_lines: int | None = None
) -> list[str]:
    """The Connect screen as plain lines, each at most ``width`` characters.

    Pure: state in, strings out, so it is testable without a terminal. The
    first line is the title. Names and short fingerprints only; keys, receipt
    ids and relay addresses never reach this function.
    """
    lines = [_fit(" Connect code" if state.code_only else " Connect", width)]
    snapshot = state.snapshot
    if state.code_only:
        lines += _block("join code", [_code_value(state)], width)
        if state.code_status == "active":
            lines.append(_fit(" type it into /connect on the other machine", width))
    elif snapshot is None:
        lines += _block("network", ["loading…"], width)
        lines += _block("join code", [_code_value(state)], width)
    else:
        network = network_label(snapshot.network, snapshot.domain)
        lines += _block("network", [f"{network}   trust: {snapshot.trust}"], width)
        lines += _block("this device", [snapshot.device or "unnamed"], width)
        lines += _block("join code", [_code_value(state)], width)
        if snapshot.requests or not state.notice:
            lines += _block("requests", _request_rows(state, width), width)
        lines += [_fit(" " + text, width) for text in state.notice]
        if snapshot.knocks:
            lines += _block(
                "knocks", [f"{snapshot.knocks} waiting   /connect knocks"], width
            )
        online = (
            [f"{name} (this device)" for name in snapshot.local_agents]
            + list(snapshot.remote_agents)
            + [f"{device} (offline)" for device in snapshot.offline_devices]
        )
        lines += _block("online", online or ["none"], width)
    lines += ["", _fit(_footer(state), width)]
    return _clip(lines, width, max_lines)


def _sent_summary(categories: tuple[str, ...]) -> str:
    """What accepting queues for the new device, from the request's own scope.

    An oauth login never travels (constitution section 9), so a provider
    category always means an api key.
    """
    if any(category.startswith("provider:") for category in categories):
        return "profile settings and one api key"
    return ""


class ConnectScreenAltView(AltView):
    """The Connect screen: join code, requests and roster on one page.

    The code is created when the screen opens and lives only in a
    ``PrivateCode`` that is wiped on expiry and on every exit. Requests and
    the roster refresh from ``on_load`` in a background task, so a slow relay
    never stalls rendering or input. With ``code_only`` it shows just the
    code (``/connect code``); nothing but this private view ever holds it.
    """

    def __init__(
        self,
        domain: str = "kollabor.ai",
        on_create: EnrollmentOfferCallback | None = None,
        on_load: Callable[[], Any] | None = None,
        on_decide: Callable[[JoinRequestRow, str], Any] | None = None,
        *,
        code_only: bool = False,
    ) -> None:
        metadata = AltViewMetadata(
            plugin_type="connect-screen",
            description="Join code, requests and online agents",
            version="1.0.0",
            author="Kollabor",
            category="internal",
            icon="[LINK]",
            aliases=[],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)
        self.target_fps = 1.0
        self.render_on_timer = True  # the code's countdown ticks
        self._domain = ConnectAltView._filter_text(domain, _MAX_DOMAIN_LENGTH)
        self._on_create = on_create
        self._on_load = on_load
        self._on_decide = on_decide
        self.code_only = code_only
        self._renderer: Any = None
        self._private_code: PrivateCode | None = None
        self._expires_at = 0
        self._code_status = "creating"
        self._snapshot: ConnectSnapshot | None = None
        self._selected = 0
        self._notice: tuple[str, ...] = ()
        self._deciding = False
        # Cleared by a decision, set again once the redraw shows the next
        # request, so a held or double-tapped key cannot decide one unseen.
        self._armed = True

    @property
    def domain(self) -> str:
        return self._domain

    async def on_enter(self, renderer: Any) -> None:
        self._renderer = renderer
        self._armed = True
        self._clear_code()
        self._code_status = "creating"
        self._snapshot = None
        self._selected = 0
        self._notice = ()
        self._start_code()
        if not self.code_only and self._on_load is not None:
            self.spawn_background_task(self._poll(), "poll")

    async def render_frame(self, delta_time: float) -> bool:
        if self._renderer is None:
            return False
        width, height = self._renderer.get_terminal_size()
        if width <= 0 or height <= 0:
            return True
        lines = connect_screen_lines(self._screen_state(), width, height - 2)
        self._renderer.clear_screen()
        theme = T()
        self._renderer.write_at(
            0, 0, solid_fg(str(C["half_bottom"]) * width, theme.dark[1]), ""
        )
        self._renderer.write_at(
            0, 1, solid(lines[0].ljust(width), theme.dark[1], theme.text, width), ""
        )
        for offset, line in enumerate(lines[1:]):
            self._renderer.write_at(0, 3 + offset, line, "")
        self._armed = True
        return True

    async def handle_input(self, key_press: KeyPress) -> bool:
        if key_press.name == "Escape" or (
            self.code_only and key_press.name == "Enter"
        ):
            return True
        count = len(self._snapshot.requests) if self._snapshot else 0
        if key_press.name == "ArrowUp":
            self._selected = max(0, self._selected - 1)
        elif key_press.name == "ArrowDown":
            self._selected = max(0, min(count - 1, self._selected + 1))
        else:
            char = (key_press.char or "").lower()
            if char == "c" and self._code_status != "creating":
                self._start_code()
            elif char in ("a", "r") and not self.code_only:
                await self._decide("accept" if char == "a" else "reject")
        self.request_render()
        return False

    async def on_suspend(self) -> None:
        """Discard the code and stop polling on every AltView exit."""
        await self._stop()
        invalidate_render_cache = getattr(
            self._renderer, "invalidate_render_cache", None
        )
        if callable(invalidate_render_cache):
            invalidate_render_cache()
        await super().on_suspend()

    async def on_complete(self) -> None:
        await self._stop()
        await super().on_complete()

    async def _stop(self) -> None:
        tasks = self.background_tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._clear_code()

    def _start_code(self) -> None:
        self.spawn_background_task(self._create(), "code")

    async def _poll(self) -> None:
        while True:
            await self._refresh()
            await asyncio.sleep(_POLL_SECONDS)

    async def _refresh(self) -> None:
        """Reload requests and roster; on any failure keep what is on screen."""
        try:
            result = self._on_load() if self._on_load is not None else None
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, ConnectSnapshot):
                self._snapshot = result
                self._selected = max(0, min(self._selected, len(result.requests) - 1))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        self.request_render()

    async def _create(self) -> None:
        self._clear_code()
        self._code_status = "creating"
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
                or not _OFFER_ID_RE.fullmatch(offer_id)
                or not isinstance(expires_at, str)
                or not expires_at.isdigit()
                or int(expires_at) <= int(time.time())
                or not isinstance(code, str)
                or not _SHORT_CODE_RE.fullmatch(code)
            ):
                raise ValueError("invalid enrollment offer")
            self._private_code = PrivateCode(code)
            self._expires_at = int(expires_at)
            self._code_status = "active"
        except asyncio.CancelledError:
            self._clear_code()
            self._code_status = "failed"
            raise
        except Exception:
            # Callback errors may carry the code; show only the fixed line.
            self._clear_code()
            self._code_status = "failed"
        self.request_render()

    async def _decide(self, decision: str) -> None:
        snapshot = self._snapshot
        if (
            snapshot is None
            or not snapshot.requests
            or self._on_decide is None
            or self._deciding
            or not self._armed
        ):
            return
        row = snapshot.requests[self._selected]
        who = row.device or "that device"
        self._deciding = True
        try:
            reason = self._on_decide(row, decision)
            if inspect.isawaitable(reason):
                reason = await reason
        except asyncio.CancelledError:
            raise
        except Exception:
            reason = "try again"
        finally:
            self._deciding = False
        if isinstance(reason, str) and reason:
            self._notice = (f"could not {decision} {who}: {reason}",)
            return
        if decision == "accept":
            notice = [
                f"accepted {who}. it is now a trusted device on {snapshot.network}."
            ]
            sent = _sent_summary(row.categories)
            if sent:
                notice.append(f"sealed config queued for {who}: {sent}")
            self._notice = tuple(notice)
        else:
            self._notice = (f"rejected {who}.",)
        remaining = tuple(item for item in snapshot.requests if item is not row)
        self._snapshot = replace(snapshot, requests=remaining)
        self._selected = max(0, min(self._selected, len(remaining) - 1))
        self._armed = False

    def _screen_state(self) -> ConnectScreenState:
        remaining = 0
        code = ""
        if self._code_status == "active":
            remaining = self._expires_at - int(time.time())
            if remaining <= 0 or self._private_code is None:
                self._clear_code()
                self._code_status = "expired"
            else:
                code = self._private_code.reveal()
        return ConnectScreenState(
            snapshot=self._snapshot,
            code=code,
            code_remaining=remaining,
            code_status=self._code_status,
            selected=self._selected,
            notice=self._notice,
            code_only=self.code_only,
        )

    def _clear_code(self) -> None:
        if self._private_code is not None:
            self._private_code.clear()
            self._private_code = None
