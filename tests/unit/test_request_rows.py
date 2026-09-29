"""Request rows (knock review, Connect screen) fit the terminal whatever a sender sends.

A sender picks the device name and the introduction. Rows are measured in
terminal columns (CJK is two wide), names are capped, tabs are dropped, and the
`[a]ccept [r]eject` hint is whole or absent, never cut mid-token.
"""

import re
import time

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.connect_altview import (
    ConnectScreenAltView,
    ConnectScreenState,
    connect_screen_lines,
)
from plugins.altview.contact_altview import ContactReviewAltView
from plugins.hub.contact_requests import PendingContactRequest, PrivateMessage
from plugins.hub.device_names import (
    NAME_DISPLAY_MAX,
    clip_display,
    display_name,
    display_width,
    request_row,
)
from plugins.hub.relay_commands import ConnectSnapshot, JoinRequestRow

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
HINT = "[a]ccept [r]eject"
WIDTHS = [60, 80, 120]
# name (up to 63 characters), introduction
HOSTILE = [
    ("x" * 63, "Ana from Webceive. Can your ops agent review a config? " * 8),
    ("日本語" * 21, "日本語の紹介文" * 40),
    ("tab\tby\t" + "y" * 50, "\t\t\thello\tworld " * 30),
    ("ana-laptop", "short"),
    ("é" * 63 + "́" * 5, "a‮b\x1b[31mc" * 40),
]


class _Renderer:
    def __init__(self, size):
        self._size = size
        self.lines = []

    def get_terminal_size(self):
        return self._size

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append((x, y, _ANSI.sub("", text)))

    def text(self):
        return "\n".join(text for _, _, text in self.lines)


def _key(char):
    return KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)


def _assert_hint_whole(text):
    assert text.count("[a]ccept") == text.count(HINT)
    assert text.count("[r]eject") == text.count(HINT)


# --------------------------------------------------------------------------- #
# The shared helper
# --------------------------------------------------------------------------- #


def test_display_width_counts_columns_not_characters():
    assert display_width("abc") == 3
    assert display_width("日本") == 4
    assert display_width("é") == 1  # a combining mark takes no column
    assert display_width("‮") == 0  # neither does a bidi control


def test_clip_display_marks_the_cut_and_never_overshoots():
    assert clip_display("hello", 10) == "hello"
    assert clip_display("hello world", 6) == "hello…"
    clipped = clip_display("日本語日本語", 7)  # two columns per character
    assert clipped == "日本語…" and display_width(clipped) <= 7
    assert clip_display("anything", 0) == ""


def test_display_name_drops_tabs_and_controls_and_caps_the_length():
    assert display_name("ana\tlaptop\x1b[31m") == "analaptop[31m"
    assert display_width(display_name("x" * 63)) == NAME_DISPLAY_MAX
    assert display_name("x" * 63).endswith("…")
    assert display_name("ana-laptop") == "ana-laptop"
    assert display_width(display_name("日本語" * 21)) <= NAME_DISPLAY_MAX


@pytest.mark.parametrize("width", [26, 44, 60, 80, 120])
@pytest.mark.parametrize("quote", ["", "short", "long introduction " * 20, "日本語" * 40])
def test_request_row_never_exceeds_width_and_the_hint_is_whole_or_absent(width, quote):
    lines = request_row(
        "> 1. " + display_name("x" * 63),
        "fingerprint 91c0…77ab",
        width,
        hint=HINT,
        quote=quote,
        indent="     ",
        gap="  ",
    )

    assert 1 <= len(lines) <= 2
    assert all(display_width(line) <= width for line in lines), lines
    _assert_hint_whole("\n".join(lines))


def test_request_row_stays_on_one_line_when_it_fits():
    (line,) = request_row("ana-laptop wants to join", "fingerprint 4d04…9f2e", 120, hint=HINT)

    assert line == "ana-laptop wants to join   fingerprint 4d04…9f2e   " + HINT


def test_request_row_splits_instead_of_wrapping_when_narrow():
    lines = request_row(
        "ana-laptop wants to join", "fingerprint 4d04…9f2e", 50, hint=HINT, indent="  "
    )

    assert lines == [
        "ana-laptop wants to join",
        "  fingerprint 4d04…9f2e   " + HINT,
    ]


