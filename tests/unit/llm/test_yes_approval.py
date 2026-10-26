"""A voice turn asks for the user's yes instead of waiting on an approval prompt."""

import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.permissions.hook import PermissionHook
from kollabor_agent.shell_executor import ShellResult
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_agent.yes_approval import NEEDS_YES, YesApprovals, human_answer
from kollabor_events.bus import EventBus
from tests.unit.llm.voice_admission_helpers import make_device_voice_context, make_voice_origin_context


@pytest.mark.parametrize(
    "text",
    ["yes", "Yes.", "yeah", "Yep!", "yes please", "Yeah, go ahead.", "OK", "okay", "sure",
     "approve it", "do it", "Go for it.", "yes, do it please", "Yes, run it.",
     "Okay, you don't have to ask, just do it.", "Yeah, go ahead and run it.", "sure, run those",
     "go", "alright, do it", "please run it", "I approve", "sounds good"],
)
def test_a_short_yes_is_a_yes(text):
    assert human_answer(text) == "yes"


@pytest.mark.parametrize("text", ["no", "No.", "nope", "no thanks", "Don’t do it.", "cancel"])
def test_a_short_no_is_a_no(text):
    assert human_answer(text) == "no"


@pytest.mark.parametrize(
    "text",
    ["yes and check the logs too", "what?", "", "please", "yes no", "is it safe?",
     "yesterday", "not yet", "ok thanks", "yes, do it, thanks", "thank you", "yes but wait",
     "yes, don't run it", "no, run it", "go away", "you don't have to ask what"],
)
def test_anything_else_is_a_normal_turn(text):
    assert human_answer(text) is None


CHECK_PRS = {"type": "terminal", "id": "call-1", "command": "gh pr list"}
# The voice policy in every mode that asks, for a LiveKit room's turn and a /voice
# turn. Trust All asks for nothing: its voice turns run as typed ones do.
ASKING_MODES = pytest.mark.parametrize("mode", ["confirm_all", "default", "auto_approve_edits"])
EVERY_VOICE = pytest.mark.parametrize(
    "voice_origin", [make_voice_origin_context, make_device_voice_context], ids=["livekit", "device"]
)


def again(call, call_id="call-2"):
    """The model asking for the same call again: a new call id, same command."""
    return {**call, "id": call_id}


def asked(approvals, *calls, context=None):
    """The agent's reply asked for these calls, and it is finished."""
    for call in calls:
        approvals.ask(call, context)
    approvals.on_reply_done()


def test_one_yes_runs_each_asked_call_once():
    approvals = YesApprovals()
    message = {"type": "hub_msg", "id": "call-3", "to": "koordinator", "message": "check CI"}
    asked(approvals, CHECK_PRS, message)
    approvals.on_human_input("Yes.")
    note = approvals.take_note()
    assert "gh pr list" in note and "koordinator" in note
    assert approvals.take_note() == ""  # one-shot
    assert approvals.take(again(CHECK_PRS)) and approvals.take(again(message))
    assert not approvals.take(again(CHECK_PRS, "call-4"))  # once each
    assert not approvals.take({"type": "terminal", "id": "x", "command": "gh issue list"})


def test_a_yes_approves_the_exact_command_of_the_exact_tool():
    approvals = YesApprovals()
    get_repo = {"type": "mcp_tool", "id": "m1", "name": "github__get_repo", "arguments": {"repo": "kollab"}}
    asked(approvals, {"type": "terminal", "id": "a", "command": 'echo "$HOME"'}, get_repo)
    approvals.on_human_input("yes")
    assert not approvals.take({"type": "terminal", "id": "b", "command": "echo '$HOME'"})  # a shell runs it another way
    assert not approvals.take({**get_repo, "id": "m2", "name": "github__delete_repo"})
    assert not approvals.take({**get_repo, "id": "m3", "arguments": {"repo": "other"}})
    assert approvals.take({"type": "terminal", "id": "c", "command": 'echo "$HOME"'})
    assert approvals.take({**get_repo, "id": "m4"})


def test_a_native_call_matches_by_its_arguments():
    approvals = YesApprovals()
    native = {"type": "terminal", "id": "a", "name": "terminal", "command": "gh pr list",
              "input": {"command": "gh pr list"}, "arguments": {"command": "gh pr list"}, "index": 0}
    asked(approvals, native)
    approvals.on_human_input("yes")
    assert approvals.take({**native, "id": "b", "index": 1})


def test_a_no_drops_the_ask():
    approvals = YesApprovals()
    asked(approvals, CHECK_PRS)
    approvals.on_human_input("no")
    approvals.on_human_input("yes")
    assert not approvals.take(again(CHECK_PRS))
    assert approvals.take_note() == ""


