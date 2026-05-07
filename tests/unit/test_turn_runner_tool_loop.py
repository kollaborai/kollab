"""Regression tests for engine agentic turn continuation."""

from types import SimpleNamespace

import pytest

from kollabor_engine.turn_runner import TurnRunner


class FakeAPIService:
    def __init__(self):
        self.calls = 0
        self.last_thinking_content = None
        self.last_token_usage = {}
        self.last_tool_calls = []
        self.last_stop_reason = ""

    async def call_llm(self, conversation_history, streaming_callback, tools):
        self.calls += 1
        self.last_thinking_content = None
        self.last_token_usage = {"prompt_tokens": 1, "completion_tokens": 1}

        if self.calls == 1:
            self.last_stop_reason = "stop"
            self.last_tool_calls = [
                {
                    "id": "call_1",
                    "type": "tool_use",
                    "name": "get_current_page",
                    "input": {},
                }
            ]
            return ""

        self.last_stop_reason = "end_turn"
        self.last_tool_calls = []
        await streaming_callback("I see the dashboard now.")
        return "I see the dashboard now."

    def format_tool_result(self, tool_id, result, is_error=False):
        return {"role": "tool", "tool_call_id": tool_id, "content": result}


class FakeToolExecutor:
    def __init__(self):
        self.calls = []

    async def execute_tool(self, tool_call):
        self.calls.append(tool_call)
        return SimpleNamespace(
            success=True,
            output='{"page":{"pathname":"/dashboard","ageSec":938}}',
            error="",
            metadata={},
        )


class FakeSession:
    def __init__(self):
        self.session_id = "sess-tool-loop"
        self.history = []
        self.api_service = FakeAPIService()
        self.tool_executor = FakeToolExecutor()
        self.mcp_integration = SimpleNamespace(tool_registry={"get_current_page": {}})
        self.profile = SimpleNamespace(
            provider="openai",
            base_url="",
            get_model=lambda: "gpt-test",
        )
        self.total_turns = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def get_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_current_page",
                    "description": "Return the browser page",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]


@pytest.mark.asyncio
async def test_tool_calls_always_continue_to_model_even_when_stop_reason_is_stop():
    session = FakeSession()
    events = [event async for event in TurnRunner().run(session, "hi")]

    assert session.api_service.calls == 2
    assert any(
        event["type"] == "token" and "dashboard" in event["text"]
        for event in events
    )
    assert events[-1]["type"] == "turn_complete"
    assert events[-1]["stop_reason"] == "end_turn"


class FakeBuiltInToolAPIService(FakeAPIService):
    async def call_llm(self, conversation_history, streaming_callback, tools):
        self.calls += 1
        self.last_thinking_content = None
        self.last_token_usage = {"prompt_tokens": 1, "completion_tokens": 1}

        if self.calls == 1:
            self.last_stop_reason = "tool_use"
            self.last_tool_calls = [
                {
                    "id": "call_file",
                    "type": "tool_use",
                    "name": "file_read",
                    "input": {"file": "README.md", "limit": 5},
                }
            ]
            return ""

        self.last_stop_reason = "end_turn"
        self.last_tool_calls = []
        await streaming_callback("read it")
        return "read it"


class FakeBuiltInToolSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.api_service = FakeBuiltInToolAPIService()
        self.mcp_integration = SimpleNamespace(tool_registry={})


@pytest.mark.asyncio
async def test_builtin_native_tool_calls_route_to_builtin_executor_type():
    session = FakeBuiltInToolSession()
    events = [event async for event in TurnRunner().run(session, "read README")]

    assert session.tool_executor.calls
    tool_call = session.tool_executor.calls[0]
    assert tool_call["type"] == "file_read"
    assert tool_call["name"] == "file_read"
    assert tool_call["file"] == "README.md"
    assert tool_call["limit"] == 5
    assert tool_call["arguments"] == {"file": "README.md", "limit": 5}
    assert any(event["type"] == "tool_start" for event in events)
