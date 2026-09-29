"""Knock review keeps keys and receipts off the screen; introductions are private."""

import re
import time

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.contact_altview import ContactReviewAltView
from plugins.hub.contact_requests import PendingContactRequest, PrivateMessage
from plugins.hub.device_names import device_key_fingerprint

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


@pytest.mark.asyncio
async def test_knock_review_row_has_name_fingerprint_and_no_key_or_receipt():
    introduction = "Ana from Webceive."
    sender_key = "c" * 64
    request = PendingContactRequest(
        "b" * 32,
        sender_key,
        int(time.time()) + 600,
        PrivateMessage(introduction),
        "ana-laptop",
    )
    renderer = _FakeRenderer()

    async def load():
        return [request]

    async def decide(_request, _decision):
        raise AssertionError("not exercised in this test")

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)

    text = renderer.text()
    assert "ana-laptop" in text
    assert introduction in text
    full = device_key_fingerprint(sender_key)
    assert f"fingerprint {full[:4]}\u2026{full[-4:]}" in text
    assert full[:12] not in text
    assert "[a]ccept" in text and "[r]eject" in text
    assert sender_key not in text
    assert "ed25519:" not in text
    assert "receipt" not in text.lower()
    assert request.receipt_id not in text


@pytest.mark.asyncio
async def test_knock_review_row_truncates_introduction_to_fit_width():
    introduction = "x" * 500
    request = PendingContactRequest(
        "b" * 32, "c" * 64, int(time.time()) + 600, PrivateMessage(introduction), "wide"
    )
    renderer = _FakeRenderer(size=(60, 30))

    async def load():
        return [request]

    async def decide(_request, _decision):
        raise AssertionError("not exercised in this test")

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)

    text = renderer.text()
    assert "…" in text
    assert introduction not in text


@pytest.mark.asyncio
async def test_knock_review_accept_passes_full_request_and_reports_by_name():
    introduction = "A human-to-human contact introduction."
    private = PrivateMessage(introduction)
    request = PendingContactRequest(
        "b" * 32, "c" * 64, int(time.time()) + 600, private, "ana-laptop"
    )
    decisions = []
    renderer = _FakeRenderer()

    async def load():
        return [request]

    async def decide(request_arg, decision):
        decisions.append((request_arg, decision))

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)

    await view.handle_input(_key("a"))
    await view.render_frame(0)

    assert decisions == [(request, "accept")]
    with pytest.raises(RuntimeError, match="cleared"):
        private.reveal()
    text = renderer.text()
    assert introduction not in text
    assert (
        "accepted ana-laptop. it is a peer with agents trust; nothing is allowed "
        "until /connect allow ana-laptop <agent>."
    ) in " ".join(text.split())
    assert "b" * 32 not in text


@pytest.mark.asyncio
async def test_knock_review_reject_reports_by_name():
    request = PendingContactRequest(
        "b" * 32,
        "c" * 64,
        int(time.time()) + 600,
        PrivateMessage("hello"),
        "ana-laptop",
    )
    renderer = _FakeRenderer()

    async def load():
        return [request]

    async def decide(_request, _decision):
        return None

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)

    await view.handle_input(_key("r"))
    await view.render_frame(0)

    assert "rejected ana-laptop." in renderer.text()


@pytest.mark.asyncio
async def test_knock_review_failed_accept_names_the_device_and_keeps_the_knock():
    request = PendingContactRequest(
        "b" * 32, "c" * 64, int(time.time()) + 600, PrivateMessage("hello"), "ana-laptop"
    )
    renderer = _FakeRenderer()

    async def load():
        return [request]

    async def decide(_request, _decision):
        return "that device name is already on this network"

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)

    await view.handle_input(_key("a"))
    await view.render_frame(0)

    text = renderer.text()
    assert "could not accept ana-laptop: that device name is already on this network" in text
    assert "hello" in text  # still pending, still readable
    assert "accepted" not in text