def test_a_yes_answers_only_the_reply_just_before_it():
    approvals = YesApprovals()
    commit = {"type": "terminal", "id": "c1", "command": "git commit -m notes"}
    asked(approvals, commit)
    approvals.on_human_input("show the diff first")
    asked(approvals)  # the diff, then "want a summary?": no call asked
    approvals.on_human_input("sure")
    assert not approvals.take(again(commit)) and approvals.take_note() == ""

    asked(approvals, commit)
    asked(approvals)  # a later reply that asks nothing (a peer's message woke it)
    approvals.on_human_input("yes")
    assert not approvals.take(again(commit))


def test_a_yes_while_the_agent_is_still_replying_answers_nothing():
    approvals = YesApprovals()
    approvals.ask(CHECK_PRS)  # its reply is still being written
    approvals.on_human_input("yeah")
    approvals.on_reply_done()
    approvals.on_human_input("yes")
    assert not approvals.take(again(CHECK_PRS))


def test_a_reply_talked_over_leaves_nothing_for_a_yes():
    from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

    approvals = YesApprovals()
    asked(approvals, CHECK_PRS, context=make_voice_origin_context("t1"))
    approvals.reply_cut("t1")  # the user talked over it: they never heard the whole ask
    approvals.on_human_input("yeah")
    assert not approvals.take(again(CHECK_PRS))

    approvals.ask(CHECK_PRS, make_voice_origin_context("t2"))
    approvals.reply_cut("t2")  # cut while its turn still runs
    approvals.ask(again(CHECK_PRS, "late"), make_voice_origin_context("t2"))
    approvals.on_reply_done()
    approvals.on_human_input("yes")
    assert not approvals.take(again(CHECK_PRS))


@EVERY_VOICE
def test_a_voice_ask_stops_counting_after_five_minutes(monkeypatch, voice_origin):
    from kollabor_agent import yes_approval

    clock = [1000.0]
    monkeypatch.setattr(yes_approval.time, "monotonic", lambda: clock[0])
    voice, typed = YesApprovals(), YesApprovals()
    asked(voice, CHECK_PRS, context=voice_origin("t1"))
    asked(typed, CHECK_PRS)  # a parked card's ask: its card stays open, and so does the ask
    clock[0] += yes_approval.ASK_OPEN_SECONDS + 1
    voice.on_human_input("yes")
    typed.on_human_input("yes")
    assert not voice.take(again(CHECK_PRS))
    assert typed.take(again(CHECK_PRS))


def test_the_greeting_asks_for_nothing():
    from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

    approvals = YesApprovals()
    greeting = {**make_voice_origin_context("t0"), "voice_hidden": True}
    asked(approvals, CHECK_PRS, context=greeting)
    approvals.on_human_input("yeah")  # to "ready?"
    assert not approvals.take(again(CHECK_PRS))


def test_a_later_ask_replaces_the_earlier_one():
    approvals = YesApprovals()
    asked(approvals, CHECK_PRS)
    approvals.on_human_input("check the issues instead")
    issues = {"type": "terminal", "id": "call-5", "command": "gh issue list"}
    asked(approvals, issues)
    approvals.on_human_input("yes")
    assert approvals.take(again(issues)) and not approvals.take(again(CHECK_PRS))


def test_an_approval_lasts_until_the_users_next_message():
    approvals = YesApprovals()
    asked(approvals, CHECK_PRS)
    approvals.on_human_input("yes")
    approvals.on_human_input("thanks")
    assert not approvals.take(again(CHECK_PRS))


def test_a_new_conversation_drops_what_the_old_one_asked():
    approvals = YesApprovals()
    approvals.on_human_input("show me the PRs", "chat-1")
    asked(approvals, CHECK_PRS)
    approvals.on_human_input("yes", "chat-2")  # after /resume or a new chat
    assert not approvals.take(again(CHECK_PRS))


# The whole path: executor, the permission hook on a real bus, a Confirm All
# PermissionManager whose prompt must never open for a voice turn.


def _confirm_all_manager(mode="confirm_all"):
    from kollabor_agent.permissions.manager import PermissionManager
    from kollabor_agent.permissions.risk_assessor import RiskAssessor
    from kollabor_events.dict_utils import deep_merge
    from kollabor_events.permissions_config import PERMISSION_CONFIG_DEFAULTS
    from kollabor_events.permissions_models import RiskAssessmentRules

    config = deep_merge(
        PERMISSION_CONFIG_DEFAULTS,
        {"kollabor": {"permissions": {"approval_mode": mode}}},
    )
    return PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(rules=RiskAssessmentRules(), config=config),
        event_bus=MagicMock(emit_with_hooks=AsyncMock()),
    )


