"""Request rows (knock screen, Connect screen) fit the terminal whatever a sender sends.

A sender picks the device name and the introduction. Rows are measured in
terminal columns (CJK is two wide), a valid name shows whole and is cut, with
an ellipsis, only when its row is wider than the terminal, tabs are dropped,
and the `[a]ccept [r]eject` hint (`[a]ccept [r]eject [b]lock` on a ringing
knock) is whole or absent, never cut mid-token.
"""

import re

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.connect_altview import (
    ConnectAltView,
    ConnectOutcome,
    ConnectScreenAltView,
    ConnectScreenState,
    connect_screen_lines,
)
from plugins.altview.knocks_altview import KnockScreenAltView
from plugins.hub.device_names import (
    NAME_DISPLAY_MAX,
    NAME_RE,
    clip_display,
    display_name,
    display_width,
    request_row,
)
from plugins.hub.relay_commands import ConnectSnapshot, JoinRequestRow

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
HINT = "[a]ccept [r]eject"
KNOCK_HINT = "[a]ccept [r]eject [b]lock"
WIDTHS = [60, 80, 120]
# The longest device name NAME_RE allows: 63 characters, every one valid.
LONGEST = "-".join(["lab"] * 16)
# name (up to 63 characters), introduction
HOSTILE = [
    ("x" * 63, "Ana from Acme. Can your ops agent review a config? " * 8),
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


def test_the_display_cap_is_the_longest_valid_device_name():
    assert len(LONGEST) == NAME_DISPLAY_MAX == 63
    assert NAME_RE.fullmatch(LONGEST)
    assert not NAME_RE.fullmatch(LONGEST + "x")


def test_display_name_drops_tabs_and_controls_and_never_cuts_a_valid_name():
    assert display_name("ana\tlaptop\x1b[31m") == "analaptop[31m"
    assert display_name(LONGEST) == LONGEST
    assert display_name("ana-laptop") == "ana-laptop"
    # a row passes the room it has; the cut is marked and measured in columns
    assert display_name(LONGEST, 30) == LONGEST[:29] + "…"
    # what the protocol never delivers (longer than any valid name) is still bounded
    assert display_width(display_name("x" * 200)) == NAME_DISPLAY_MAX
    assert display_name("x" * 200).endswith("…")
    assert display_width(display_name("日本語" * 21)) <= NAME_DISPLAY_MAX


@pytest.mark.parametrize("width", [26, 44, 60, 80, 120])
@pytest.mark.parametrize("quote", ["", "short", "long introduction " * 20, "日本語" * 40])
def test_request_row_never_exceeds_width_and_the_hint_is_whole_or_absent(width, quote):
    lines = request_row(
        "> 1. " + display_name("x" * 63),
        "fingerprint 1234…5678",
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
    (line,) = request_row("ana-laptop wants to join", "fingerprint abcd…ef01", 120, hint=HINT)

    assert line == "ana-laptop wants to join   fingerprint abcd…ef01   " + HINT


def test_request_row_splits_instead_of_wrapping_when_narrow():
    lines = request_row(
        "ana-laptop wants to join", "fingerprint abcd…ef01", 50, hint=HINT, indent="  "
    )

    assert lines == [
        "ana-laptop wants to join",
        "  fingerprint abcd…ef01   " + HINT,
    ]


# --------------------------------------------------------------------------- #
# The knock review
# --------------------------------------------------------------------------- #


async def _review(width, name, intro, *, count=1):
    snapshot = {
        "online": True, "domain": "relay.example", "mode": "everyone", "mode_until": 0,
        "missed_limit": 20, "calls": [], "missed": [], "blocked": [], "contacts": [],
        "ringing": [
            {"id": str(index) * 32, "device": name, "fingerprint": "ab12…ef01",
             "route": "8f3a2c1d9e4b7a60", "text": intro, "left": 290}
            for index in range(1, count + 1)
        ],
    }
    renderer = _Renderer((width, 30))

    async def act(action, _args):
        shown = display_name(name)
        return f"accepted {shown}. /connect allow {shown} <agent> lets it message one of your agents"

    view = KnockScreenAltView(lambda: snapshot, act)
    await view.on_enter(renderer)
    await view._refresh()
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
    assert KNOCK_HINT in text  # the selected row keeps its whole hint
    assert text.count("[b]lock") == text.count(KNOCK_HINT)


@pytest.mark.asyncio
@pytest.mark.parametrize("width", WIDTHS)
async def test_several_knock_rows_fit_and_only_the_selected_row_carries_the_hint(width):
    view, renderer = await _review(width, "x" * 63, "hello " * 30, count=3)

    for x, _y, line in renderer.lines:
        assert x + display_width(line) <= width
    assert renderer.text().count(KNOCK_HINT) == 1
    await view.handle_input(KeyPress(name="ArrowDown", code=0, char=None, type=KeyType.SPECIAL))
    await view.render_frame(0)
    assert renderer.text().count(KNOCK_HINT) == 1
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
        fingerprint="abcd…ef01",
        categories=("conversation:send",),
    )


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("name", [h[0] for h in HOSTILE])
def test_connect_request_rows_fit_the_width_with_a_hostile_name(width, name):
    snapshot = ConnectSnapshot(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="laptop-kollab",
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
        device="laptop-kollab",
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


# --------------------------------------------------------------------------- #
# A valid name shows whole; only a row wider than the terminal cuts it
# --------------------------------------------------------------------------- #

_VALUE = " requests     "  # the label column that starts every Connect screen row
_WORDS = " wants to join"


def _screen(*names, width, selected=0):
    snapshot = ConnectSnapshot(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="laptop-kollab",
        relay_online=True,
        requests=tuple(_join_row(name) for name in names),
    )
    state = ConnectScreenState(
        snapshot=snapshot,
        code="7QK4-M2XP",
        code_remaining=200,
        code_status="active",
        selected=selected,
    )
    return connect_screen_lines(state, width)


def _heads(lines):
    """The lines that say who wants to join."""
    return [line for line in lines if line.endswith(_WORDS)]


def test_the_longest_name_shows_in_full_on_the_request_row_at_120_columns():
    lines = _screen(LONGEST, width=120)

    assert _heads(lines) == [f"{_VALUE}{LONGEST}{_WORDS}"]
    assert all(display_width(line) <= 120 for line in lines)
    assert f"fingerprint abcd…ef01   {HINT}" in "\n".join(lines)


def test_the_longest_name_is_cut_with_an_ellipsis_at_80_columns_and_the_words_stay():
    lines = _screen(LONGEST, width=80)

    (head,) = _heads(lines)
    name = head[len(_VALUE) : -len(_WORDS)]
    assert name.endswith("…") and LONGEST.startswith(name[:-1])
    assert display_width(head) == 80  # the name takes everything the row has
    assert LONGEST not in "\n".join(lines)
    assert all(display_width(line) <= 80 for line in lines)
    assert f"fingerprint abcd…ef01   {HINT}" in "\n".join(lines)


@pytest.mark.parametrize("width", [80, 120])
def test_a_selection_marker_takes_its_columns_out_of_the_name_not_the_words(width):
    lines = _screen(LONGEST, "ana-laptop", LONGEST, width=width, selected=2)

    heads = [line for line in lines if _WORDS in line]
    assert len(heads) == 3 and all(display_width(line) <= width for line in lines)
    assert [head[len(_VALUE) : len(_VALUE) + 2] for head in heads] == ["  ", "  ", "> "]
    names = [head[len(_VALUE) + 2 : head.index(_WORDS)] for head in heads]
    assert names[1] == "ana-laptop"
    for name in (names[0], names[2]):
        if width == 120:
            assert name == LONGEST
        else:
            assert name == LONGEST[:49] + "…" and display_width(name) == 50


@pytest.mark.parametrize("width", [60, 80])
def test_a_name_is_cut_exactly_when_its_row_is_full(width):
    room = width - len(_VALUE) - len(_WORDS)

    assert _heads(_screen("a" * room, width=width)) == [f"{_VALUE}{'a' * room}{_WORDS}"]
    (cut,) = _heads(_screen("a" * (room + 1), width=width))
    assert cut == f"{_VALUE}{'a' * (room - 1)}…{_WORDS}"


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [80, 120])
async def test_the_accept_notice_names_the_longest_device_in_full_when_it_fits(width):
    snapshot = ConnectSnapshot(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="laptop-kollab",
        relay_online=True,
        requests=(_join_row(LONGEST),),
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

    (notice,) = [line for _x, _y, line in renderer.lines if "accepted" in line]
    assert all(x + display_width(line) <= width for x, _y, line in renderer.lines)
    full = f" accepted {LONGEST}. it is now a trusted device on marco-home."
    if width == 120:
        assert notice == full
    else:
        assert notice.endswith("…") and full.startswith(notice[:-1])
    await view.on_complete()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [80, 120])
async def test_the_joined_line_is_cut_by_width_with_an_ellipsis_not_mid_character(width):
    joined = f"joined marco-home as {LONGEST}. trust: open"
    view = ConnectAltView(on_submit=lambda _submission: ConnectOutcome.approved(joined))
    renderer = _Renderer((width, 24))
    await view.on_enter(renderer)
    view._focus = "code"
    for character in "7QK4M2XP":
        await view.handle_input(_key(character))
    await view.handle_input(KeyPress(name="Enter", code=0, char=None, type=KeyType.SPECIAL))
    await view.render_frame(0)

    ((x, _y, line),) = [row for row in renderer.lines if "joined" in row[2]]
    assert x + display_width(line) <= width
    if width == 120:
        assert line == joined
    else:
        assert line.endswith("…") and joined.startswith(line[:-1])


@pytest.mark.asyncio
async def test_a_knock_row_shows_the_longest_name_in_full_at_120_columns():
    _view, renderer = await _review(120, LONGEST, "hi")

    assert f"1. {LONGEST}" in renderer.text()
    assert all(x + display_width(line) <= 120 for x, _y, line in renderer.lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [60, 80])
async def test_a_knock_row_cuts_the_longest_name_with_an_ellipsis_on_a_narrower_terminal(width):
    _view, renderer = await _review(width, LONGEST, "hi")

    assert LONGEST not in renderer.text()
    ((x, _y, row),) = [r for r in renderer.lines if "> 1. " in r[2]]
    assert row.endswith("…") and x + display_width(row) == width
    assert KNOCK_HINT in renderer.text()  # the keys stay whole on a line of their own


@pytest.mark.asyncio
async def test_a_confirmation_that_needs_more_than_three_lines_says_it_was_cut():
    view, renderer = await _review(60, LONGEST, "hi")

    await view.handle_input(_key("a"))
    await view.render_frame(0)

    assert all(x + display_width(line) <= 60 for x, _y, line in renderer.lines)
    lines = [line for _x, y, line in sorted(renderer.lines, key=lambda r: r[1]) if line.strip()]
    assert any(line.endswith("…") for line in lines)
    assert "accepted lab-" in renderer.text()