# --------------------------------------------------------------------------- #
# The knock review
# --------------------------------------------------------------------------- #


async def _review(width, name, intro, *, count=1):
    requests = [
        PendingContactRequest(
            str(index) * 32,
            str(index) * 64,
            int(time.time()) + 600,
            PrivateMessage(intro),
            name,
        )
        for index in range(1, count + 1)
    ]
    renderer = _Renderer((width, 30))

    async def load():
        return requests

    async def decide(_request, _decision):
        return None

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)
    return view, renderer


@pytest.mark.asyncio
@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("name, intro", HOSTILE)
async def test_knock_rows_fit_the_width_with_a_hostile_name_and_introduction(width, name, intro):
    _view, renderer = await _review(width, name, intro)

    for x, _y, line in renderer.lines:
        assert x + display_width(line) <= width, (width, line)
    text = renderer.text()
    assert "\t" not in text and "\x1b" not in text and "‮" not in text
    if display_width(name) > NAME_DISPLAY_MAX:
        assert name not in text  # only the capped name is ever printed
    assert HINT in text  # the selected row keeps its whole hint
    _assert_hint_whole(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("width", WIDTHS)
async def test_several_knock_rows_fit_and_only_the_selected_row_carries_the_hint(width):
    view, renderer = await _review(width, "x" * 63, "hello " * 30, count=3)

    for x, _y, line in renderer.lines:
        assert x + display_width(line) <= width
    assert renderer.text().count(HINT) == 1
    await view.handle_input(KeyPress(name="ArrowDown", code=0, char=None, type=KeyType.SPECIAL))
    await view.render_frame(0)
    assert renderer.text().count(HINT) == 1
    assert all(x + display_width(line) <= width for x, _y, line in renderer.lines)


@pytest.mark.asyncio
async def test_a_wide_name_is_capped_in_the_confirmation_too():
    view, renderer = await _review(80, "日本語" * 21, "hi")

    await view.handle_input(_key("a"))
    await view.render_frame(0)

    for x, _y, line in renderer.lines:
        assert x + display_width(line) <= 80
    assert "accepted 日本語" in renderer.text()


# --------------------------------------------------------------------------- #
# The Connect screen
# --------------------------------------------------------------------------- #


def _join_row(device):
    return JoinRequestRow(
        enrollment_id="a" * 32,
        device=device,
        fingerprint="4d04…9f2e",
        categories=("conversation:send",),
    )


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("name", [h[0] for h in HOSTILE])
def test_connect_request_rows_fit_the_width_with_a_hostile_name(width, name):
    snapshot = ConnectSnapshot(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="mac-kollab",
        relay_online=True,
        requests=(_join_row(name), _join_row("ana-laptop"), _join_row(name)),
    )
    state = ConnectScreenState(
        snapshot=snapshot, code="7QK4-M2XP", code_remaining=200, code_status="active", selected=2
    )

    lines = connect_screen_lines(state, width)

    assert all(display_width(line) <= width for line in lines), [
        (display_width(line), line) for line in lines if display_width(line) > width
    ]
    text = "\n".join(lines)
    assert "\t" not in text
    if display_width(name) > NAME_DISPLAY_MAX:
        assert name not in text
    assert text.count(HINT) == 3  # every request row keeps its whole hint
    _assert_hint_whole(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("width", WIDTHS)
async def test_the_connect_screen_notice_caps_a_sender_chosen_name(width):
    name = "日本語" * 21
    snapshot = ConnectSnapshot(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="mac-kollab",
        relay_online=True,
        requests=(_join_row(name),),
    )

    async def on_create(_domain):
        raise ValueError("no code in this test")

    async def on_load():
        return snapshot

    async def on_decide(_row, _decision):
        return None

    view = ConnectScreenAltView(
        "kollabor.ai", on_create=on_create, on_load=on_load, on_decide=on_decide
    )
    renderer = _Renderer((width, 30))
    await view.on_enter(renderer)
    for _ in range(12):
        await __import__("asyncio").sleep(0)
    await view.render_frame(0)
    await view.handle_input(_key("a"))
    await view.render_frame(0)

    for x, _y, line in renderer.lines:
        assert x + display_width(line) <= width, line
    assert name not in renderer.text()
    await view.on_complete()
