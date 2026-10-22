"""Readable, bounded rendering for hub transcript captures."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kollabor.cli import _handle_cli_hub, _print_hub_help, parse_arguments
from kollabor_agent.tool_definitions.hub import hub_capture as hub_capture_tool
from plugins.hub.capture import normalize_records, render_transcript


def _events():
    return [
        {
            "type": "user_message",
            "text": "old\rQuestion\x1b[31m red\x1b[0m\x1b]0;title\x07",
        },
        {"type": "token", "text": "Answer "},
        {"type": "token", "text": "ready\x1b[32m!\x1b[0m"},
        {
            "type": "tool_start",
            "tool_name": "\x1b[32mfile_read\x1b[0m",
            "input": {"path": "/private/path", "token": "must not render"},
        },
        {
            "type": "tool_result",
            "tool_name": "\x1b[32mfile_read\x1b[0m",
            "success": True,
            "output": "read\x1b[31m ok\x1b[0m",
        },
        {
            "type": "tool_result",
            "tool_name": "terminal",
            "success": False,
            "error": "permission denied",
        },
        {"type": "status", "message": "waiting for reply"},
        {"type": "error", "message": "connection failed"},
        {
            "type": "thinking",
            "role": "assistant",
            "text": "private reasoning must not render",
        },
    ]


def test_transcript_labels_messages_tools_status_and_errors_without_private_args():
    payload = {"lines": normalize_records(_events()), "truncated": False}

    rendered = render_transcript(payload, identity="lapis")

    assert "User: Question red" in rendered
    assert "Assistant: Answer ready!" in rendered
    assert "Tool call: file_read" in rendered
    assert "Tool result [ok] file_read: read ok" in rendered
    assert "Tool result [error] terminal: permission denied" in rendered
    assert "Status: waiting for reply" in rendered
    assert "Error: connection failed" in rendered
    assert "private/path" not in rendered
    assert "must not render" not in rendered
    assert "private reasoning" not in rendered
    assert "\x1b" not in rendered
    assert "title" not in rendered


def test_transcript_preserves_pagination_and_bounds_output_lines_and_characters():
    payload = {
        "lines": normalize_records(
            [
                {"type": "user_message", "text": "x" * 200},
                {"type": "token", "text": "y" * 200},
            ]
        ),
        "truncated": True,
        "next_cursor": "24",
    }

    rendered = render_transcript(payload, identity="lapis", max_lines=3, max_chars=72)

    assert len(rendered) <= 72
    assert len(rendered.splitlines()) <= 3
    assert "truncated" in rendered
    assert "next_cursor=24" in rendered


def test_single_line_truncation_keeps_a_content_prefix_and_marker():
    payload = {
        "lines": normalize_records(
            [
                {"type": "user_message", "text": "question"},
                {"type": "token", "text": "answer"},
            ]
        ),
        "truncated": True,
    }

    rendered = render_transcript(payload, max_lines=1, max_chars=72)

    assert len(rendered.splitlines()) == 1
    assert rendered.startswith("User: question")
    assert "truncated" in rendered


def test_transcript_sanitizes_legacy_plain_and_bare_line_fallbacks():
    assert (
        render_transcript({"text": "old\rnew\x1b[31m red\x1b[0m"}, identity="lapis")
        == "new red"
    )
    assert (
        render_transcript(
            {"lines": ["\x1b]0;title\x07old\rnew", "second"]}, identity="lapis"
        )
        == "new\nsecond"
    )


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


def test_cli_capture_defaults_to_transcript_json_dispatch(tmp_path, capsys):
    socket_path = _write_presence(tmp_path, "lapis")
    payload = {
        "lines": normalize_records(_events()),
        "truncated": True,
        "next_cursor": "9",
    }
    capture = AsyncMock(return_value=(payload, None))
    raw = AsyncMock(return_value=["raw terminal line"])

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
        patch("plugins.hub.messenger.AgentMessenger.request_output", raw),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "7"]))

    capture.assert_awaited_once()
    assert capture.await_args.args == (socket_path,)
    assert capture.await_args.kwargs["lines"] == 7
    assert capture.await_args.kwargs["capture_format"] == "json"
    assert raw.await_count == 0
    output = capsys.readouterr().out
    assert "User: Question red" in output
    assert "Assistant: Answer ready!" in output
    assert "Tool result [error] terminal: permission denied" in output
    assert "next_cursor=9" in output


def test_cli_capture_raw_keeps_the_legacy_terminal_dispatch(tmp_path, capsys):
    socket_path = _write_presence(tmp_path, "lapis")
    capture = AsyncMock(return_value=({}, None))
    raw = AsyncMock(return_value=["\x1b[31mraw terminal\x1b[0m", "second line"])

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
        patch("plugins.hub.messenger.AgentMessenger.request_output", raw),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "--raw", "11"]))

    raw.assert_awaited_once_with(socket_path, lines=11)
    assert capture.await_count == 0
    assert capsys.readouterr().out == "\x1b[31mraw terminal\x1b[0m\nsecond line\n"


def test_cli_parser_routes_raw_to_hub_capture_only():
    parsed = parse_arguments(argv=["--hub", "capture", "lapis", "7", "--raw"])

    assert parsed.hub == ["capture", "lapis", "7", "--raw"]
    assert parsed._unknown_args == []

    with pytest.raises(SystemExit) as exc:
        parse_arguments(argv=["--raw"])
    assert exc.value.code == 2


def test_cli_capture_usage_mentions_raw(capsys):
    try:
        asyncio.run(_handle_cli_hub(["capture"]))
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("capture without an identity must exit with usage")

    assert "kollab --hub capture <identity> [lines] [--raw]" in capsys.readouterr().out


def test_cli_capture_reports_transport_failure_actionably(tmp_path, capsys):
    _write_presence(tmp_path, "lapis")
    capture = AsyncMock(return_value=(None, "connection timed out"))

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
    ):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(_handle_cli_hub(["capture", "lapis"]))

    assert exc.value.code == 1
    assert "capture failed for 'lapis': connection timed out" in capsys.readouterr().err


def test_cli_help_mentions_raw_capture(capsys):
    _print_hub_help()

    assert "capture <name> [lines] [--raw]" in capsys.readouterr().out


def test_transcript_degrades_malformed_record_text_without_crashing():
    rendered = render_transcript(
        {
            "lines": [
                {
                    "event_type": "error",
                    "kind": "error",
                    "text": {"message": "malformed but readable"},
                    "payload": {},
                }
            ]
        }
    )

    assert 'Error: {"message": "malformed but readable"}' == rendered


def test_hub_capture_tool_schema_documents_transcript_default():
    format_parameter = next(
        parameter
        for parameter in hub_capture_tool.parameters
        if parameter.name == "format"
    )

    assert "readable transcript" in hub_capture_tool.description
    assert "'transcript' (default)" in format_parameter.description
    assert "'plain'" in format_parameter.description
    assert "'terminal'" in format_parameter.description