async def _executor(mode="confirm_all"):
    bus = EventBus()
    manager = _confirm_all_manager(mode)
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
    executor.permissions = manager
    return executor, prompt


NOTES = {"type": "terminal", "id": "call-1", "command": "touch notes.txt"}


@ASKING_MODES
@EVERY_VOICE
def test_a_voice_command_that_needs_a_yes_runs_on_the_users_yes(mode, voice_origin):
    async def scenario():
        executor, prompt = await _executor(mode)
        first = await executor.execute_tool(dict(NOTES), execution_context=voice_origin("t1"))
        executor.yes_approvals.on_reply_done()
        executor.yes_approvals.on_human_input("Yeah, go ahead.")
        note = executor.yes_approvals.take_note()
        second = await executor.execute_tool(again(NOTES), execution_context=voice_origin("t2"))
        third = await executor.execute_tool(again(NOTES, "c3"), execution_context=voice_origin("t2"))
        return executor, prompt, first, note, second, third

    executor, prompt, first, note, second, third = asyncio.run(scenario())
    assert not first.success and first.error == NEEDS_YES
    assert "touch notes.txt" in note
    assert second.success and second.output == "clean\n"
    # the yes ran it once, as the guard planned it: argv, never a shell
    executor.shell_executor.run_argv.assert_awaited_once()
    argv = executor.shell_executor.run_argv.await_args.args[0]
    assert argv[0].endswith("/touch") and argv[1:] == ("notes.txt",)
    executor._execute_terminal_command.assert_not_awaited()
    assert third.error == NEEDS_YES
    prompt.assert_not_awaited()


@ASKING_MODES
@EVERY_VOICE
def test_a_voice_file_create_points_the_model_at_the_terminal_and_its_yes(tmp_path, mode, voice_origin):
    # Real models reach for file_create first: it never runs, and its result names the
    # terminal path a yes can run, so the agent asks instead of saying it can't.
    async def scenario():
        executor, prompt = await _executor(mode)
        create = {"type": "file_create", "id": "f1", "file": str(tmp_path / "notes.md"), "content": "notes"}
        refused = await executor.execute_tool(dict(create), execution_context=voice_origin("t1"))
        touch = {"type": "terminal", "id": "c1", "command": "touch notes.md"}
        asked = await executor.execute_tool(dict(touch), execution_context=voice_origin("t1"))
        executor.yes_approvals.on_reply_done()
        executor.yes_approvals.on_human_input("Yes.")
        ran = await executor.execute_tool(again(touch), execution_context=voice_origin("t2"))
        return executor, prompt, refused, asked, ran

    executor, prompt, refused, asked, ran = asyncio.run(scenario())
    assert not refused.success and not (tmp_path / "notes.md").exists()
    assert refused.error.startswith("command_blocked") and "call the terminal tool" in refused.error
    assert asked.error == NEEDS_YES and ran.success
    assert executor.shell_executor.run_argv.await_args.args[0][1:] == ("notes.md",)
    prompt.assert_not_awaited()


@ASKING_MODES
@EVERY_VOICE
def test_a_voice_yes_never_runs_a_shell_or_a_wrapper(mode, voice_origin):
    async def scenario():
        executor, _ = await _executor(mode)
        results = []
        for command in ("bash -c 'rm -rf ~'", "env rm -rf ~"):
            call = {"type": "terminal", "id": "w1", "command": command}
            results.append(await executor.execute_tool(dict(call), execution_context=voice_origin("t1")))
            executor.yes_approvals.on_reply_done()
            executor.yes_approvals.on_human_input("yes")
            results.append(await executor.execute_tool(again(call), execution_context=voice_origin("t2")))
        return executor, results

    executor, results = asyncio.run(scenario())
    assert all(result.error.startswith("command_blocked") for result in results)
    executor.shell_executor.run_argv.assert_not_awaited()
    executor._execute_terminal_command.assert_not_awaited()


