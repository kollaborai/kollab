from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.commands.executor import SlashCommandExecutor
from kollabor.commands.parser import SlashCommandParser
from kollabor.commands.registry import SlashCommandRegistry
from kollabor.state.local import LocalStateService, _web_command_output
from plugins.resume_conversation_plugin import ResumeConversationPlugin


def _conversation(session_id: str, request: str) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        created_time=None,
        preview_messages=[{"role": "user", "content": request}],
        message_count=3,
        git_branch="main",
        last_message_preview=f"{request} finished",
        duration=None,
        file_id="file-id",
        working_directory="/tmp/project",
        topics=[],
        search_relevance=None,
    )


def _assert_action_row(reply: str, title: str, details: str, command: str) -> None:
    matching_rows = [line for line in reply.splitlines() if title in line]
    assert len(matching_rows) == 1
    row = matching_rows[0]
    assert details in row
    assert f"`{command}`" in row


def _session_list_result(message: str, sessions: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        message=message,
        ui_config=SimpleNamespace(
            title="Sessions",
            modal_config={
                "footer": "Up/Down navigate | Enter select | Esc cancel",
                "sections": [
                    {
                        "title": "Sessions",
                        "type": "session_list",
                        "sessions": sessions,
                    }
                ],
            },
        ),
    )


async def _web_reply(
    plugin: ResumeConversationPlugin, name: str, args: list[str]
) -> str:
    class WebEventBus:
        async def emit_with_hooks(self, *_args, **_kwargs):
            return {}

    command_text = " ".join([f"/{name}", *args])
    command = SlashCommandParser().parse_command(command_text)
    assert command is not None
    registry = SlashCommandRegistry()
    plugin._register_resume_commands(registry)
    service = LocalStateService.__new__(LocalStateService)
    service._event_bus = WebEventBus()
    _, reply = await service._run_web_slash_command(
        command, SlashCommandExecutor(registry)
    )
    return reply


@pytest.mark.asyncio
async def test_web_resume_and_branch_lists_render_actionable_rows():
    plugin = ResumeConversationPlugin()
    plugin.conversation_manager = Mock()
    plugin.conversation_manager.get_session_messages.return_value = [
        {"index": 0, "role": "user", "preview": "A release plan"},
        {"index": 1, "role": "assistant", "preview": "The plan is ready"},
    ]
    plugin.conversation_logger = Mock()
    plugin._ensure_conversation_manager = Mock(return_value=True)
    plugin.llm_service = SimpleNamespace(
        conversation_history=[
            {"role": "user", "content": "Active task"},
            {"role": "assistant", "content": "Working"},
        ]
    )
    conversations = [
        _conversation("session-1", "Plan a release"),
        _conversation("session-2", "Investigate a regression"),
        _conversation("session-3", "Prepare a handoff"),
        _conversation("session-4", "Review a migration"),
    ]
    plugin.discover_conversations = AsyncMock(
        side_effect=lambda limit=None: conversations[:limit] if limit else conversations
    )
    plugin.conversation_logger.search_sessions.return_value = [
        {
            "session_id": "search-1",
            "file_path": "search-1.json",
            "message_count": 2,
            "start_time": None,
            "end_time": None,
            "preview_messages": [{"role": "user", "content": "Find release notes"}],
            "topics": ["release"],
            "working_directory": "/tmp/project",
            "git_branch": "main",
            "duration": None,
            "size_bytes": 128,
            "search_relevance": 0.9,
        }
    ]

    branch_sessions = await _web_reply(plugin, "branch", [])
    _assert_action_row(
        branch_sessions,
        "Plan a release",
        r"session-1 \| 3 msgs \| main",
        "/branch session-1",
    )
    _assert_action_row(
        branch_sessions,
        "Investigate a regression",
        r"session-2 \| 3 msgs \| main",
        "/branch session-2",
    )
    _assert_action_row(
        branch_sessions,
        "Prepare a handoff",
        r"session-3 \| 3 msgs \| main",
        "/branch session-3",
    )
    _assert_action_row(
        branch_sessions,
        r"\[\*CURRENT\*\] Active task",
        r"2 msgs \| this session",
        "/branch current",
    )
    assert "Review a migration" in branch_sessions
    assert "Up/Down navigate" not in branch_sessions
    assert "Enter select" not in branch_sessions
    assert "Esc cancel" not in branch_sessions

    fork_sessions = await _web_reply(plugin, "fork", [])
    _assert_action_row(
        fork_sessions,
        "Plan a release",
        r"session-1 \| 3 msgs \| main",
        "/resume session-1",
    )

    fork_search_results = await _web_reply(plugin, "fork", ["search", "release"])
    _assert_action_row(
        fork_search_results,
        "Find release notes",
        r"search-1 \| 2 msgs \| main",
        "/resume search-1",
    )

    resume_sessions = await _web_reply(plugin, "resume", [])
    _assert_action_row(
        resume_sessions,
        "Investigate a regression",
        r"session-2 \| 3 msgs \| main",
        "/resume session-2",
    )

    search_results = await _web_reply(plugin, "resume", ["search", "release"])
    _assert_action_row(
        search_results,
        "Find release notes",
        r"search-1 \| 2 msgs \| main",
        "/resume search-1",
    )

    filtered_sessions = await _web_reply(plugin, "resume", ["--limit", "3"])
    _assert_action_row(
        filtered_sessions,
        "Prepare a handoff",
        r"session-3 \| 3 msgs \| main",
        "/resume session-3",
    )
    assert "Review a migration" not in filtered_sessions
    plugin.discover_conversations.assert_awaited_with(3)

    branch_points = await _web_reply(plugin, "branch", ["session-1"])
    _assert_action_row(
        branch_points,
        "A release plan",
        "user",
        "/branch session-1 0",
    )
    _assert_action_row(
        branch_points,
        "The plan is ready",
        "assistant",
        "/branch session-1 1",
    )


