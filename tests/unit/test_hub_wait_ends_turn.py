"""hub_msg / hub_reply wait="true" is "send, then stop".

The tool definition, the agent prompt and CLAUDE.md all say wait="true" ends the
sender's turn once the send is out, and the hub sets it by itself for idle
chatter such as "standing by". The runtime only copied the flag into the result's
metadata; nothing read it, so the model was called again after every send. These
tests drive the real parser, tool executor, hub handler and queue processor, for
XML tags and for native tool calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.native_tools_handler import NativeToolsHandler
from kollabor_agent.queue_processor import QueueProcessor
from kollabor_agent.runtime import AgentRuntime
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_ai.response_parser import ResponseParser
from kollabor_events.data_models import ConversationMessage
from plugins.hub.plugin import HubPlugin


class _Bus:
    """Just enough event bus for the executor and the queue processor."""

    def get_service(self, name):
        return None

    async def emit_with_hooks(self, *args, **kwargs):
        return {}


class _Turn:
    """One agent: real parser, executor and hub handler behind a real queue processor."""

    def __init__(self, tmp_path, *, rejections=(), online=("lapis",)):
        self.parser = ResponseParser()
        self.executor = ToolExecutor(
            mcp_integration=None,
            event_bus=_Bus(),
            terminal_timeout=15,
            workspace=str(tmp_path),
        )
        services = {"response_parser": self.parser, "tool_executor": self.executor}
        bus = MagicMock()
        bus.get_service.side_effect = services.get

        self.hub = HubPlugin(event_bus=bus)
        self.hub._identity = AgentRuntime(
            name="coordinator",
            identity="koordinator",
            agent_id="koordinator-id",
            is_coordinator=True,
        )
        self.hub._presence = MagicMock()
        self.hub._presence.scan_all_presence.return_value = [
            AgentRuntime(name="coder", identity=name, agent_id=f"{name}-id")
            for name in online
        ]
        self.hub._route_message = AsyncMock(return_value=list(rejections))
        self.hub._display_outgoing_message = MagicMock()
        self.hub._bridge_forward = AsyncMock()
        self.hub._resolve_scope = MagicMock(return_value="direct")
        self.hub._task_ledger = MagicMock()
        self.hub._task_ledger.get_active_for.return_value = []
        self.hub._register_pipeline_tools()

        self.api = MagicMock()
        self.api._provider = None
        self.api.model = "test-model"
        self.api.provider_type = "test"
        self.api.last_stop_reason = ""
        self.api.last_thinking_content = None
        self.api.get_last_token_usage.return_value = None
        self.api.has_pending_tool_calls.return_value = False
        self.api.get_last_tool_calls.return_value = []
        self.api.format_tool_result.side_effect = (
            lambda call_id, text, is_error=False: {
                "role": "tool",
                "content": text,
            }
        )
        self.stream = MagicMock()
        self.stream.call_llm = AsyncMock(return_value="")
        self.history = [ConversationMessage(role="user", content="go")]

        self.processor = QueueProcessor(
            conversation_history=self.history,
            session_stats={
                "messages": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
            },
            stats={"total_thinking_time": 0},
            pending_tools=[],
            queue_metrics={},
            task_config=SimpleNamespace(
                queue=SimpleNamespace(overflow_strategy="drop_oldest")
            ),
            api_service=self.api,
            tool_executor=self.executor,
            response_parser=self.parser,
            message_display_service=MagicMock(),
            renderer=MagicMock(),
            config=SimpleNamespace(
                get=lambda key, default=None: 0 if key.endswith("delay") else default
            ),
            event_bus=_Bus(),
            conversation_logger=AsyncMock(),
            streaming_handler=self.stream,
            native_tools_handler=NativeToolsHandler(
                mcp_integration=SimpleNamespace(tool_registry={}),
                profile_manager=None,
                api_service=self.api,
                config=SimpleNamespace(get=lambda key, default=None: default),
            ),
            add_message_fn=MagicMock(),
            max_history=90,
            question_gate_enabled=False,
            max_queue_size=10,
        )
        self.processor._drain_env_block = MagicMock(return_value=None)

    async def respond(self, text: str, *, native=None) -> bool:
        """Run one turn where the model answered `text`; True when the turn ended."""
        self.stream.call_llm.return_value = text
        calls = [
            SimpleNamespace(id=f"call_{i}", name="hub_msg", input=args)
            for i, args in enumerate(native or [])
        ]
        self.api.has_pending_tool_calls.return_value = bool(calls)
        self.api.get_last_tool_calls.return_value = calls
        await self.processor._execute_llm_turn_inner(
            user_message_provided=True, current_parent_uuid="parent"
        )
        return self.processor.turn_completed

    @property
    def sends(self):
        return [call.args[0] for call in self.hub._route_message.await_args_list]


@pytest.fixture
def turn(tmp_path):
    return _Turn(tmp_path)


# --- XML tags --------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        '<hub_msg to="lapis" wait="true">report sent</hub_msg>',
        '<hub_msg wait="true" to="lapis">report sent</hub_msg>',
        "<hub_msg to='lapis' wait='true'>report sent</hub_msg>",
        '<hub_msg to="lapis" wait="TRUE">report sent</hub_msg>',
        '<hub_reply to="lapis" wait="true">on it</hub_reply>',
        '<hub_reply wait="true" to="lapis">on it</hub_reply>',
    ],
)
async def test_explicit_wait_ends_the_turn_after_the_send(turn, response):
    assert await turn.respond(response) is True

    assert len(turn.sends) == 1
    assert turn.sends[0].to == "lapis"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "All done. Standing by.",
        "Waiting for your review.",
        "Going quiet until you need me.",
        "staying quiet now",
    ],
)
async def test_idle_chatter_sets_wait_by_itself_and_ends_the_turn(turn, message):
    assert await turn.respond(f'<hub_msg to="lapis">{message}</hub_msg>') is True

    assert len(turn.sends) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        '<hub_msg to="lapis">here is the plan, starting now</hub_msg>',
        '<hub_msg to="lapis" wait="false">here is the plan</hub_msg>',
        '<hub_reply to="lapis">here is the plan</hub_reply>',
    ],
)
async def test_a_send_without_wait_still_goes_back_to_the_model(turn, response):
    assert await turn.respond(response) is False

    assert len(turn.sends) == 1


@pytest.mark.asyncio
async def test_a_rejected_send_does_not_end_the_turn(tmp_path):
    turn = _Turn(tmp_path, rejections=[("lapis", "cooldown active")])

    ended = await turn.respond('<hub_msg to="lapis" wait="true">report sent</hub_msg>')

    assert ended is False, "the model has to see the rejection"
    assert "cooldown active" in turn.history[-1].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message, wait, ends",
    [
        ("check the tunnel", ' wait="true"', True),
        ("check the tunnel, I am waiting for the result", "", True),
        ("check the tunnel", "", False),
    ],
)
async def test_a_send_to_a_remote_agent_follows_the_same_rule(
    turn, message, wait, ends
):
    ended = await turn.respond(
        f'<hub_msg to="infra@home-server"{wait}>{message}</hub_msg>'
    )

    assert ended is ends
    assert turn.sends[0].to == "infra@home-server"
    assert "sent to infra@home-server" in turn.history[-1].content


@pytest.mark.asyncio
async def test_a_send_to_nobody_does_not_end_the_turn(tmp_path):
    """The message went nowhere; waiting for a reply would wait forever."""
    turn = _Turn(tmp_path, online=("lapis",))

    ended = await turn.respond('<hub_msg to="ghost" wait="true">report sent</hub_msg>')

    assert ended is False
    assert "not online" in turn.history[-1].content


@pytest.mark.asyncio
async def test_a_failed_send_with_an_empty_message_does_not_end_the_turn(turn):
    ended = await turn.respond('<hub_msg to="lapis" wait="true">   </hub_msg>')

    assert ended is False
    assert turn.sends == []


@pytest.mark.asyncio
async def test_other_tools_in_the_same_reply_still_run(turn):
    ended = await turn.respond(
        "<terminal>printf hello-from-terminal</terminal>"
        '<hub_msg to="lapis" wait="true">report sent</hub_msg>'
    )

    assert ended is True
    assert len(turn.sends) == 1
    # The terminal ran and its output waits in history for the next turn.
    assert "hello-from-terminal" in turn.history[-1].content


@pytest.mark.asyncio
async def test_a_failing_tool_beside_a_wait_send_keeps_the_turn_going(turn):
    ended = await turn.respond(
        "<terminal>sh -c 'exit 3'</terminal>"
        '<hub_msg to="lapis" wait="true">report sent</hub_msg>'
    )

    assert ended is False, "the model has to see the failed command"
    assert len(turn.sends) == 1
    assert "Exit code 3" in turn.history[-1].content


@pytest.mark.asyncio
async def test_a_repeated_send_with_wait_ends_the_turn_too(turn):
    assert await turn.respond('<hub_msg to="lapis">same words</hub_msg>') is False

    ended = await turn.respond('<hub_msg to="lapis" wait="true">same words</hub_msg>')

    assert ended is True
    assert len(turn.sends) == 1, "the repeat is deduplicated, not delivered twice"


@pytest.mark.asyncio
async def test_a_reply_without_tools_still_ends_the_turn(turn):
    assert await turn.respond("Nothing to send.") is True
    assert turn.sends == []


@pytest.mark.asyncio
async def test_the_next_turn_after_a_wait_runs_normally(turn):
    assert await turn.respond('<hub_msg to="lapis" wait="true">report sent</hub_msg>')

    ended = await turn.respond(
        '<hub_msg to="lapis">one more thing, more later</hub_msg>'
    )

    assert ended is False


# --- native tool calls -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"to": "lapis", "message": "report sent", "wait": "true"},
        {"to": "lapis", "message": "report sent", "wait": True},
        {"to": "lapis", "message": "report sent", "wait": "True"},
        {"to": "lapis", "message": "All done. Standing by."},
        {"to": 'wait="true" to="lapis"', "message": "report sent"},
        {"to": '<hub_msg to="lapis" wait="true">report sent</hub_msg>'},
    ],
)
async def test_native_hub_msg_wait_ends_the_turn(turn, args):
    assert await turn.respond("", native=[args]) is True

    assert len(turn.sends) == 1
    assert turn.sends[0].to == "lapis"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"to": "lapis", "message": "here is the plan"},
        {"to": "lapis", "message": "here is the plan", "wait": "false"},
        {"to": "lapis", "message": "here is the plan", "wait": False},
    ],
)
async def test_native_hub_msg_without_wait_goes_back_to_the_model(turn, args):
    assert await turn.respond("", native=[args]) is False


@pytest.mark.asyncio
async def test_attributes_jammed_into_to_are_read_in_any_order(turn):
    await turn.respond(
        "", native=[{"to": "force='true' wait='true' to='lapis'", "message": "x"}]
    )

    assert turn.sends[0].to == "lapis"
    assert turn.sends[0].force is True
    assert turn.sends[0].metadata["wait"] is True


@pytest.mark.asyncio
async def test_jammed_attributes_beyond_to_wait_force_are_not_guessed_at(turn):
    """A kind or thread_id in `to` stays a literal target and fails, as it always did."""
    await turn.respond("", native=[{"to": 'to="lapis" kind="answer"', "message": "x"}])

    assert turn.sends[0].to == 'to="lapis" kind="answer"'


@pytest.mark.asyncio
async def test_a_rejected_native_send_does_not_end_the_turn(tmp_path):
    turn = _Turn(tmp_path, rejections=[("lapis", "cooldown active")])

    ended = await turn.respond(
        "", native=[{"to": "lapis", "message": "report sent", "wait": "true"}]
    )

    assert ended is False


@pytest.mark.asyncio
async def test_a_native_wait_beside_a_native_failure_keeps_the_turn_going(turn):
    ended = await turn.respond(
        "",
        native=[
            {"to": "lapis", "message": "report sent", "wait": "true"},
            {"to": "lapis", "message": ""},
        ],
    )

    assert ended is False


# --- the result the handler hands back -------------------------------------------


@pytest.mark.asyncio
async def test_the_result_flags_end_turn_and_the_flag_is_not_sent_to_the_peer(turn):
    result = await turn.hub._handle_hub_msg_tool(
        {"id": "t1", "to": "lapis", "content": "report sent", "wait": "true"}
    )

    assert result.success
    assert result.metadata["end_turn"] is True
    assert result.metadata["wait"] is True
    routed = turn.sends[0]
    assert routed.metadata["wait"] is True
    assert "end_turn" not in routed.metadata


@pytest.mark.asyncio
async def test_no_flag_without_wait_and_none_on_a_failure(tmp_path):
    turn = _Turn(tmp_path)
    plain = await turn.hub._handle_hub_msg_tool(
        {"id": "t1", "to": "lapis", "content": "here is the plan"}
    )
    assert plain.success
    assert "end_turn" not in plain.metadata
    assert plain.metadata["wait"] is False

    turn.hub._route_message = AsyncMock(return_value=[("lapis", "cooldown active")])
    failed = await turn.hub._handle_hub_msg_tool(
        {"id": "t2", "to": "lapis", "content": "another plan", "wait": "true"}
    )
    assert not failed.success
    assert "end_turn" not in failed.metadata