@EVERY_VOICE
def test_trust_all_runs_a_voice_turns_tools_as_a_typed_turn_does(tmp_path, voice_origin):
    # Trust All: a voice turn asks for nothing, and nothing is off the voice list.
    async def scenario():
        executor, prompt = await _executor("trust_all")
        results = []
        for command in ("touch notes.txt", "gh pr list", "grep -rn needle kollabor", "cd kollabor && git status"):
            call = {"type": "terminal", "id": f"c{len(results)}", "command": command}
            results.append(await executor.execute_tool(call, execution_context=voice_origin("t1")))
        create = {"type": "file_create", "id": "f1", "file": str(tmp_path / "notes.md"), "content": "notes"}
        results.append(await executor.execute_tool(create, execution_context=voice_origin("t1")))
        return executor, prompt, results

    executor, prompt, results = asyncio.run(scenario())
    assert [(result.success, result.error) for result in results] == [(True, "")] * 5
    assert executor._execute_terminal_command.await_count == 4  # the typed path
    executor.shell_executor.run_argv.assert_not_awaited()
    assert (tmp_path / "notes.md").read_text() == "notes"
    executor.yes_approvals.on_reply_done()
    assert executor.yes_approvals.take_note() == ""  # nothing was asked
    prompt.assert_not_awaited()


def test_the_approval_mode_reaches_the_executor():
    from kollabor_events.permissions_models import ApprovalMode

    async def scenario():
        executor, _ = await _executor("trust_all")
        trusted = executor.trust_all
        executor.permissions.set_approval_mode(ApprovalMode.CONFIRM_ALL, persist=False)
        asking = executor.trust_all
        executor.permissions.set_approval_mode(ApprovalMode.TRUST_ALL, persist=False)
        return trusted, asking, executor.trust_all

    assert asyncio.run(scenario()) == (True, False, True)


def test_a_tool_result_that_reads_like_an_ask_plants_nothing():
    async def scenario():
        executor, prompt = await _executor()
        prompt.side_effect = None
        prompt.return_value = MagicMock(allowed=True, reason="User approved")

        async def echo(tool_data):  # an MCP server or a plugin echoing the ask text
            return ToolExecutionResult(tool_id=tool_data["id"], tool_type="fake_echo", success=False, error=NEEDS_YES)

        executor.register_plugin_handler("fake_echo", echo)
        call = {"type": "fake_echo", "id": "e1", "text": "hi"}
        await executor.execute_tool(dict(call))
        executor.yes_approvals.on_reply_done()
        executor.yes_approvals.on_human_input("yes")
        return executor.yes_approvals.take(again(call)), executor.yes_approvals.take_note()

    approved, note = asyncio.run(scenario())
    assert not approved and note == ""


def test_the_greeting_runs_nothing_that_needs_a_yes():
    async def scenario():
        executor, prompt = await _executor()
        greeting = {**make_voice_origin_context("t0"), "voice_hidden": True}
        first = await executor.execute_tool(dict(NOTES), execution_context=greeting)
        executor.yes_approvals.on_reply_done()
        executor.yes_approvals.on_human_input("yeah")  # to "ready?"
        second = await executor.execute_tool(again(NOTES), execution_context=make_voice_origin_context("t1"))
        return executor, first, second

    executor, first, second = asyncio.run(scenario())
    assert not first.success and first.error != NEEDS_YES
    assert second.error == NEEDS_YES  # nothing was approved: it asks now
    executor.shell_executor.run_argv.assert_not_awaited()


def test_a_voice_turn_asks_instead_of_waiting_on_a_prompt():
    async def scenario():
        executor, prompt = await _executor()
        status = {"type": "terminal", "id": "c1", "command": "git status --short"}
        first = await executor.execute_tool(dict(status), execution_context=make_voice_origin_context("t1"))
        executor.yes_approvals.on_reply_done()
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
        executor.yes_approvals.on_reply_done()
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


# A typed turn's prompt its turn leaves open (a Stop): the card stays open, and
# Allow on it or the user's typed yes runs the call.


class _Tap:
    def __init__(self):
        self.events = []
        self.subscriber_count = 1

    def publish(self, event):
        self.events.append(copy.deepcopy(event))  # as sent: a real tap serializes it


class _Rpc:
    def register(self, method, handler):
        pass


async def _parked_call():
    """A Confirm All call on the real bridge, its prompt parked by a Stop."""
    from kollabor.llm.permissions.attach_bridge import AttachPermissionBridge
    from kollabor_agent.permissions import handle_confirmation_response

    executor, _ = await _executor()
    manager = executor.permissions
    bridge, tap = AttachPermissionBridge(), _Tap()

    async def ask(details):
        response = await bridge.request_confirmation(
            display_tap=tap, rpc_server=_Rpc(), details=dict(details), timeout=None
        )
        return await handle_confirmation_response(manager, details, response)

    manager.set_confirmation_callback(ask, park=bridge.park, settle=bridge.try_settle_response)
    call = asyncio.create_task(executor.execute_tool(dict(CHECK_PRS)))
    for _ in range(200):
        if bridge._pending:
            break
        await asyncio.sleep(0)
    manager.park_confirmations()  # the Stop
    result = await asyncio.wait_for(call, 1)
    executor.yes_approvals.on_reply_done()  # and its turn ended
    return executor, manager, bridge, tap, result