def test_unusable_session_list_rows_keep_the_command_message():
    result = SimpleNamespace(
        success=True,
        message="No sessions available",
        ui_config=SimpleNamespace(
            title="History",
            modal_config={
                "sections": [
                    {
                        "title": "Sessions",
                        "type": "session_list",
                        "sessions": [None, {"metadata": "invalid"}],
                    }
                ]
            },
        ),
    )

    assert _web_command_output(result, "fork") == "No sessions available"


def test_session_list_titles_and_subtitles_are_markdown_escaped():
    result = _session_list_result(
        "No sessions available",
        [
            {
                "id": "session-1",
                "title": "*bold* [click](https://example.invalid) — Café 東京",
                "subtitle": "![image](https://example.invalid/image.png)",
                "action": "resume_session",
                "metadata": {"session_id": "session-1"},
            }
        ],
    )

    output = _web_command_output(result, "resume")

    assert r"\*bold\* \[click\]\(https://example.invalid\) — Café 東京" in output
    assert r"\!\[image\]\(https://example.invalid/image.png\)" in output
    assert "[click](https://example.invalid)" not in output
    assert "![image](https://example.invalid/image.png)" not in output


def test_session_without_explicit_action_is_not_rendered_as_a_bare_row():
    result = _session_list_result(
        "No actionable sessions",
        [
            {
                "id": "session-1",
                "title": "Unselectable conversation",
                "subtitle": "3 msgs",
                "metadata": {"session_id": "session-1"},
            }
        ],
    )

    assert _web_command_output(result, "fork") == "No actionable sessions"


def test_whitespace_only_title_falls_back_to_session_id():
    result = _session_list_result(
        "No sessions available",
        [
            {
                "id": "session-1",
                "title": "   ",
                "action": "resume_session",
                "metadata": {"session_id": "session-1"},
            }
        ],
    )

    output = _web_command_output(result, "resume")

    _assert_action_row(output, "session-1", "", "/resume session-1")


@pytest.mark.parametrize("session_id", ["bad id", "bad\nid", "bad`id", "bad/id"])
def test_unsafe_session_ids_are_not_rendered_as_commands(session_id: str):
    result = _session_list_result(
        "No safe sessions",
        [
            {
                "id": session_id,
                "title": "Unsafe id",
                "action": "resume_session",
                "metadata": {"session_id": session_id},
            }
        ],
    )

    assert _web_command_output(result, "resume") == "No safe sessions"


@pytest.mark.parametrize("message_index", [True, -1, "0"])
def test_invalid_branch_message_indices_are_not_rendered(message_index):
    result = _session_list_result(
        "No safe branch points",
        [
            {
                "id": "0",
                "title": "Invalid branch point",
                "action": "branch_execute",
                "metadata": {
                    "session_id": "session-1",
                    "message_index": message_index,
                },
            }
        ],
    )

    assert _web_command_output(result, "branch") == "No safe branch points"


def test_branch_execute_requires_session_id_in_metadata():
    result = _session_list_result(
        "No safe branch points",
        [
            {
                "id": "0",
                "title": "Branch point without session metadata",
                "action": "branch_execute",
                "metadata": {"message_index": 0},
            }
        ],
    )

    assert _web_command_output(result, "branch") == "No safe branch points"


def test_mixed_safe_and_unsafe_rows_explain_hidden_choices():
    result = _session_list_result(
        "No sessions available",
        [
            {
                "id": "session-1",
                "title": "Safe session",
                "action": "resume_session",
                "metadata": {"session_id": "session-1"},
            },
            {
                "id": "bad/id",
                "title": "Unsafe session",
                "action": "resume_session",
                "metadata": {"session_id": "bad/id"},
            },
        ],
    )

    output = _web_command_output(result, "resume")

    assert "Safe session" in output
    assert "Unsafe session" not in output
    assert "Some session rows were hidden" in output
