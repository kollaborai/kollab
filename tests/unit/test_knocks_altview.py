"""The knock screen (plugins/altview/knocks_altview.py): what it shows and what each key does."""

import asyncio
import re

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview import knocks_altview
from plugins.altview.knocks_altview import KnockScreenAltView, knock_screen_lines

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
KEY = "c" * 64


def _snapshot(**over):
    snapshot = {
        "online": True, "domain": "relay.example", "mode": "everyone", "mode_until": 0, "missed_limit": 20,
        "ringing": [{"id": "1" * 32, "device": "ana-laptop", "fingerprint": "ab12…ef01",
                     "route": "8f3a2c1d9e4b7a60", "text": "Ana from Acme", "left": 290}],
        "calls": [{"route": "1a2b3c4d5e6f7a8b", "target": "relay.example/c/1a2b3c4d5e6f7a8b",
                   "state": "redialing", "left": 60}],
        "missed": [{"id": "2" * 32, "device": "bob-desk", "fingerprint": "cd34…0a1b",
                    "route": "0f1e2d3c4b5a6978", "text": "hello", "at": 1_800_000_000}],
        "blocked": [{"route": "0123456789abcdef", "device": "spam-box"}],
        "contacts": [{"route": "fedcba9876543210", "expected": True}],
    }
    return snapshot | over


class _Renderer:
    def __init__(self, size=(100, 40)):
        self.size, self.lines = size, []

    def get_terminal_size(self):
        return self.size

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append((x, y, _ANSI.sub("", text)))

    def text(self):
        return "\n".join(text for _x, _y, text in self.lines)


def _key(name):
    if len(name) == 1:
        return KeyPress(name=name, code=ord(name), char=name, type=KeyType.PRINTABLE)
    return KeyPress(name=name, code=0, char=None, type=KeyType.SPECIAL)


async def _screen(snapshot=None, *, poll=False):
    """A knock screen on a fake renderer; `acted` records every action."""
    acted, state = [], {"snapshot": snapshot or _snapshot()}

    def act(action, args):
        acted.append((action, args))
        return f"{action} done"

    view = KnockScreenAltView(lambda: state["snapshot"], act)
    renderer = _Renderer()
    if poll:
        await view.on_enter(renderer)
    else:  # one load, no background poll
        view._renderer = renderer
        await view._refresh()
    await view.render_frame(0)
    return view, renderer, acted, state


async def _press(view, *names):
    for name in names:
        await view.handle_input(_key(name))
        await view.render_frame(0)


def test_every_kind_of_row_shows_names_and_routes_never_keys_or_ids():
    lines = knock_screen_lines(_snapshot(), 0, 100)
    text = "\n".join(lines)

    for label in ("ringing", "knocking", "missed", "blocked", "contacts", "who may knock"):
        assert f" {label}" in text
    assert "ana-laptop" in text and '"Ana from Acme"' in text and "4:50" in text
    assert "redialing, next in 1:00" in text and "1 of 20   [c] clear all" in text
    assert "0123456789abcdef   spam-box" in text and "first knock accepted" in text
    assert not re.search(r"[0-9a-f]{32}", text)  # no key, no knock id
    assert all(len(line) <= 100 for line in lines)
    assert lines[-1] == " up/down select   w who may knock   esc close"


def test_sender_text_cannot_move_the_cursor_or_turn_the_line_around():
    hostile = "a\x1b[2Jb‮c\u0085d\nnext\tline"
    snapshot = _snapshot(ringing=[_snapshot()["ringing"][0] | {"text": hostile}])

    text = "\n".join(knock_screen_lines(snapshot, 0, 120))

    assert '"a[2Jbcd next line"' in text
    assert not any(c in text for c in "\x1b‮\u0085\t")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "downs, key, expected",
    [
        (0, "a", ("accept", {"id": "1" * 32})),
        (0, "r", ("reject", {"id": "1" * 32})),
        (0, "b", ("block", {"id": "1" * 32})),
        (0, "s", None),  # not a ringing knock's key
        (1, "s", ("stop", {"route": "1a2b3c4d5e6f7a8b"})),
        (2, "b", ("block", {"id": "2" * 32})),
        (2, "d", ("delete", {"id": "2" * 32})),
        (2, "a", None),
        (3, "u", ("unblock", {"route": "0123456789abcdef"})),
        (4, "x", ("unexpect", {"route": "fedcba9876543210"})),
    ],
)
async def test_each_row_answers_to_its_own_keys_only(downs, key, expected):
    view, renderer, acted, _state = await _screen()
    await _press(view, *["ArrowDown"] * downs)

    await _press(view, key)

    assert acted == ([expected] if expected else [])
    if expected:
        assert f"{expected[0]} done" in renderer.text()