def _continued(manager):
    """The agent was told to go on (TRIGGER_LLM_CONTINUE), and with what."""
    from kollabor_events.models import EventType

    triggers = [
        call for call in manager._event_bus.emit_with_hooks.await_args_list
        if call.args and call.args[0] == EventType.TRIGGER_LLM_CONTINUE
    ]
    hud = manager._event_bus.get_service.return_value.queue_agent_hud
    return triggers, hud


def test_allow_on_a_parked_card_runs_the_call_and_the_agent_goes_on():
    from kollabor_events.permissions_models import ConfirmationResponse

    async def scenario():
        executor, manager, bridge, tap, first = await _parked_call()
        assert "call-1" in bridge._pending  # the card stays open after the Stop
        bridge.try_settle_response("call-1", ConfirmationResponse.APPROVE_ONCE)  # Allow
        for _ in range(50):
            await asyncio.sleep(0)
        triggers, hud = _continued(manager)
        second = await asyncio.wait_for(executor.execute_tool(again(CHECK_PRS)), 2)  # the agent's re-call
        return executor, first, triggers, hud, second, list(tap.events)

    executor, first, triggers, hud, second, events = asyncio.run(scenario())
    assert first.error == NEEDS_YES
    assert len(triggers) == 1  # the agent goes on
    section, label, text = hud.call_args.args
    assert (section, label) == ("permission", "approved") and "gh pr list" in text
    assert second.success and second.output == "#318 open"
    executor._execute_terminal_command.assert_awaited_once()
    assert events[-1] == {"type": "permission_granted", "tool_id": "call-1", "scope": "once", "parked": True}


def test_deny_on_a_parked_card_never_runs_it_and_the_agent_stays_quiet():
    from kollabor_events.permissions_models import ConfirmationResponse

    async def scenario():
        executor, manager, bridge, tap, first = await _parked_call()
        bridge.try_settle_response("call-1", ConfirmationResponse.DENY)
        for _ in range(50):
            await asyncio.sleep(0)
        triggers, _ = _continued(manager)
        executor.yes_approvals.on_human_input("yes")  # nothing is asked any more
        return executor, triggers, executor.yes_approvals.take(again(CHECK_PRS))

    executor, triggers, approved = asyncio.run(scenario())
    assert triggers == [] and not approved
    executor._execute_terminal_command.assert_not_awaited()


def test_a_typed_yes_closes_the_parked_card_and_runs_the_call():
    async def scenario():
        executor, manager, bridge, tap, first = await _parked_call()
        executor.yes_approvals.on_human_input("Yes.")
        for _ in range(50):
            await asyncio.sleep(0)
        triggers, _ = _continued(manager)
        second = await asyncio.wait_for(executor.execute_tool(again(CHECK_PRS)), 2)
        return executor, set(bridge._pending), triggers, second, list(tap.events)

    executor, pending, triggers, second, events = asyncio.run(scenario())
    assert not pending  # its card closed, approved
    assert events[-1] == {"type": "permission_granted", "tool_id": "call-1", "scope": "once", "parked": True}
    assert triggers == []  # the yes is its own turn: it runs the call there
    assert second.success
    executor._execute_terminal_command.assert_awaited_once()


def test_a_typed_no_closes_the_parked_card_denied():
    async def scenario():
        executor, manager, bridge, tap, first = await _parked_call()
        executor.yes_approvals.on_human_input("no thanks")
        for _ in range(50):
            await asyncio.sleep(0)
        return set(bridge._pending), list(tap.events)

    pending, events = asyncio.run(scenario())
    assert not pending
    assert events[-1] == {"type": "permission_denied", "tool_id": "call-1", "parked": True}


def test_a_new_chat_closes_the_old_ones_parked_cards():
    async def scenario():
        executor, manager, bridge, tap, first = await _parked_call()
        executor.yes_approvals.on_human_input("show me the PRs", "chat-1")
        executor.yes_approvals.on_human_input("hello", "chat-2")  # /resume or a new chat
        for _ in range(50):
            await asyncio.sleep(0)
        return set(bridge._pending), list(tap.events)

    pending, events = asyncio.run(scenario())
    assert not pending
    assert events[-1] == {"type": "permission_denied", "tool_id": "call-1", "outcome": "cancelled", "parked": True}
