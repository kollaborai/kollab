"""A display event past the engine's frame limit is cut, not dropped (#544).

The engine reads each daemon line up to kollabor_rpc.transport.LARGE_BUFFER_LIMIT
(16 MiB). A tool result or ACP update longer than that reached it as a line the
engine drops, so its chat said the reply was incomplete. These tests publish such
events through the real DisplayTap, encode them as the attach stream does, and
read them with the engine's own reader over a real unix socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from kollabor_rpc.transport import (
    LARGE_BUFFER_LIMIT,
    open_unix_connection_with_large_buffer,
)
from kollabor_tui.display_tap import DisplayTap, publish_semantic


async def _read_as_the_engine_does(line: bytes) -> dict:
    """Serve ``line`` on a unix socket and read it with the engine's reader."""

    async def daemon(_reader, writer):
        writer.write(line)
        await writer.drain()
        await _reader.read()  # until the engine hangs up
        writer.close()

    path = os.path.join(tempfile.mkdtemp(prefix="kollab-544-"), "attach.sock")
    server = await asyncio.start_unix_server(daemon, path)
    try:
        reader, writer = await open_unix_connection_with_large_buffer(path)
        try:
            raw = await reader.readuntil(b"\n")
        finally:
            writer.close()
    finally:
        server.close()
    return json.loads(raw)


def _attach_line(event: dict) -> bytes:
    # The attach stream writes each event this way (messenger.py _send_events).
    return (json.dumps(event, default=str) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_a_tool_result_past_the_frame_limit_still_reaches_the_engine():
    tap = DisplayTap()
    live = tap.subscribe("attach")
    output = "x" * (20 * 1024 * 1024)  # a 20 MB tool output
    # ACP passthrough also copies the raw payload into metadata.
    publish_semantic(
        tap,
        "tool_result",
        tool_id="t1",
        tool_name="shell",
        output=output,
        metadata={"acp": {"rawOutput": output}},
    )

    frame = await _read_as_the_engine_does(_attach_line(live.get_nowait()))

    assert frame["type"] == "tool_result"
    assert frame["tool_id"] == "t1"
    assert frame["output"].startswith("x")
    assert frame["output"].endswith("x")
    assert "(20,971,520 characters)" in frame["output"]  # the cut says what it cut


def test_an_event_within_the_budget_is_published_unchanged():
    tap = DisplayTap()
    live = tap.subscribe("attach")
    output = "y" * (4 * 1024 * 1024)  # a 4 MB output: under the budget
    publish_semantic(tap, "tool_result", tool_id="t2", output=output)

    assert live.get_nowait()["output"] == output


@pytest.mark.asyncio
async def test_many_large_strings_are_cut_until_the_line_fits():
    tap = DisplayTap()
    live = tap.subscribe("attach")
    chunk = "z" * (2 * 1024 * 1024)  # 16 blocks of 2 MB: 32 MB of text
    publish_semantic(
        tap,
        "acp_update",
        update={"content": [{"type": "text", "text": chunk} for _ in range(16)]},
    )

    line = _attach_line(live.get_nowait())
    assert len(line) <= LARGE_BUFFER_LIMIT

    frame = await _read_as_the_engine_does(line)
    blocks = frame["update"]["content"]
    assert [b["type"] for b in blocks] == ["text"] * 16
    assert all("characters)" in b["text"] for b in blocks)


def _many_short_strings_event(tap: DisplayTap, output: str) -> dict:
    """A completed ACP tool call whose raw output is a long list of short strings.

    The list is what keeps the event over the budget, so cutting ``output``
    cannot make it fit (a rawOutput of ``{"lines": [...]}`` in the review of #810).
    """
    live = tap.subscribe("attach")
    lines = ["abcdefgh"] * 1_800_000  # about 21 MB once encoded
    publish_semantic(
        tap,
        "tool_result",
        tool_id="t3",
        output=output,
        metadata={"acp": {"rawOutput": {"lines": lines}}},
    )
    return live.get_nowait()


def test_an_event_that_cutting_text_cannot_fit_keeps_its_text():
    tap = DisplayTap()
    output = "o" * (2 * 1024 * 1024)

    published = _many_short_strings_event(tap, output)

    # Still over the limit either way, so the engine drops it as on main; the
    # text must not be thrown away for a cut that cannot rescue the frame.
    assert published["output"] == output


def test_an_event_that_cannot_fit_is_not_encoded_again_and_again(monkeypatch):
    import kollabor_tui.display_tap as display_tap

    calls = 0
    real_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(display_tap.json, "dumps", counting_dumps)

    _many_short_strings_event(DisplayTap(), "o" * (2 * 1024 * 1024))

    # One encode to measure and one for the single cut: not a halving loop
    # of a dozen full copies on the publishing thread.
    assert calls <= 3
