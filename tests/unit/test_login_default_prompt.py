"""LoginAltView post-auth 'make this your default?' confirmation stage."""

import re

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.login_altview import LoginAltView

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class _FakeRenderer:
    def __init__(self):
        self.lines = []

    def get_terminal_size(self):
        return (100, 30)

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append(_ANSI.sub("", text))

    def text(self):
        return " | ".join(line for line in self.lines if line.strip())


def _char(ch):
    return KeyPress(name=ch, code=ord(ch), char=ch, type=KeyType.PRINTABLE)


def _esc():
    return KeyPress(name="Escape", code=27, char=None, type=KeyType.SPECIAL)


def _confirm_view():
    view = LoginAltView()
    view._renderer = _FakeRenderer()
    view._stage = "confirm"
    view.result_best_model = "gpt-5-codex"
    return view


@pytest.mark.asyncio
async def test_confirm_stage_renders_prompt():
    view = _confirm_view()
    await view.render_frame(0.1)
    text = view._renderer.text()
    assert "make openai-oauth your default profile?" in text
    assert "[y] yes" in text and "[n] no" in text
    assert "y: make default" in text and "n: session only" in text


@pytest.mark.asyncio
async def test_yes_sets_default_and_exits():
    view = _confirm_view()
    assert await view.handle_input(_char("y")) is True
    assert view.result_make_default is True
    assert view._stage == "done"
    assert view._cancelled is False


@pytest.mark.asyncio
async def test_no_declines_default_but_keeps_login():
    view = _confirm_view()
    assert await view.handle_input(_char("n")) is True
    assert view.result_make_default is False
    # login still succeeded -- NOT cancelled, no error set
    assert view._cancelled is False
    assert view.result_error is None


@pytest.mark.asyncio
async def test_escape_at_confirm_declines_without_cancelling_login():
    view = _confirm_view()
    assert await view.handle_input(_esc()) is True
    assert view.result_make_default is False
    assert view._cancelled is False
    assert view.result_error is None


@pytest.mark.asyncio
async def test_escape_while_waiting_still_cancels_login():
    view = LoginAltView()
    view._renderer = _FakeRenderer()
    view._stage = "waiting"
    view._code = "AB-12"
    assert await view.handle_input(_esc()) is True
    assert view._cancelled is True
    assert view.result_error == "cancelled by user"


@pytest.mark.asyncio
async def test_default_make_default_is_false():
    """Fresh view defaults to not-default until the user opts in."""
    view = LoginAltView()
    assert view.result_make_default is False
