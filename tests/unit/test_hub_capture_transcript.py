"""Readable, bounded rendering for hub transcript captures."""

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import kollabor.cli as cli_module
from kollabor.cli import _handle_cli_hub, _print_hub_help, parse_arguments
from kollabor_agent.tool_definitions.hub import hub_capture as hub_capture_tool
from plugins.hub.capture import (
    group_transcript_records,
    normalize_records,
    render_transcript,
)
from plugins.hub.plugin import HubPlugin


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
    raw = AsyncMock(return_value=(["raw terminal line"], None))
    legacy_wrapper = AsyncMock(return_value=["unexpected wrapper call"])

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
        patch("plugins.hub.messenger.AgentMessenger.request_output_diagnostic", raw),
        patch("plugins.hub.messenger.AgentMessenger.request_output", legacy_wrapper),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "7"]))

    capture.assert_awaited_once()
    assert capture.await_args.args == (socket_path,)
    assert capture.await_args.kwargs["lines"] == 7
    assert capture.await_args.kwargs["capture_format"] == "json"
    assert raw.await_count == 0

    assert legacy_wrapper.await_count == 0
    output = capsys.readouterr().out
    assert "User: Question red" in output
    assert "Assistant: Answer ready!" in output
    assert "Tool result [error] terminal: permission denied" in output
    assert "next_cursor=9" in output


def test_cli_capture_raw_keeps_the_legacy_terminal_dispatch(tmp_path, capsys):
    socket_path = _write_presence(tmp_path, "lapis")
    capture = AsyncMock(return_value=({}, None))
    raw = AsyncMock(return_value=(["\x1b[31mraw terminal\x1b[0m", "second line"], None))
    legacy_wrapper = AsyncMock(return_value=["unexpected wrapper call"])

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
        patch("plugins.hub.messenger.AgentMessenger.request_output_diagnostic", raw),
        patch("plugins.hub.messenger.AgentMessenger.request_output", legacy_wrapper),
    ):
        asyncio.run(_handle_cli_hub(["capture", "lapis", "--raw", "11"]))

    raw.assert_awaited_once_with(socket_path, lines=11)

    legacy_wrapper.assert_not_awaited()
    assert capture.await_count == 0
    assert capsys.readouterr().out == "\x1b[31mraw terminal\x1b[0m\nsecond line\n"


def test_cli_parser_routes_raw_to_hub_capture_only():
    parsed = parse_arguments(argv=["--hub", "capture", "lapis", "7", "--raw"])

    assert parsed.hub == ["capture", "lapis", "7", "--raw"]
    assert parsed._unknown_args == []

    with pytest.raises(SystemExit) as exc:
        parse_arguments(argv=["--raw"])
    assert exc.value.code == 2


@pytest.mark.parametrize("raw", [False, True])
def test_cli_main_parses_and_dispatches_capture_mode(
    raw, tmp_path, capsys, monkeypatch
):
    socket_path = _write_presence(tmp_path, "lapis")
    payload = {"lines": normalize_records(_events()), "truncated": False}
    capture = AsyncMock(return_value=(payload, None))
    raw_capture = AsyncMock(return_value=(["raw terminal line"], None))
    legacy_wrapper = AsyncMock(return_value=["unexpected wrapper call"])
    dispatched = []
    original_handler = cli_module._handle_cli_hub

    async def _dispatch(hub_args):
        dispatched.append(list(hub_args))
        await original_handler(hub_args)

    monkeypatch.setattr(cli_module, "_update_before_launch", lambda: None)
    monkeypatch.setattr(cli_module, "_should_use_daemon", lambda: False)
    monkeypatch.setattr(
        cli_module,
        "_run_async_main_with_sigterm_cleanup",
        lambda _detached: asyncio.run(cli_module.async_main()),
    )
    monkeypatch.setattr(cli_module, "setup_bootstrap_logging", lambda: None)
    monkeypatch.setattr(cli_module, "discover_plugin_args", lambda: ([], None))
    monkeypatch.setattr(cli_module, "handle_early_plugin_args", lambda *_args: False)
    monkeypatch.setattr(cli_module, "_handle_cli_hub", _dispatch)
    monkeypatch.setattr(sys, "argv", ["kollab", "--hub", "capture", "lapis", "7"])
    if raw:
        sys.argv.append("--raw")

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch("plugins.hub.messenger.AgentMessenger.request_capture", capture),
        patch(
            "plugins.hub.messenger.AgentMessenger.request_output_diagnostic",
            raw_capture,
        ),
        patch("plugins.hub.messenger.AgentMessenger.request_output", legacy_wrapper),
    ):
        cli_module.cli_main()

    expected = ["capture", "lapis", "7"] + (["--raw"] if raw else [])
    assert dispatched == [expected]
    if raw:
        raw_capture.assert_awaited_once_with(socket_path, lines=7)

        legacy_wrapper.assert_not_awaited()
        assert capture.await_count == 0
        assert capsys.readouterr().out == "raw terminal line\n"
    else:
        capture.assert_awaited_once()
        assert capture.await_args.kwargs["capture_format"] == "json"
        assert raw_capture.await_count == 0
        assert legacy_wrapper.await_count == 0
        assert "User: Question red" in capsys.readouterr().out


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