@pytest.mark.asyncio
async def test_the_selected_row_carries_its_keys_and_no_other_row_does():
    view, renderer, _acted, _state = await _screen()

    assert renderer.text().count("[a]ccept [r]eject [b]lock") == 1
    assert "[s]top" not in renderer.text()
    await _press(view, "ArrowDown")
    assert "[s]top" in renderer.text() and "[a]ccept" not in renderer.text()


@pytest.mark.asyncio
async def test_w_steps_through_who_may_knock_and_c_clears_the_missed_list():
    view, _renderer, acted, state = await _screen()

    await _press(view, "w")
    state["snapshot"] = _snapshot(mode="contacts")
    await view._refresh()
    await _press(view, "w", "c")

    assert acted == [
        ("mode", {"mode": "contacts", "minutes": 0}),
        ("mode", {"mode": "nobody", "minutes": 0}),
        ("clear", {}),
    ]
    view2, _r, acted2, _s = await _screen(_snapshot(missed=[]))
    await _press(view2, "c")
    assert acted2 == []  # nothing to clear


@pytest.mark.asyncio
async def test_knock_back_asks_for_the_words_then_sends_or_cancels():
    view, renderer, acted, _state = await _screen()
    await _press(view, "ArrowDown", "ArrowDown", "k")

    assert " knock back to bob-desk: _" in renderer.text()
    await _press(view, "Enter")  # nothing typed: nothing sent
    await _press(view, "h", "i", "!", "Backspace")
    assert " knock back to bob-desk: hi_" in renderer.text()
    await _press(view, "Enter")
    assert acted == [("knock_back", {"id": "2" * 32, "text": "hi"})]

    await _press(view, "k", "n", "o", "Escape")
    assert acted == [("knock_back", {"id": "2" * 32, "text": "hi"})]
    assert "knock back to" not in renderer.text()


@pytest.mark.asyncio
async def test_a_held_key_cannot_act_on_a_row_the_human_has_not_seen():
    view, _renderer, acted, _state = await _screen()

    await view.handle_input(_key("a"))
    await view.handle_input(_key("a"))  # before the result was drawn
    assert len(acted) == 1

    await view.render_frame(0)
    await view.handle_input(_key("a"))
    assert len(acted) == 2


@pytest.mark.asyncio
async def test_the_selection_stays_on_its_row_when_a_new_knock_arrives_above():
    view, renderer, _acted, state = await _screen()
    await _press(view, "ArrowDown", "ArrowDown")  # the missed knock

    newer = _snapshot()["ringing"][0] | {"id": "3" * 32, "device": "cy-laptop"}
    state["snapshot"] = _snapshot(ringing=[newer, *_snapshot()["ringing"]])
    await view._refresh()
    await view.render_frame(0)

    assert "> 1. bob-desk" in renderer.text()


@pytest.mark.asyncio
async def test_a_bad_snapshot_keeps_what_is_on_screen_or_says_knocks_are_unavailable():
    view, renderer, _acted, state = await _screen()
    state["snapshot"] = {"online": True}

    await view._refresh()
    await view.render_frame(0)
    assert "ana-laptop" in renderer.text()

    fresh, renderer2, _a, _s = await _screen({"ringing": "nope"})
    assert "knocks are unavailable" in renderer2.text()


@pytest.mark.asyncio
async def test_the_screen_reloads_while_open_and_stops_when_it_closes(monkeypatch):
    monkeypatch.setattr(knocks_altview, "_POLL_SECONDS", 0.01)
    loads = []
    view = KnockScreenAltView(lambda: loads.append(1) or _snapshot(), lambda *_: "")
    await view.on_enter(_Renderer())

    await asyncio.sleep(0.05)
    assert len(loads) >= 2
    await view.on_complete()
    seen = len(loads)
    await asyncio.sleep(0.03)
    assert len(loads) == seen
    assert all(task.done() for task in view.background_tasks)
