"""Golden contract tests for tool-call routing and history shape."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from kollabor.tool_contract_proof import collect_tool_contract_proofs
from kollabor_agent.queue_processor import QueueProcessor
from kollabor_agent.tool_call_contract import normalize_native_tool_call
from kollabor_agent.tool_executor import ToolExecutionResult
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

    native_mcp = normalize_native_tool_call(
        SimpleNamespace(id="call_mcp", name="browser_get_page", input={"tab": "active"}),
        mcp_tool_names={"browser_get_page"},
    )
    assert native_mcp == {
        "type": "mcp_tool",
        "id": "call_mcp",
        "name": "browser_get_page",
        "input": {"tab": "active"},
        "arguments": {"tab": "active"},
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


def test_mixed_native_and_xml_tool_history_shape_is_stable():
    async def run_turn():
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
            pending_tools=[],
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
                    return_value=(
                        "doing both\n"
                        "<read><file>README.md</file></read>"
                    )
                )
            ),
            native_tools_handler=FakeNativeToolsHandler(),
            add_message_fn=add_message,
            max_history=50,
            question_gate_enabled=False,
            max_queue_size=10,
        )

        await processor._execute_llm_turn(
            user_message_provided=False,
            current_parent_uuid="root",
        )

        return conversation_history

    history = asyncio.run(run_turn())

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
    assert native_result.metadata == {"tool_call_id": "call_native"}

    xml_result = history[2]
    assert xml_result.role == "user"
    assert xml_result.content == "Tool result: [file_read] xml saved"
