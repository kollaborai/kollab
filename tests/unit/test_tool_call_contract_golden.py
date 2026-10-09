"""Golden contract tests for tool-call routing and history shape."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from kollabor.tool_contract_proof import collect_tool_contract_proofs
from kollabor_agent.queue_processor import QueueProcessor
from kollabor_agent.tool_call_contract import (
    normalize_native_tool_call,
    resolve_text_tool_call,
)
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_ai.response_parser import ResponseParser
from kollabor_events.data_models import ConversationMessage


def test_xml_native_and_mcp_normalize_to_executor_shape():
    parser = ResponseParser()
    parsed = parser.parse_response(
        "<terminal>pwd</terminal>\n"
        "<read><file>README.md</file></read>\n"
        '<tool name="browser_get_page" tab="active">inspect</tool>'
    )

    tools = parser.get_all_tools(parsed)
    by_type = {tool["type"]: tool for tool in tools}

    assert by_type["terminal"]["command"] == "pwd"
    assert by_type["file_read"]["file"] == "README.md"
    assert by_type["mcp_tool"]["name"] == "browser_get_page"
    assert by_type["mcp_tool"]["arguments"]["tab"] == "active"

    native_plugin = normalize_native_tool_call(
        SimpleNamespace(id="call_state", name="state_update", input={"state": "ok"}),
        plugin_handler_names={"state_update"},
    )
    assert native_plugin == {
        "type": "state_update",
        "id": "call_state",
        "name": "state_update",
        "input": {"state": "ok"},
        "arguments": {"state": "ok"},
        "state": "ok",
    }

    native_hub_spawn = normalize_native_tool_call(
        SimpleNamespace(
            id="call_spawn",
            name="hub_spawn",
            type="function",
            input={"name": "lapis", "type": "research", "task": "audit"},
        ),
        plugin_handler_names={"hub_spawn"},
    )
    assert native_hub_spawn["type"] == "hub_spawn"
    assert native_hub_spawn["name"] == "hub_spawn"
    assert native_hub_spawn["input"] == {
        "name": "lapis",
        "type": "research",
        "task": "audit",
    }
    assert native_hub_spawn["task"] == "audit"
    assert "name" not in {
        key for key in native_hub_spawn if key not in {"name", "input", "arguments"}
    }

    native_mcp = normalize_native_tool_call(
        SimpleNamespace(
            id="call_mcp", name="browser_get_page", input={"tab": "active"}
        ),
        mcp_tool_names={"browser_get_page"},
    )
    assert native_mcp == {
        "type": "mcp_tool",
        "id": "call_mcp",
        "name": "browser_get_page",
        "input": {"tab": "active"},
        "arguments": {"tab": "active"},
    }

    native_git = normalize_native_tool_call(
        SimpleNamespace(
            id="call_git",
            name="git",
            input={"command": "git status --short"},
        )
    )
    assert native_git["type"] == "terminal"
    assert native_git["name"] == "git"
    assert native_git["command"] == "git status --short"

    native_tool_load = normalize_native_tool_call(
        SimpleNamespace(
            id="call_load",
            name="tool_load",
            input={"name": "mcp:zai-mcp-server:analyze_image"},
        )
    )
    assert native_tool_load["type"] == "tool_load"
    assert native_tool_load["name"] == "tool_load"
    assert native_tool_load["parameters"] == {
        "name": "mcp:zai-mcp-server:analyze_image"
    }


def test_doctor_contract_probe_reports_stable_proof_labels():
    assert collect_tool_contract_proofs() == [
        ("proof xml", "file_read normalized"),
        ("proof mock-mcp", "doctor_ping normalized"),
        ("proof native", "state_update normalized"),
    ]


def test_tool_result_conversation_format_is_stable():
    result = ToolExecutionResult(
        tool_id="terminal_1",
        tool_type="terminal",
        success=True,
        output="ok",
    )

    executor = SimpleNamespace()
    from kollabor_agent.tool_executor import ToolExecutor

    assert ToolExecutor.format_result_for_conversation(executor, result) == (
        "[terminal] ok"
    )

    failed = ToolExecutionResult(
        tool_id="mcp_1",
        tool_type="mcp_tool",
        success=False,
        error="permission denied",
    )
    assert ToolExecutor.format_result_for_conversation(executor, failed) == (
        "[mcp_tool] ERROR: permission denied"
    )


class FakeToolCall:
    id = "call_native"
    name = "state_update"
    input = {"state": "working"}


class FakeApiService:
    model = "test-model"
    last_stop_reason = "stop"
    last_thinking_content = ""
    provider_type = "test"

    def __init__(self):
        self._tool_calls = [FakeToolCall()]

    def has_pending_tool_calls(self):
        return True

    def get_last_tool_calls(self):
        return self._tool_calls

    def get_last_token_usage(self):
        return {}

    def format_tool_result(self, tool_call_id, result, is_error=False):
        return {
            "role": "tool",
            "content": result,
            "tool_call_id": tool_call_id,
            "is_error": is_error,
        }


class FakeNativeToolsHandler:
    tool_calling_enabled = True
    tools = [{"type": "function", "function": {"name": "state_update"}}]

    def __init__(self):
        self.discovery_complete = asyncio.Event()
        self.discovery_complete.set()

    async def execute_tool_calls(self, tool_executor):
        return [
            ToolExecutionResult(
                tool_id="call_native",
                tool_type="state_update",
                success=True,
                output="state saved",
                execution_time=0.2504,
            )
        ]


class FailingNativeToolsHandler(FakeNativeToolsHandler):
    async def execute_tool_calls(self, tool_executor):
        return [
            ToolExecutionResult(
                tool_id="call_native",
                tool_type="state_update",
                success=False,
                error="Command exited with code 1",
                execution_time=0.01,
            )
        ]


class FakeToolExecutor:
    def format_result_for_conversation(self, result):
        output = result.output if result.success else f"ERROR: {result.error}"
        return f"[{result.tool_type}] {output}"

    def is_cancelled(self):
        return False

    async def execute_tool(self, tool_data):
        return ToolExecutionResult(
            tool_id=tool_data["id"],
            tool_type=tool_data["type"],
            success=True,
            output="xml saved",
        )


async def _run_mixed_turn(native_tools_handler):
    conversation_history: list[ConversationMessage] = []
    added_messages: list[ConversationMessage] = []
    api_service = FakeApiService()
    response_parser = ResponseParser()
    conversation_logger = AsyncMock()
    conversation_logger.log_assistant_message.return_value = "assistant-parent"

    def add_message(message, parent_uuid=None):
        conversation_history.append(message)
        added_messages.append(message)

    processor = QueueProcessor(
        conversation_history=conversation_history,
        session_stats={
            "messages": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        },
        stats={"total_thinking_time": 0},
        queue_metrics={},
        task_config=SimpleNamespace(
            queue=SimpleNamespace(
                overflow_strategy="drop_oldest",
                log_queue_events=False,
                enable_queue_metrics=False,
                block_timeout=None,
            )
        ),
        api_service=api_service,
        tool_executor=FakeToolExecutor(),
        response_parser=response_parser,
        message_display_service=MagicMock(),
        renderer=MagicMock(),
        config=SimpleNamespace(get=lambda key, default=None: 0),
        event_bus=SimpleNamespace(
            get_service=lambda name: None,
            emit_with_hooks=AsyncMock(return_value={}),
        ),
        conversation_logger=conversation_logger,
        streaming_handler=SimpleNamespace(
            call_llm=AsyncMock(
                return_value=("doing both\n" "<read><file>README.md</file></read>")
            )
        ),
        native_tools_handler=native_tools_handler,
        add_message_fn=add_message,
        max_queue_size=10,
    )

    await processor._execute_llm_turn(
        user_message_provided=False,
        current_parent_uuid="root",
    )

    return conversation_history


def test_mixed_native_and_xml_tool_history_shape_is_stable():
    history = asyncio.run(_run_mixed_turn(FakeNativeToolsHandler()))

    assistant = history[0]
    assert assistant.role == "assistant"
    assert assistant.metadata["tool_calls"] == [
        {
            "id": "call_native",
            "type": "function",
            "function": {
                "name": "state_update",
                "arguments": json.dumps({"state": "working"}),
            },
        }
    ]

    native_result = history[1]
    assert native_result.role == "tool"
    assert native_result.content == "state saved"
    assert native_result.metadata == {
        "tool_call_id": "call_native",
        "tool_execution_time": 0.25,
    }

    xml_result = history[2]
    assert xml_result.role == "user"
    assert xml_result.content == "Tool result: [file_read] xml saved"


def test_failed_native_tool_result_is_flagged_in_history():
    """The web history reads `is_error`; only a failed call carries it."""
    history = asyncio.run(_run_mixed_turn(FailingNativeToolsHandler()))

    assert history[1].metadata == {
        "tool_call_id": "call_native",
        "tool_execution_time": 0.01,
        "is_error": True,
    }


def test_native_arguments_holding_xml_attributes_are_split():
    """GLM 5.3 writes the rest of the documented XML tag into one argument."""

    def call(name, arguments):
        tool_call = SimpleNamespace(id="call_1", name=name, input=arguments)
        return normalize_native_tool_call(tool_call, plugin_handler_names={name})

    spawned = call("hub_spawn", {"name": 'coder" task="ROLE: builder. Return "429" when over.'})
    assert spawned["input"] == {"name": "coder", "task": 'ROLE: builder. Return "429" when over.'}
    assert spawned["task"] == 'ROLE: builder. Return "429" when over.'

    body = call("hub_spawn", {"name": 'coder">ROLE: builder'})
    assert body["input"] == {"name": "coder", "task": "ROLE: builder"}

    cron = call("hub_cron_add", {"interval": '5m" to="koordinator', "message": "check in"})
    assert cron["input"] == {"interval": "5m", "to": "koordinator", "message": "check in"}


def test_native_arguments_with_ordinary_quotes_are_left_alone():
    def call(name, arguments):
        tool_call = SimpleNamespace(id="call_1", name=name, input=arguments)
        return normalize_native_tool_call(tool_call, plugin_handler_names={name})["input"]

    message = {"to": "lapis", "message": 'say "hi" then wait=">" for me'}
    assert call("hub_msg", message) == message
    undeclared = {"name": 'coder" model="glm-5.3'}  # not a hub_spawn parameter
    assert call("hub_spawn", undeclared) == undeclared
    kept = {"name": 'coder" task="other', "task": "the real task"}  # task already given
    assert call("hub_spawn", kept) == kept
    command = {"command": 'echo ">" && grep "a" b.txt'}
    assert normalize_native_tool_call(SimpleNamespace(id="c", name="terminal", input=command))["input"] == command


def test_a_native_call_written_as_text_runs_as_that_tool():
    # gpt-5.6-luna wrote this into its reply instead of calling hub_msg (live
    # story 7, 2026-10-09): nothing was sent, and the reply said it was.
    parser = ResponseParser()
    parsed = parser.parse_response(
        "On it.\n"
        '<functions.hub_msg>{"to":"koordinator@server","message":"What does '
        '`uname -n` print?","wait":"true","force":"true","thread_id":"",'
        '"reply_to":"","kind":"message"}</functions.hub_msg>'
        "The remote query has been sent."
    )

    assert "<functions." not in parsed["content"]
    [call] = [
        resolve_text_tool_call(tool, plugin_handler_names={"hub_msg"})
        for tool in parser.get_all_tools(parsed)
    ]
    assert call["type"] == "hub_msg"
    assert call["to"] == "koordinator@server"
    assert call["message"] == "What does `uname -n` print?"
    assert call["wait"] == "true"
    assert call["raw"].startswith("<functions.hub_msg>")


def test_text_calls_go_only_to_builtin_and_plugin_tools():
    parser = ResponseParser()
    parsed = parser.parse_response(
        '<tool_call>{"name": "git", "arguments": {"command": "echo `git status`"}}</tool_call>\n'
        '<functions.browser_get_page>{"tab": "active"}</functions.browser_get_page>\n'
        '<functions.hub_msg>{"to": "lapis",</functions.hub_msg>\n'
        '<tool name="hub_msg" to="lapis">hello</tool>'
    )

    tools = [
        resolve_text_tool_call(
            tool, mcp_tool_names={"browser_get_page"}, plugin_handler_names={"hub_msg"}
        )
        for tool in parser.get_all_tools(parsed)
    ]
    by_call = {(tool["type"], tool.get("name")): tool for tool in tools}

    # Backticks arrive as code-span placeholders and come back intact.
    assert by_call[("terminal", "git")]["command"] == "echo `git status`"
    assert by_call[("mcp_tool", "browser_get_page")]["arguments"] == {"tab": "active"}
    # Dispatching on the name alone would drop the tag body, so it stays MCP.
    assert by_call[("mcp_tool", "hub_msg")]["content"] == "hello"
    [malformed] = [tool for tool in tools if tool["type"] == "malformed_tool"]
    assert malformed["raw"] == '<functions.hub_msg>{"to": "lapis",</functions.hub_msg>'


def test_a_tool_call_block_without_a_name_is_not_a_tool_named_unknown():
    # gpt-5.6-luna called hub_msg natively and ended its reply with the same
    # arguments as a nameless <tool_call> (live story 7, 2026-10-09).
    parser = ResponseParser()
    [call] = parser.get_all_tools(
        parser.parse_response(
            "Sent.\n"
            '<tool_call>{"to":"koordinator@server","message":"Find out what '
            '`uname -n` prints","wait":"true","force":"false","thread_id":"",'
            '"reply_to":"","kind":"message"}</tool_call>'
        )
    )

    assert call["type"] == "malformed_tool"
    assert call["error"] == "the call names no tool"
    assert call["raw"].startswith("<tool_call>")


def test_a_text_call_that_cannot_be_read_tells_the_model_why():
    mcp = MagicMock()
    mcp.tool_registry = {}
    mcp.server_connections = {}
    event_bus = MagicMock()
    event_bus.emit_with_hooks = AsyncMock(return_value=None)
    executor = ToolExecutor(
        mcp_integration=mcp, event_bus=event_bus, terminal_timeout=5, mcp_timeout=10
    )
    parser = ResponseParser()
    [call] = parser.get_all_tools(
        parser.parse_response('<functions.hub_msg>{"to": "lapis",</functions.hub_msg>')
    )

    result = asyncio.run(executor.execute_tool(call))

    assert not result.success
    assert result.error.startswith("Could not read this tool call (")
    assert '<functions.hub_msg>{"to": "lapis",' in result.error
