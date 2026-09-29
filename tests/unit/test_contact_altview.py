"""Knock review keeps keys and receipts off the screen; introductions are private."""

import re
import time

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.contact_altview import ContactReviewAltView, _safe_display_text
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


def _named(name: str) -> KeyPress:
    return KeyPress(name=name, code=0, char=None, type=KeyType.SPECIAL)


def _knock(name: str, receipt: str, key: str) -> PendingContactRequest:
    return PendingContactRequest(
        receipt * 32, key * 64, int(time.time()) + 600, PrivateMessage(f"hi from {name}"), name
    )


async def _open_two():
    eve, ana = _knock("eve-box", "1", "a"), _knock("ana-laptop", "2", "b")
    decisions = []
    renderer = _FakeRenderer()

    async def load():
        return [eve, ana]

    async def decide(request, decision):
        decisions.append((request.device_name, decision))

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)
    return view, renderer, decisions


@pytest.mark.asyncio
async def test_knock_review_hint_is_only_on_the_selected_row_and_a_acts_on_it():
    view, renderer, decisions = await _open_two()
    rows = [line for _, _, line in renderer.lines if "fingerprint" in line]
    assert [("[a]ccept" in row) for row in rows] == [True, False]
    assert rows[0].lstrip().startswith("> 1. eve-box")

    await view.handle_input(_named("ArrowDown"))
    await view.render_frame(0)
    rows = [line for _, _, line in renderer.lines if "fingerprint" in line]
    assert [("[a]ccept" in row) for row in rows] == [False, True]
    assert rows[1].lstrip().startswith("> 2. ana-laptop")

    await view.handle_input(_key("a"))

    assert decisions == [("ana-laptop", "accept")]  # the row the hint was on


@pytest.mark.asyncio
async def test_knock_review_a_held_key_cannot_decide_the_next_knock_unseen():
    view, renderer, decisions = await _open_two()

    await view.handle_input(_key("r"))
    await view.handle_input(_key("r"))  # buffered before the redraw shows Ana

    assert decisions == [("eve-box", "reject")]

    await view.render_frame(0)
    await view.handle_input(_key("r"))

    assert decisions == [("eve-box", "reject"), ("ana-laptop", "reject")]


@pytest.mark.asyncio
async def test_knock_review_row_flattens_tabs_so_it_stays_one_row():
    request = PendingContactRequest(
        "b" * 32, "c" * 64, int(time.time()) + 600, PrivateMessage("\t" * 300), "tabby"
    )
    renderer = _FakeRenderer(size=(80, 30))

    async def load():
        return [request]

    async def decide(_request, _decision):
        return None

    view = ContactReviewAltView("relay.example", load, decide)
    await view.on_enter(renderer)
    await view.render_frame(0)

    assert "\t" not in renderer.text()
    assert "[a]ccept" in renderer.text()


def test_safe_display_text_strips_escape_c1_bidi_and_other_controls():
    hostile = (
        "a\x1b[31mred\x1b[0m"  # ESC sequences lose their ESC
        "\x85b\x9bc"  # C1 controls (NEL, CSI)
        "\u202edcba\u2066x\u2069"  # bidi override and isolates
        "\u200bz\x00\x7f\tq"  # zero-width space, NUL, DEL, tab
        "\nr"
    )

    cleaned = _safe_display_text(hostile)

    assert cleaned == "a[31mred[0mbcdcbaxzq\nr"
    assert not any(ord(char) < 32 and char != "\n" for char in cleaned)
    assert not any(0x7F <= ord(char) <= 0x9F or char in "\u202e\u2066\u2069\u200b" for char in cleaned)
