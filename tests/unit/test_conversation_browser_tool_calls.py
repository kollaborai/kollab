"""#25: the /conversations browser preview must show an assistant turn's tool calls.

The loader used to flatten a `[text, tool_use, ...]` content list to its first
block's text, so the browser never saw a tool call. The calls now ride beside
the message as display-only `tool_uses`; `content` and `metadata` stay exactly
what resume hands to the model.
"""

import json
from pathlib import Path

from kollabor_ai.conversation_manager import ConversationManager
from plugins.altview.conversations_altview import ConversationsAltView

SESSION_ID = "2610-tool-preview"


class MockConfig:
    def get(self, key, default=None):
        return default


def _record(kind: str, uuid: str, content, **extra) -> str:
    return json.dumps(
        {
            "type": kind,
            "uuid": uuid,
            "timestamp": f"2026-10-10T00:00:{uuid[-1]}0Z",
            "message": {"role": kind, "content": content},
            **extra,
        }
    )


def _manager(tmp_path: Path) -> ConversationManager:
    lines = [
        _record("user", "u1", "list the files"),
        _record(
            "assistant",
            "a2",
            [
                {"type": "text", "text": "Checking the directory."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "bash",
                    "input": {"command": "ls -la"},
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "file_read",
                    "input": {"path": "x" * 300},
                },
            ],
            provider_reasoning={"kept": False},
        ),
        _record(
            "assistant",
            "a3",
            [{"type": "tool_use", "id": "t3", "name": "hub_msg", "input": "hi"}],
        ),
        _record(
            "assistant",
            "a4",
            [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}],
        ),
    ]
    (tmp_path / f"{SESSION_ID}.jsonl").write_text("\n".join(lines) + "\n")
    config = MockConfig()
    config._conversations_dir = tmp_path
    return ConversationManager(config)


def test_loader_keeps_model_facing_fields_clean(tmp_path):
    mgr = _manager(tmp_path)
    msgs = mgr._load_from_jsonl(tmp_path / f"{SESSION_ID}.jsonl")["messages"]

    assert [m["content"] for m in msgs] == [
        "list the files",
        "Checking the directory.",
        "",
        "first\nsecond",
    ]
    # tool calls never reach content or metadata (resume feeds both to the model)
    assert all("[tool" not in m["content"] for m in msgs)
    assert all("tool_calls" not in m["metadata"] for m in msgs)
    assert msgs[1]["metadata"] == {}  # reasoning is not restored for a tool_use turn
    assert [[b["name"] for b in m.get("tool_uses", [])] for m in msgs] == [
        [],
        ["bash", "file_read"],
        ["hub_msg"],
        [],
    ]


def test_browser_rows_show_tool_calls(tmp_path):
    rows = _manager(tmp_path).get_session_messages(SESSION_ID)

    assert [r["role"] for r in rows] == ["user", "assistant", "assistant", "assistant"]
    assert rows[0]["full_content"] == "list the files"

    lines = rows[1]["full_content"].split("\n")
    assert lines[0] == "Checking the directory."
    assert lines[1] == '[tool: bash] {"command": "ls -la"}'
    assert (
        lines[2].startswith("[tool: file_read] ")
        and len(lines[2]) == 120
        and lines[2].endswith("...")
    )
    assert rows[1]["preview"] == "Checking the directory."

    # a tool-only turn used to render as an empty row
    assert rows[2]["preview"] == "[tool: hub_msg] hi"
    assert rows[2]["full_content"] == "[tool: hub_msg] hi"


def test_the_pane_gives_each_line_its_own_row(tmp_path):
    # wrap_text wraps one line: a "\n" left inside a row is overdrawn by the row
    # below it, which hid the tool line (and every line after a message's first)
    view = ConversationsAltView()
    view.messages = _manager(tmp_path).get_session_messages(SESSION_ID)

    rows = [r["content"] for r in view._build_message_lines(80)]

    assert all("\n" not in r for r in rows)
    text = rows.index("  Checking the directory.")
    assert rows[text + 1] == '  [tool: bash] {"command": "ls -la"}'
    assert rows[rows.index("  first") + 1] == "  second"
