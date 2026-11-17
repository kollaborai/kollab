from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.state.local import LocalStateService, _web_command_output
from kollabor_events.models import SlashCommand
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


async def _web_reply(
    plugin: ResumeConversationPlugin, name: str, args: list[str]
) -> str:
    handlers = {
        "branch": plugin.handle_branch,
        "fork": plugin.handle_sessions,
        "resume": plugin.handle_resume,
    }

    class HandlerExecutor:
        async def execute_command(self, command, event_bus):
            return await handlers[command.name](command)

    command_text = " ".join([f"/{name}", *args])
    command = SlashCommand(name=name, args=args, raw_input=command_text)
    service = LocalStateService.__new__(LocalStateService)
    service._event_bus = None
    _, reply = await service._run_web_slash_command(command, HandlerExecutor())
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
    plugin.llm_service = None
    plugin.discover_conversations = AsyncMock(
        return_value=[
            _conversation("session-1", "Plan a release"),
            _conversation("session-2", "Investigate a regression"),
            _conversation("session-3", "Prepare a handoff"),
        ]
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
    assert "Plan a release" in branch_sessions
    assert "session-1 | 3 msgs | main" in branch_sessions
    assert "/branch session-1" in branch_sessions
    assert "/branch session-2" in branch_sessions
    assert "/branch session-3" in branch_sessions

    fork_sessions = await _web_reply(plugin, "fork", [])
    assert "Plan a release" in fork_sessions
    assert "/resume session-1" in fork_sessions

    fork_search_results = await _web_reply(plugin, "fork", ["search", "release"])
    assert "/resume search-1" in fork_search_results

    resume_sessions = await _web_reply(plugin, "resume", [])
    assert "Investigate a regression" in resume_sessions
    assert "/resume session-2" in resume_sessions

    search_results = await _web_reply(plugin, "resume", ["search", "release"])
    assert "Find release notes" in search_results
    assert "/resume search-1" in search_results

    filtered_sessions = await _web_reply(plugin, "resume", ["--limit", "3"])
    assert "Prepare a handoff" in filtered_sessions
    assert "/resume session-3" in filtered_sessions

    branch_points = await _web_reply(plugin, "branch", ["session-1"])
    assert "A release plan" in branch_points
    assert "/branch session-1 0" in branch_points
    assert "/branch session-1 1" in branch_points


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
