"""Private entry and human review keep contact introductions off chat output."""

import re
import time

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.contact_altview import (
    ContactRequestAltView,
    ContactReviewAltView,
    ContactSubmissionOutcome,
)
from plugins.hub.contact_requests import PendingContactRequest, PrivateMessage

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class _FakeRenderer:
    def __init__(self, size=(100, 30)):
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


def _key(char: str) -> KeyPress:
    return KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)


def _named(name: str) -> KeyPress:
    return KeyPress(name=name, code=0, char=None, type=KeyType.SPECIAL)


@pytest.mark.asyncio
async def test_contact_entry_clears_private_introduction_and_returns_receipt_only():
    captured = {}
    introduction = "Please contact me about a project."
    recipient_key = "1" * 64

    async def submit(value):
        captured["submission"] = value
        captured["repr"] = repr(value)
        captured["message"] = value.introduction
        captured["plain"] = value.introduction.reveal()
        return ContactSubmissionOutcome("a" * 32)

    view = ContactRequestAltView("relay.example", on_submit=submit)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)

    view._focus = "key"
    for char in recipient_key:
        await view.handle_input(_key(char))
    view._focus = "intro"
    for char in introduction:
        await view.handle_input(_key(char))
    await view.render_frame(0)
    assert introduction in renderer.text()

    await view.handle_input(_named("Enter"))
    await view.render_frame(0)

    assert captured["plain"] == introduction
    assert introduction not in captured["repr"]
    assert "<redacted>" in repr(captured["message"])
    with pytest.raises(RuntimeError, match="cleared"):
        captured["message"].reveal()
    assert view._intro_chars == []
    assert view._receipt_id == "a" * 32
    assert introduction not in renderer.text()
    assert "Receipt: " + "a" * 32 in renderer.text()


@pytest.mark.asyncio
async def test_contact_request_cancel_wipes_intro_without_callback():
    called = False
    view = ContactRequestAltView(
        "relay.example", on_submit=lambda _value: _unexpected_callback()
    )
    await view.on_enter(_FakeRenderer())
    view._intro_chars.extend(list("private introduction"))
    view._cursor["intro"] = len(view._intro_chars)

    assert await view.handle_input(_named("Escape")) is True
    assert view._intro_chars == []
    assert view.cancelled


async def _unexpected_callback():
    raise AssertionError("cancelled request must not submit")


@pytest.mark.asyncio
async def test_contact_review_accepts_only_the_selected_receipt_without_model_or_grant():
    introduction = "A human-to-human contact introduction."
    private = PrivateMessage(introduction)
    request = PendingContactRequest("b" * 32, "c" * 64, int(time.time()) + 600, private)
    decisions = []
    renderer = _FakeRenderer()

    async def load():
        return [request]

    async def decide(receipt, decision):
        decisions.append((receipt, decision))

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)

    assert introduction in renderer.text()
    assert (
        "No membership, workspace/tool grant or model run is created."
        in renderer.text()
    )

    await view.handle_input(_key("a"))
    await view.render_frame(0)

    assert decisions == [("b" * 32, "accept")]
    with pytest.raises(RuntimeError, match="cleared"):
        private.reveal()
    assert introduction not in renderer.text()
    assert "accepted" in renderer.text()
