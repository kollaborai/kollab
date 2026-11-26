"""Persisted model context is not part of the visible chat transcript."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from kollabor.llm.agent_hud import (
    AgentHudEntry,
    agent_hud_hub_metadata,
    build_hub_note,
    format_agent_hud,
)
from kollabor.state.chat_stream import ChatStreamService
from kollabor.state.local import LocalStateService
from kollabor_events.data_models import ConversationMessage
from kollabor_tui.display_tap import DisplayTap


class _State:
    def __init__(self, history):
        self._event_bus = SimpleNamespace(get_service=lambda _name: None)
        self._llm_service = SimpleNamespace(conversation_history=history)

    def get_conversation_uid(self) -> str:
        return "conversation-projection"


def test_system_initialization_prompt_is_not_projected_as_a_chat_row():
    state = _State(
        [
            ConversationMessage(
                role="system",
                content="private system instructions that must not be displayed",
                metadata={"system_initialization": True},
            )
        ]
    )

    snapshot = ChatStreamService(state).subscribe({})["events"][0]

    assert snapshot["data"]["rows"] == []


def _hub_entry(message_id: str, content: str) -> AgentHudEntry:
    message = SimpleNamespace(
        from_identity=f"agent-{message_id}",
        to="operator",
        scope="direct",
        content=content,
        metadata={},
        thread_id=None,
        reply_to=None,
        type="message",
    )
    return AgentHudEntry(
        section="hub",
        label="incoming",
        content=content,
        message_id=message_id,
        hub_note=build_hub_note(message, observed=True),
        hub_admission="note",
    )


def test_internal_tool_batches_and_hud_text_are_not_transcript_rows():
    entries = [_hub_entry("hub-message-1", "First note"), _hub_entry("hub-message-2", "Second note")]
    status_only = AgentHudEntry("queue", "status", "private worker status")
    history = [
        ConversationMessage(
            role="user",
            content="Tool result: private output",
            metadata={"tool_output_batch": True, "xml_tool_results": []},
        ),
        ConversationMessage(
            role="user",
            content=format_agent_hud([status_only]),
            metadata={"agent_hud": True},
        ),
        ConversationMessage(
            role="user",
            content=format_agent_hud(entries),
            metadata={"agent_hud": True, **agent_hud_hub_metadata(entries)},
        ),
    ]

    rows = ChatStreamService(_State(history)).subscribe({})["events"][0]["data"]["rows"]

    assert [row["id"] for row in rows] == ["hub-message-1", "hub-message-2"]
    assert all(row["kind"] == "note" for row in rows)
    assert [row["parts"][0]["text"] for row in rows] == ["First note", "Second note"]
    assert all(
        "<agent_hud>" not in part.get("text", "")
        for row in rows
        for part in row["parts"]
    )


def test_compaction_snapshot_and_replace_use_one_stable_public_marker():
    state = _State(
        [
            ConversationMessage(
                role="user",
                content="Previous Context Summary: private details from old history",
                timestamp="2026-11-25T12:00:00Z",
                metadata={"context_compaction": True, "compaction_id": "compact-1"},
            )
        ]
    )
    stream = ChatStreamService(state)

    snapshot_rows = stream.subscribe({})["events"][0]["data"]["rows"]
    replacement = stream.replace_transcript()
    replacement_rows = replacement["data"]["rows"]
    repeated_replacement = stream.replace_transcript()
    repeated_rows = repeated_replacement["data"]["rows"]

    for rows in (snapshot_rows, replacement_rows, repeated_rows):
        markers = [row for row in rows if row["kind"] == "marker"]
        assert len(markers) == 1
        assert markers[0]["parts"] == [
            {"type": "text", "text": "Earlier messages compacted"}
        ]
    assert replacement_rows[0]["id"] == snapshot_rows[0]["id"]
    assert repeated_rows[0]["id"] == snapshot_rows[0]["id"]


def test_native_tool_history_reloads_as_distinct_reply_segments_and_one_call_row():
    uid = "conversation-projection"
    turn_id = "native-turn-1"
    history = [
        ConversationMessage(
            role="user",
            content="Read the file",
            metadata={
                "request_id": "web-native-tool-1",
                "request_ids": ["web-native-tool-1"],
                "turn_id": turn_id,
            },
        ),
        ConversationMessage(
            role="assistant",
            content="I will inspect it.",
            metadata={
                "turn_id": turn_id,
                "tool_calls": [
                    {
                        "id": "call-read-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        },
                    }
                ],
            },
        ),
        ConversationMessage(
            role="tool",
            content="file contents",
            metadata={"tool_call_id": "call-read-1", "is_error": False},
        ),
        ConversationMessage(
            role="assistant",
            content="The file says hello.",
            metadata={"turn_id": turn_id},
        ),
    ]

    rows = ChatStreamService(_State(history)).subscribe({})["events"][0]["data"]["rows"]
    replies = [row for row in rows if row["kind"] == "reply"]
    tools = [row for row in rows if row["kind"] == "tool"]
    base_identity = f"{uid}:turn:{turn_id}"
    expected_reply_ids = [
        f"reply-{uuid.uuid5(uuid.NAMESPACE_URL, base_identity).hex}",
        f"reply-{uuid.uuid5(uuid.NAMESPACE_URL, f'{base_identity}:segment:1').hex}",
    ]

    assert [row["id"] for row in replies] == expected_reply_ids
    assert len(tools) == 1
    assert tools[0]["parts"] == [
        {
            "type": "tool-call",
            "tool_call_id": "call-read-1",
            "tool_name": "read_file",
            "args": {"path": "README.md"},
            "result": "file contents",
            "is_error": False,
        }
    ]
    assert len(rows) == 4


def test_native_tool_first_turn_does_not_consume_an_empty_reply_segment():
    uid = "conversation-projection"
    turn_id = "native-tool-first"
    history = [
        ConversationMessage(
            role="assistant",
            content="",
            metadata={
                "turn_id": turn_id,
                "tool_calls": [
                    {
                        "id": "call-first-1",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"query":"weather"}',
                        },
                    }
                ],
            },
        ),
        ConversationMessage(
            role="tool",
            content="sunny",
            metadata={"tool_call_id": "call-first-1", "is_error": False},
        ),
        ConversationMessage(
            role="assistant",
            content="The forecast is sunny.",
            metadata={"turn_id": turn_id},
        ),
    ]

    rows = ChatStreamService(_State(history)).subscribe({})["events"][0]["data"]["rows"]
    replies = [row for row in rows if row["kind"] == "reply"]
    expected_id = ChatStreamService._reply_row_id(uid, turn_id)

    assert len(replies) == 1
    assert replies[0]["id"] == expected_id
    assert replies[0]["parts"] == [
        {"type": "text", "text": "The forecast is sunny."}
    ]


def test_reused_native_tool_call_id_keeps_each_turns_own_result_and_status():
    history = []
    for turn_id, result, is_error in (
        ("native-tool-turn-1", "first result", False),
        ("native-tool-turn-2", "second result", True),
    ):
        history.extend(
            [
                ConversationMessage(
                    role="assistant",
                    content="Calling tool.",
                    metadata={
                        "turn_id": turn_id,
                        "tool_calls": [
                            {
                                "id": "reused-call-id",
                                "type": "function",
                                "function": {
                                    "name": "inspect",
                                    "arguments": '{"target":"item"}',
                                },
                            }
                        ],
                    },
                ),
                ConversationMessage(
                    role="tool",
                    content=result,
                    metadata={"tool_call_id": "reused-call-id", "is_error": is_error},
                ),
            ]
        )

    rows = ChatStreamService(_State(history)).subscribe({})["events"][0]["data"]["rows"]
    tools = [row for row in rows if row["kind"] == "tool"]

    assert [row["parts"][0]["result"] for row in tools] == [
        "first result",
        "second result",
    ]
    assert [row["parts"][0]["is_error"] for row in tools] == [False, True]


@pytest.mark.asyncio
async def test_private_goal_command_history_reuses_the_client_input_id():
    uid = "conversation-projection"
    input_id = "web-goal-input-1"
    reply_id = "goal-" + "a" * 32
    history = []

    def add_history(role, content, metadata=None, **_kwargs):
        history.append(
            ConversationMessage(role=role, content=content, metadata=metadata or {})
        )

    class Runtime:
        async def input_scope(self, _uid):
            return ("scope", "conversation-projection", "principal")

        async def add_input(self, params):
            return {"accepted": True, "id": params["id"], "state": "queued"}

    class Bus:
        def __init__(self, services):
            self.services = services

        def get_service(self, name):
            return self.services.get(name)

        def register_service(self, name, service):
            self.services[name] = service

        async def emit_with_hooks(self, *_args, **_kwargs):
            return {}

    async def execute_command(_command, _event_bus):
        return SimpleNamespace(success=True, message="Goal status", ui_config=None)

    parser = SimpleNamespace(
        parse_command=lambda _text: SimpleNamespace(name="goal", args=["show"])
    )
    executor = SimpleNamespace(execute_command=execute_command)
    tap = DisplayTap()
    tap.conversation_source = lambda: uid
    llm = SimpleNamespace(
        is_processing=False,
        conversation_history=history,
        _add_conversation_message=add_history,
        create_background_task=lambda coro, name=None: asyncio.create_task(coro, name=name),
    )
    bus = Bus(
        {
            "display_tap": tap,
            "web_message_queue_runtime": Runtime(),
            "slash_parser": parser,
            "command_executor": executor,
        }
    )
    state = _State(history)
    state._event_bus = bus
    state._llm_service = llm
    stream = ChatStreamService(state)
    assert await stream.add_input(
        {"id": input_id, "text": "/goal show", "attachments": [], "goal_reply_id": reply_id}
    ) == {"id": input_id, "state": "queued"}

    local = LocalStateService.__new__(LocalStateService)
    local._event_bus = bus
    local._llm_service = llm
    local.get_conversation_uid = lambda: uid
    accepted = local._try_goal_command(
        "/goal show",
        "/goal show",
        client_request_id=input_id,
        private_reply=True,
        reply_id=reply_id,
        expected_uid=uid,
        return_task=True,
    )
    await accepted["_private_task"]

    snapshot = stream.subscribe({})["events"][0]["data"]
    input_rows = [row for row in snapshot["rows"] if row["kind"] == "input"]
    assert [row["id"] for row in input_rows] == [input_id]
    assert reply_id not in str(snapshot)
