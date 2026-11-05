"""CLI capture cursor regression coverage for #752.

`kollab --hub capture` ends long windows with `... (truncated,
next_cursor=N)`, but the CLI had no way to pass N back: the flag parser
stripped `--raw` and treated every remaining token as the line count, so
`--cursor 49620` died in the usage check. These tests pin the flag path.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kollabor.cli import _handle_cli_hub


def _write_presence(path: Path, identity: str) -> str:
    socket_path = path / f"{identity}.sock"
    (path / f"{identity}.json").write_text(
        json.dumps(
            {
                "identity": identity,
                "pid": os.getpid(),
                "last_heartbeat": __import__("time").time(),
                "socket_path": str(socket_path),
                "is_coordinator": False,
            }
        )
    )
    return str(socket_path)


def _capture_envelope(**overrides):
    envelope = {
        "type": "output",
        "format": "json",
        "order": "oldest-first",
        "lines": [
            {"seq": 3, "kind": "text", "text": "continuation after the cursor"}
        ],
        "truncated": False,
        "next_cursor": None,
    }
    envelope.update(overrides)
    return envelope


def test_cli_capture_accepts_cursor_flag(tmp_path, capsys):
    _write_presence(tmp_path, "lapis")
    capture = AsyncMock(return_value=(_capture_envelope(), None))

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "10", "--cursor", "49620"]))

    capture.assert_awaited_once()
    assert capture.await_args.kwargs.get("cursor") == "49620"
    assert capture.await_args.kwargs.get("lines") == 10
    out = capsys.readouterr().out
    assert "continuation after the cursor" in out


def test_cli_capture_cursor_flag_works_without_line_count(tmp_path, capsys):
    _write_presence(tmp_path, "lapis")
    capture = AsyncMock(return_value=(_capture_envelope(), None))

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "--cursor", "7"]))

    capture.assert_awaited_once()
    assert capture.await_args.kwargs.get("cursor") == "7"
    assert capture.await_args.kwargs.get("lines") == 50


def test_cli_capture_cursor_follow_follows_truncated_marker(tmp_path, capsys):
    """The issue's scripted-polling story: follow the marker to the end."""
    _write_presence(tmp_path, "lapis")

    pages = [
        (
            _capture_envelope(
                lines=[{"seq": 1, "kind": "text", "text": "first page"}],
                truncated=True,
                next_cursor="1",
            ),
            None,
        ),
        (
            _capture_envelope(
                lines=[{"seq": 2, "kind": "text", "text": "second page"}],
                truncated=True,
                next_cursor="2",
            ),
            None,
        ),
        (
            _capture_envelope(
                lines=[{"seq": 3, "kind": "text", "text": "final page"}],
                truncated=False,
                next_cursor=None,
            ),
            None,
        ),
    ]
    cursors_seen = []

    async def follow(socket_path_arg, *, lines, cursor=None, **kwargs):
        cursors_seen.append(cursor)
        return pages[len(cursors_seen) - 1]

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch(
            "plugins.hub.messenger.AgentMessenger.request_capture", side_effect=follow
        ),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "--follow"]))

    assert cursors_seen == [None, "1", "2"]
    out = capsys.readouterr().out
    assert "first page" in out and "second page" in out and "final page" in out
    # Intermediate pages legitimately show their then-current marker; the
    # FINAL page must not end with a marker (the window is complete).
    assert not out.rstrip().endswith(")")
    assert "next_cursor=2" not in out.split("final page")[1]


def test_cli_capture_cursor_follow_refuses_safety_loop(tmp_path, capsys):
    """A stuck next_cursor must not spin forever (sweep-class guard)."""
    _write_presence(tmp_path, "lapis")

    async def stuck(socket_path_arg, *, lines, cursor=None, **kwargs):
        return (
            _capture_envelope(
                lines=[{"seq": 1, "kind": "text", "text": "stuck page"}],
                truncated=True,
                next_cursor="1",
            ),
            None,
        )

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", side_effect=stuck),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "--follow"]))

    out = capsys.readouterr().out
    # The stuck page is fetched once, printed once; the loop must then stop
    # with an explicit stop reason rather than spin.
    assert out.count("stuck page") == 1
    assert "next_cursor=1" in out
    assert "peer did not advance" in out


def test_cli_capture_bad_cursor_value_is_a_usage_error(tmp_path, capsys):
    _write_presence(tmp_path, "lapis")
    capture = AsyncMock()

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
    ):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(_handle_cli_hub(["capture", "lapis", "--cursor", "abc"]))

    assert exc.value.code == 2
    capture.assert_not_awaited()


def test_cli_capture_rejects_positional_after_cursor_value(tmp_path):
    _write_presence(tmp_path, "lapis")
    capture = AsyncMock()

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
    ):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(
                _handle_cli_hub(["capture", "lapis", "10", "--cursor", "7", "junk"])
            )

    assert exc.value.code == 2
    capture.assert_not_awaited()
