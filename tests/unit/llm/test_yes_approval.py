"""A voice turn asks for the user's yes instead of waiting on an approval prompt."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.permissions.hook import PermissionHook
from kollabor_agent.shell_executor import ShellResult
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_agent.yes_approval import NEEDS_YES, YesApprovals, human_answer
from kollabor_events.bus import EventBus
from tests.unit.llm.voice_admission_helpers import make_voice_origin_context


@pytest.mark.parametrize(
    "text",
    ["yes", "Yes.", "yeah", "Yep!", "yes please", "Yeah, go ahead.", "OK", "okay", "sure",
     "approve it", "do it", "Go for it.", "yes, do it, thanks"],
)
def test_a_short_yes_is_a_yes(text):
    assert human_answer(text) == "yes"


@pytest.mark.parametrize("text", ["no", "No.", "nope", "no thanks", "Don’t do it.", "cancel"])
def test_a_short_no_is_a_no(text):
    assert human_answer(text) == "no"


@pytest.mark.parametrize(
    "text",
    ["yes and check the logs too", "what?", "", "please", "yes no", "is it safe?",
     "yesterday", "not yet"],
)
def test_anything_else_is_a_normal_turn(text):
    assert human_answer(text) is None


CHECK_PRS = {"type": "terminal", "id": "call-1", "command": "gh pr list"}


def again(call, call_id="call-2"):
    """The model asking for the same call again: a new call id, same command."""
    return {**call, "id": call_id}


def test_one_yes_runs_each_asked_call_once():
    approvals = YesApprovals()
    message = {"type": "hub_msg", "id": "call-3", "to": "koordinator", "message": "check CI"}
    approvals.ask(CHECK_PRS)
    approvals.ask(message)
    approvals.on_human_input("Yes.")
    note = approvals.take_note()
    assert "gh pr list" in note and "koordinator" in note
    assert approvals.take_note() == ""  # one-shot
    assert approvals.take(again(CHECK_PRS)) and approvals.take(again(message))
    assert not approvals.take(again(CHECK_PRS, "call-4"))  # once each
    assert not approvals.take({"type": "terminal", "id": "x", "command": "gh issue list"})


def test_the_same_command_matches_however_it_is_quoted():
    approvals = YesApprovals()
    approvals.ask({"type": "terminal", "id": "a", "command": 'kollab --hub msg koordinator "check CI"'})
    approvals.on_human_input("yes")
    assert approvals.take({"type": "terminal", "id": "b", "command": "kollab --hub msg koordinator 'check CI'"})


def test_a_native_call_matches_by_its_arguments():
    approvals = YesApprovals()
    native = {"type": "terminal", "id": "a", "name": "terminal", "command": "gh pr list",
              "input": {"command": "gh pr list"}, "arguments": {"command": "gh pr list"}, "index": 0}
    approvals.ask(native)
    approvals.on_human_input("yes")
    assert approvals.take({**native, "id": "b", "index": 1})


def test_a_no_drops_the_ask_and_other_words_leave_it_open():
    approvals = YesApprovals()
    approvals.ask(CHECK_PRS)
    approvals.on_human_input("hmm what does that do")
    approvals.on_human_input("yes")
    assert approvals.take(again(CHECK_PRS))  # still open after the question

    approvals.ask(CHECK_PRS)
    approvals.on_human_input("no")
    approvals.on_human_input("yes")
    assert not approvals.take(again(CHECK_PRS))
    assert approvals.take_note() == ""


def test_a_later_ask_replaces_the_earlier_one():
    approvals = YesApprovals()
    approvals.ask(CHECK_PRS)
    approvals.on_human_input("check the issues instead")
    issues = {"type": "terminal", "id": "call-5", "command": "gh issue list"}
    approvals.ask(issues)
    approvals.on_human_input("yes")
    assert approvals.take(again(issues)) and not approvals.take(again(CHECK_PRS))


def test_an_approval_lasts_until_the_users_next_message():
    approvals = YesApprovals()
    approvals.ask(CHECK_PRS)
    approvals.on_human_input("yes")
    approvals.on_human_input("thanks")
    assert not approvals.take(again(CHECK_PRS))


def test_a_new_conversation_drops_what_the_old_one_asked():
    approvals = YesApprovals()
    approvals.on_human_input("show me the PRs", "chat-1")
    approvals.ask(CHECK_PRS)
    approvals.on_human_input("yes", "chat-2")  # after /resume or a new chat
    assert not approvals.take(again(CHECK_PRS))


# The whole path: executor, the permission hook on a real bus, a Confirm All
# PermissionManager whose prompt must never open for a voice turn.


def _confirm_all_manager():
    from kollabor_agent.permissions.manager import PermissionManager
    from kollabor_agent.permissions.risk_assessor import RiskAssessor
    from kollabor_events.dict_utils import deep_merge
    from kollabor_events.permissions_config import PERMISSION_CONFIG_DEFAULTS
    from kollabor_events.permissions_models import RiskAssessmentRules

    config = deep_merge(
        PERMISSION_CONFIG_DEFAULTS,
        {"kollabor": {"permissions": {"approval_mode": "confirm_all"}}},
    )
    return PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(rules=RiskAssessmentRules(), config=config),
        event_bus=MagicMock(emit_with_hooks=AsyncMock()),
    )


async def _executor():
    bus = EventBus()
    manager = _confirm_all_manager()
    prompt = AsyncMock(side_effect=AssertionError("a voice turn never opens a prompt"))
    manager.set_confirmation_callback(prompt)
    await PermissionHook(permission_manager=manager).register(bus)
    executor = ToolExecutor(mcp_integration=MagicMock(), event_bus=bus, terminal_timeout=10, mcp_timeout=20)
    manager.set_tool_executor(executor)
    executor._execute_terminal_command = AsyncMock(
        return_value=ToolExecutionResult(tool_id="t", tool_type="terminal", success=True, output="#318 open")
    )
    executor.shell_executor.run_argv = AsyncMock(
        return_value=ShellResult(success=True, stdout="clean\n", stderr="", exit_code=0)
    )
    return executor, prompt


def test_a_voice_command_that_needs_a_yes_runs_on_the_users_yes():
    async def scenario():
        executor, prompt = await _executor()
        first = await executor.execute_tool(dict(CHECK_PRS), execution_context=make_voice_origin_context("t1"))
        executor.yes_approvals.on_human_input("Yeah, go ahead.")
        note = executor.yes_approvals.take_note()
        second = await executor.execute_tool(again(CHECK_PRS), execution_context=make_voice_origin_context("t2"))
        third = await executor.execute_tool(again(CHECK_PRS, "c3"), execution_context=make_voice_origin_context("t2"))
        return executor, prompt, first, note, second, third

    executor, prompt, first, note, second, third = asyncio.run(scenario())
    assert not first.success and first.error == NEEDS_YES
    assert "gh pr list" in note
    assert second.success and second.output == "#318 open"
    executor._execute_terminal_command.assert_awaited_once()  # the yes ran it, once
    assert third.error == NEEDS_YES
    prompt.assert_not_awaited()


def test_a_voice_turn_asks_instead_of_waiting_on_a_prompt():
    async def scenario():
        executor, prompt = await _executor()
        status = {"type": "terminal", "id": "c1", "command": "git status --short"}
        first = await executor.execute_tool(dict(status), execution_context=make_voice_origin_context("t1"))
        executor.yes_approvals.on_human_input("yes")
        second = await executor.execute_tool(again(status), execution_context=make_voice_origin_context("t2"))
        return executor, prompt, first, second

    executor, prompt, first, second = asyncio.run(scenario())
    assert not first.success and first.error == NEEDS_YES
    assert second.success and second.output == "clean\n"
    executor.shell_executor.run_argv.assert_awaited_once()
    prompt.assert_not_awaited()


def test_a_typed_yes_runs_what_a_voice_turn_asked_for():
    async def scenario():
        executor, prompt = await _executor()
        await executor.execute_tool(dict(CHECK_PRS), execution_context=make_voice_origin_context("t1"))
        executor.yes_approvals.on_human_input("yes")
        return prompt, await executor.execute_tool(again(CHECK_PRS))

    prompt, result = asyncio.run(scenario())
    assert result.success
    prompt.assert_not_awaited()


def test_a_typed_turn_still_shows_its_prompt():
    async def scenario():
        executor, prompt = await _executor()
        prompt.side_effect = None
        prompt.return_value = MagicMock(allowed=False, reason="User denied execution")
        return prompt, await executor.execute_tool(dict(CHECK_PRS))

    prompt, result = asyncio.run(scenario())
    prompt.assert_awaited_once()
    assert not result.success and result.error == "User denied execution"
