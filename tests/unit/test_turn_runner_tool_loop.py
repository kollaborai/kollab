"""Regression tests for engine agentic turn continuation."""

import asyncio
from types import SimpleNamespace

import pytest
from kollabor_engine import turn_runner as turn_runner_mod
from kollabor_engine.turn_runner import MAX_AGENTIC_TURNS, TurnRunner


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
        self.plugin_handlers = {}

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
    assert tool_call["arguments"] == {"file": "README.md", "limit": 5}
    assert any(event["type"] == "tool_start" for event in events)


class FakeMcpToolAPIService(FakeAPIService):
    async def call_llm(self, conversation_history, streaming_callback, tools):
        self.calls += 1
        self.last_thinking_content = None
        self.last_token_usage = {"prompt_tokens": 1, "completion_tokens": 1}

        if self.calls == 1:
            self.last_stop_reason = "tool_use"
            self.last_tool_calls = [
                {
                    "id": "call_browser",
                    "type": "tool_use",
                    "name": "browser_get_page",
                    "input": {"tab": "active"},
                }
            ]
            return ""

        self.last_stop_reason = "end_turn"
        self.last_tool_calls = []
        await streaming_callback("got page")
        return "got page"


class FakeMcpToolSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.api_service = FakeMcpToolAPIService()
        self.mcp_integration = SimpleNamespace(
            tool_registry={"browser_get_page": {"server": "browser"}}
        )


@pytest.mark.asyncio
async def test_mcp_native_tool_call_stays_mcp_tool_when_registered_by_mcp():
    session = FakeMcpToolSession()
    await anext(TurnRunner().run(session, "check page"))

    assert session.tool_executor.calls
    tool_call = session.tool_executor.calls[0]
    assert tool_call["type"] == "mcp_tool"
    assert tool_call["name"] == "browser_get_page"
    assert tool_call["arguments"] == {"tab": "active"}


@pytest.mark.asyncio
async def test_direct_engine_tool_execution_normalizes_provider_object():
    session = FakeBuiltInToolSession()
    raw_tool_call = SimpleNamespace(
        id="call_direct",
        type="tool_use",
        name="file_read",
        input={"file": "README.md", "limit": 5},
    )

    await TurnRunner()._execute_tool(session, raw_tool_call, asyncio.Queue())

    assert session.tool_executor.calls
    tool_call = session.tool_executor.calls[0]
    assert tool_call["type"] == "file_read"
    assert tool_call["name"] == "file_read"
    assert tool_call["input"] == {"file": "README.md", "limit": 5}


# --------------------------------------------------------------------------- #
# Regression: MAX_AGENTIC_TURNS=20 silently truncated long investigations      #
# mid-chain (the "engine stops tool calls" bug, 2026-07-03)                    #
# --------------------------------------------------------------------------- #


class LongChainAPIService(FakeAPIService):
    """Emits a tool call for the first `tool_turns` model passes, then ends."""

    def __init__(self, tool_turns):
        super().__init__()
        self.tool_turns = tool_turns

    async def call_llm(self, conversation_history, streaming_callback, tools):
        self.calls += 1
        self.last_thinking_content = None
        self.last_token_usage = {"prompt_tokens": 1, "completion_tokens": 1}

        if self.calls <= self.tool_turns:
            self.last_stop_reason = "tool_use"
            self.last_tool_calls = [
                {
                    "id": f"call_{self.calls}",
                    "type": "tool_use",
                    "name": "get_current_page",
                    "input": {},
                }
            ]
            return ""

        self.last_stop_reason = "end_turn"
        self.last_tool_calls = []
        await streaming_callback("done investigating.")
        return "done investigating."


class LongChainSession(FakeSession):
    def __init__(self, tool_turns):
        super().__init__()
        self.api_service = LongChainAPIService(tool_turns)


@pytest.mark.asyncio
async def test_long_investigation_past_old_cap_completes_naturally():
    """A 30-tool-call chain must finish; the old cap of 20 cut it off silently.

    The model calls a tool 30 times, then completes. Under the old
    MAX_AGENTIC_TURNS=20 the loop ended at turn 20 with tools still
    pending — the client saw a truncated turn and had to hit "continue".
    """
    session = LongChainSession(tool_turns=30)
    events = [event async for event in TurnRunner().run(session, "investigate")]

    # 30 tool turns + 1 final completion pass.
    assert session.api_service.calls == 31
    assert len(session.tool_executor.calls) == 30
    assert events[-1]["type"] == "turn_complete"
    assert events[-1]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_cap_raised_well_above_typical_investigation_depth():
    """The runaway backstop must clear real investigation depth (was 20)."""
    assert MAX_AGENTIC_TURNS >= 100


@pytest.mark.asyncio
async def test_hitting_the_cap_logs_and_stops_without_orphaning(monkeypatch, caplog):
    """When the cap IS hit, the engine logs a warning and stops cleanly —
    tool results stay in history so the client can continue."""
    monkeypatch.setattr(turn_runner_mod, "MAX_AGENTIC_TURNS", 3)
    # Model never stops calling tools → cap is the only exit.
    session = LongChainSession(tool_turns=999)

    import logging

    with caplog.at_level(logging.WARNING):
        events = [event async for event in TurnRunner().run(session, "loop")]

    assert session.api_service.calls == 3  # exactly the (patched) cap
    assert any("MAX_AGENTIC_TURNS" in r.message for r in caplog.records)
    # Turn still completes cleanly (no exception) and the tool results the
    # model produced are in history for a follow-up "continue".
    assert events[-1]["type"] == "turn_complete"
    assert any(m["role"] == "tool" for m in session.history)
