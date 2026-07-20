"""Tests for the /login device-code copy-to-clipboard shortcut.

Covers:
  * kollabor_tui.clipboard.copy_to_clipboard (success / missing-tool / failure)
  * LoginAltView 'c' keypress copies the code, keeps the view open, and
    renders the hint + transient feedback.
"""

import re
from unittest.mock import Mock

import pytest

import plugins.altview.login_altview as login_mod
from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.login_altview import LoginAltView

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class _FakeRenderer:
    """Records write_at calls with ANSI stripped for assertions."""

    def __init__(self, size=(100, 30)) -> None:
        self._size = size
        self.lines: list[str] = []

    def get_terminal_size(self):
        return self._size

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append(_ANSI.sub("", text))

    def text(self) -> str:
        return " | ".join(line for line in self.lines if line.strip())


def _key(char: str) -> KeyPress:
    return KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)


def _escape() -> KeyPress:
    return KeyPress(name="Escape", code=27, char=None, type=KeyType.SPECIAL)


def _waiting_view() -> LoginAltView:
    view = LoginAltView()
    view._renderer = _FakeRenderer()
    view._stage = "waiting"
    view._url = "https://chatgpt.com/device"
    view._code = "WXYZ-4821"
    return view


# --- clipboard helper ---------------------------------------------------


def test_copy_to_clipboard_success(monkeypatch):
    from kollabor_tui import clipboard

    captured = {}

    class _Proc:
        returncode = 0

        def communicate(self, input=None):
            captured["input"] = input
            return (b"", b"")

    def _popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(clipboard.subprocess, "Popen", _popen)

    assert clipboard.copy_to_clipboard("hello") is True
    assert captured["input"] == b"hello"
    # first command tried is pbcopy
    assert captured["cmd"][0] == "pbcopy"


def test_copy_to_clipboard_no_tool(monkeypatch):
    from kollabor_tui import clipboard

    def _missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(clipboard.subprocess, "Popen", _missing)

    assert clipboard.copy_to_clipboard("hello") is False


def test_copy_to_clipboard_nonzero_exit(monkeypatch):
    from kollabor_tui import clipboard

    class _Proc:
        returncode = 1

        def communicate(self, input=None):
            return (b"", b"boom")

    monkeypatch.setattr(clipboard.subprocess, "Popen", lambda cmd, **kw: _Proc())

    # every tool returns non-zero -> overall failure
    assert clipboard.copy_to_clipboard("hello") is False


# --- LoginAltView 'c' shortcut ------------------------------------------


@pytest.mark.asyncio
async def test_c_copies_code_and_keeps_view_open(monkeypatch):
    view = _waiting_view()
    copy_mock = Mock(return_value=True)
    monkeypatch.setattr(login_mod, "copy_to_clipboard", copy_mock)

    exit_requested = await view.handle_input(_key("c"))

    copy_mock.assert_called_once_with("WXYZ-4821")
    assert exit_requested is False  # view stays open
    assert view._copy_ok is True
    assert view._copied_at > 0


@pytest.mark.asyncio
async def test_uppercase_c_also_copies(monkeypatch):
    view = _waiting_view()
    copy_mock = Mock(return_value=True)
    monkeypatch.setattr(login_mod, "copy_to_clipboard", copy_mock)

    assert await view.handle_input(_key("C")) is False
    copy_mock.assert_called_once_with("WXYZ-4821")


@pytest.mark.asyncio
async def test_c_is_noop_before_code_issued(monkeypatch):
    view = LoginAltView()
    view._renderer = _FakeRenderer()
    view._stage = "init"  # no code yet
    copy_mock = Mock(return_value=True)
    monkeypatch.setattr(login_mod, "copy_to_clipboard", copy_mock)

    assert await view.handle_input(_key("c")) is False
    copy_mock.assert_not_called()
    assert view._copied_at == 0.0


@pytest.mark.asyncio
async def test_escape_still_exits():
    view = _waiting_view()
    assert await view.handle_input(_escape()) is True
    assert view._cancelled is True


@pytest.mark.asyncio
async def test_render_shows_copy_hint_and_footer():
    view = _waiting_view()
    await view.render_frame(0.1)
    text = view._renderer.text()

    assert "WXYZ-4821" in text  # code visible
    assert "press c to copy" in text  # inline hint
    assert "c: copy code" in text  # footer hint


@pytest.mark.asyncio
async def test_render_shows_feedback_after_copy(monkeypatch):
    view = _waiting_view()
    monkeypatch.setattr(login_mod, "copy_to_clipboard", Mock(return_value=True))
    await view.handle_input(_key("c"))
    await view.render_frame(0.1)

    assert "copied to clipboard" in view._renderer.text()


@pytest.mark.asyncio
async def test_render_shows_failure_feedback_when_no_clipboard(monkeypatch):
    view = _waiting_view()
    monkeypatch.setattr(login_mod, "copy_to_clipboard", Mock(return_value=False))
    await view.handle_input(_key("c"))
    await view.render_frame(0.1)

    assert "no clipboard tool found" in view._renderer.text()


def test_login_altview_uses_shared_clipboard_helper():
    """Guards against re-introducing a duplicate copy implementation."""
    import plugins.altview.login_altview as mod
    from kollabor_tui.clipboard import copy_to_clipboard

    assert mod.copy_to_clipboard is copy_to_clipboard