def test_grouped_transcript_replaces_stream_chunks_with_final_response():
    events = [
        {"seq": 1, "type": "user_message", "text": "question"},
        {
            "seq": 2,
            "type": "token",
            "text": "partial ",
            "request_id": "request-1",
            "turn_id": "turn-1",
        },
        {
            "seq": 3,
            "type": "token",
            "text": "answer",
            "request_id": "request-1",
            "turn_id": "turn-1",
        },
        {
            "seq": 4,
            "type": "response",
            "text": "clean final answer",
            "request_id": "request-1",
            "turn_id": "turn-1",
        },
        {
            "seq": 5,
            "type": "token",
            "text": "next ",
            "request_id": "request-2",
            "turn_id": "turn-2",
        },
        {
            "seq": 6,
            "type": "token",
            "text": "turn",
            "request_id": "request-2",
            "turn_id": "turn-2",
        },
    ]

    grouped = group_transcript_records(normalize_records(events))
    assistant = [record for record in grouped if record.get("role") == "assistant"]

    assert [
        (record["kind"], record["text"], record["seq"]) for record in assistant
    ] == [
        ("message", "clean final answer", 4),
        ("text", "next turn", 6),
    ]

    rendered = render_transcript({"lines": grouped})
    assert "User: question" in rendered
    assert "Assistant: clean final answer" in rendered
    assert "Assistant: next turn" in rendered
    assert "partial answer" not in rendered
    assert rendered.count("Assistant:") == 2


def test_hub_capture_limits_after_token_grouping_and_keeps_group_cursor():
    events = [
        {
            "seq": seq,
            "type": "token",
            "text": chunk,
            "request_id": "request-1",
            "turn_id": "turn-1",
        }
        for seq, chunk in ((1, "complete "), (2, "assistant "), (3, "answer"))
    ]
    plugin = object.__new__(HubPlugin)
    plugin._display_tap = SimpleNamespace(get_snapshot=lambda: events)

    page = plugin._get_output_lines(limit=1, format="json", max_chars=10_000)
    truncated_page = plugin._get_output_lines(limit=1, format="json", max_chars=10)

    assert len(page["lines"]) == 1
    assert page["lines"][0]["text"] == "complete assistant answer"
    assert page["lines"][0]["seq"] == 3
    assert truncated_page["truncated"] is True
    assert truncated_page["next_cursor"] == "3"


def test_hub_capture_reports_overwritten_display_ring_history():
    events = [
        {"seq": seq, "type": "status", "text": "working"} for seq in range(2, 202)
    ]
    plugin = object.__new__(HubPlugin)
    plugin._display_tap = SimpleNamespace(get_snapshot=lambda: events)

    page = plugin._get_output_lines(limit=50, format="json", max_chars=10_000)

    assert page["truncated"] is True
    assert page["next_cursor"] == str(page["lines"][-1]["seq"])


def test_cli_capture_raw_surfaces_diagnostic_errors(tmp_path, capsys):
    socket_path = _write_presence(tmp_path, "lapis")
    diagnostic = AsyncMock(return_value=([], "timeout after 5s"))
    legacy_wrapper = AsyncMock(return_value=None)

    with (
        patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path),
        patch(
            "plugins.hub.messenger.AgentMessenger.request_output_diagnostic", diagnostic
        ),
        patch("plugins.hub.messenger.AgentMessenger.request_output", legacy_wrapper),
    ):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(_handle_cli_hub(["capture", "lapis", "7", "--raw"]))

    assert exc.value.code == 1
    diagnostic.assert_awaited_once_with(socket_path, lines=7)
    legacy_wrapper.assert_not_awaited()
    assert "timeout after 5s" in capsys.readouterr().err


def test_stale_sequence_cursor_after_restart_is_reset_and_marked_truncated():
    events = [{"seq": 1, "type": "status", "text": "agent ready"}]
    plugin = object.__new__(HubPlugin)
    plugin._display_tap = SimpleNamespace(get_snapshot=lambda: events)

    page = plugin._get_output_lines(
        limit=1,
        format="json",
        max_chars=10_000,
        cursor="900",
    )

    assert page["lines"][0]["text"] == "agent ready"
    assert page["truncated"] is True
    assert page["next_cursor"] == "1"
    assert page["cursor_reset"] is True
    assert "capture window reset" in render_transcript(page, identity="lapis")


def test_cursor_page_returns_next_grouped_records_not_the_tail_window():
    events = [
        {"seq": seq, "type": "status", "text": f"status {seq}"} for seq in range(1, 101)
    ]
    plugin = object.__new__(HubPlugin)
    plugin._display_tap = SimpleNamespace(get_snapshot=lambda: events)

    page = plugin._get_output_lines(
        limit=50,
        format="json",
        max_chars=100_000,
        cursor=10,
    )

    assert [record["seq"] for record in page["lines"]] == list(range(11, 61))
    assert page["truncated"] is True
    assert page["next_cursor"] == "60"
    assert page.get("cursor_reset") is not True


def test_two_grouped_records_with_limit_one_report_omission():
    events = [
        {"seq": 1, "type": "status", "text": "first group"},
        {"seq": 2, "type": "status", "text": "second group"},
    ]
    plugin = object.__new__(HubPlugin)
    plugin._display_tap = SimpleNamespace(get_snapshot=lambda: events)

    page = plugin._get_output_lines(limit=1, format="json", max_chars=10_000)

    assert [record["seq"] for record in page["lines"]] == [2]
    assert page["truncated"] is True
    assert page["next_cursor"] == "2"
    assert page.get("cursor_reset") is not True


def test_cursor_older_than_retained_ring_resets_with_usable_cursor():
    events = [
        {"seq": seq, "type": "status", "text": f"retained {seq}"}
        for seq in range(51, 151)
    ]
    plugin = object.__new__(HubPlugin)
    plugin._display_tap = SimpleNamespace(get_snapshot=lambda: events)

    page = plugin._get_output_lines(
        limit=50,
        format="json",
        max_chars=100_000,
        cursor=10,
    )

    assert [record["seq"] for record in page["lines"]] == list(range(51, 101))
    assert page["truncated"] is True
    assert page["cursor_reset"] is True
    assert page["next_cursor"] == "100"
