"""Semantic DisplayTap events - the machine-readable mirror of rendered output.

Attach clients that aren't terminals (kollabor-engine, the web UI) can't consume
``{"type": "output", "rendered": "<ansi>"}``. These tests cover the structured
events published alongside it, using the same names as ``kollabor_engine.sse``.
"""

from __future__ import annotations

import asyncio
import queue
from typing import Any, Dict

import pytest

from kollabor_tui.display_tap import DisplayTap, publish_semantic, resolve_tap


def _drain(q: queue.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


class TestResolveTap:
    """Callers hold an event bus, a renderer, or the tap itself - never one shape."""

    def test_resolves_from_tap_bus_and_renderer(self):
        tap = DisplayTap()

        class Bus:
            def get_service(self, name):
                return tap if name == "display_tap" else None

        class Renderer:
            class message_coordinator:  # noqa: N801 - mimics the real attribute
                _display_tap = tap

        assert resolve_tap(tap) is tap
        assert resolve_tap(Bus()) is tap
        assert resolve_tap(Renderer()) is tap

    def test_missing_tap_is_not_an_error(self):
        """A plain TUI run has no tap - publishing must be a silent no-op."""
        assert resolve_tap(None) is None
        assert resolve_tap(object()) is None
        publish_semantic(None, "token", text="x")
        publish_semantic(object(), "token", text="x")

    def test_broken_tap_never_breaks_the_turn(self):
        class Exploding:
            def publish(self, event):
                raise RuntimeError("tap exploded")

        publish_semantic(Exploding(), "token", text="x")


class TestEventShape:
    def test_matches_engine_sse_vocabulary(self):
        tap = DisplayTap()
        q = tap.subscribe("test")

        publish_semantic(tap, "token", text="hi")
        publish_semantic(
            tap,
            "turn_complete",
            input_tokens=5,
            output_tokens=2,
            stop_reason="end_turn",
        )

        events = _drain(q)
        assert [e["type"] for e in events] == ["token", "turn_complete"]
        assert events[0]["text"] == "hi"
        assert events[1]["input_tokens"] == 5
        assert events[1]["stop_reason"] == "end_turn"
        assert all("ts" in e for e in events)


class TestToolExecutorEmitsEvents:
    """Every tool path funnels through ToolExecutor.execute_tool - XML tools from
    queue_processor and native calls from native_tools_handler both land here, so
    instrumenting it once covers all of them."""

    @pytest.mark.asyncio
    async def test_real_tool_run_emits_start_and_result(self, tmp_path):
        from kollabor_agent.tool_executor import ToolExecutor

        tap = DisplayTap()

        class Bus:
            def get_service(self, name):
                return tap if name == "display_tap" else None

            async def emit_with_hooks(self, *args, **kwargs) -> Dict[str, Any]:
                return {}

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=Bus(),
            terminal_timeout=15,
            workspace=str(tmp_path),
        )
        q = tap.subscribe("test")

        result = await executor.execute_tool(
            {"type": "terminal", "id": "t1", "command": "echo semantic-tap-ok"}
        )

        events = {e["type"]: e for e in _drain(q)}
        assert "tool_start" in events, "tool_start not published"
        assert "tool_result" in events, "tool_result not published"
        assert events["tool_start"]["tool_id"] == "t1"
        assert events["tool_start"]["tool_type"] == "terminal"
        assert events["tool_result"]["success"] is result.success
        assert "semantic-tap-ok" in events["tool_result"]["output"]

    @pytest.mark.asyncio
    async def test_result_event_fires_on_early_return(self, tmp_path):
        """execute_tool has several early returns (cancel, scope, permission).
        The wrapper must still report a result for each of them."""
        from kollabor_agent.tool_executor import ToolExecutor

        tap = DisplayTap()

        class Bus:
            def get_service(self, name):
                return tap if name == "display_tap" else None

            async def emit_with_hooks(self, *args, **kwargs) -> Dict[str, Any]:
                return {}

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=Bus(),
            workspace=str(tmp_path),
        )
        executor.is_cancelled = lambda: True  # shortest early-return path
        q = tap.subscribe("test")

        await executor.execute_tool(
            {"type": "terminal", "id": "t2", "command": "echo never-runs"}
        )

        events = {e["type"]: e for e in _drain(q)}
        assert events["tool_result"]["success"] is False
        assert events["tool_result"]["tool_id"] == "t2"


class TestStreamingHandlerEmitsTokens:
    @pytest.mark.asyncio
    async def test_chunks_become_token_events(self):
        """handle_chunk is the real streaming entry point the API calls back into."""
        from kollabor.llm.streaming_handler import StreamingHandler

        tap = DisplayTap()

        class Coordinator:
            _display_tap = tap

            def display_message_sequence(self, *args, **kwargs):
                pass

        class Renderer:
            message_coordinator = Coordinator()

            def update_thinking(self, *args, **kwargs):
                pass

        class DisplayService:
            def start_streaming_response(self):
                pass

            def is_streaming_active(self):
                return False

        handler = StreamingHandler(
            api_service=None,
            message_display_service=DisplayService(),
            renderer=Renderer(),
        )
        q = tap.subscribe("test")

        await handler.handle_chunk("Hello ")
        await handler.handle_chunk("world")

        tokens = [e for e in _drain(q) if e["type"] == "token"]
        assert [t["text"] for t in tokens] == ["Hello ", "world"]


class TestToolCountAccounting:
    """turn_complete reports tools for the whole turn, not the last pass.

    A turn spans several continuation passes: the model asks for tools, they
    run, results go back, the model answers. Counting at the executor means the
    total survives that loop.
    """

    @pytest.mark.asyncio
    async def test_count_accumulates_across_passes_and_resets(self, tmp_path):
        from kollabor_agent.tool_executor import ToolExecutor

        tap = DisplayTap()

        class Bus:
            def get_service(self, name):
                return tap if name == "display_tap" else None

            async def emit_with_hooks(self, *args, **kwargs) -> Dict[str, Any]:
                return {}

        executor = ToolExecutor(
            mcp_integration=None, event_bus=Bus(), workspace=str(tmp_path)
        )
        assert executor.take_executed_count() == 0

        for i in range(3):
            await executor.execute_tool(
                {"type": "terminal", "id": f"t{i}", "command": "echo x"}
            )

        assert executor.take_executed_count() == 3
        # Consuming resets, so the next turn starts from zero
        assert executor.take_executed_count() == 0

    @pytest.mark.asyncio
    async def test_failed_tools_still_count(self, tmp_path):
        from kollabor_agent.tool_executor import ToolExecutor

        class Bus:
            def get_service(self, name):
                return None

            async def emit_with_hooks(self, *args, **kwargs) -> Dict[str, Any]:
                return {}

        executor = ToolExecutor(
            mcp_integration=None, event_bus=Bus(), workspace=str(tmp_path)
        )
        executor.is_cancelled = lambda: True

        await executor.execute_tool({"type": "terminal", "id": "t1", "command": "x"})
        assert executor.take_executed_count() == 1


class TestSendMessage:
    """state.send_message must return on acceptance, not on turn completion."""

    @pytest.mark.asyncio
    async def test_returns_before_the_turn_finishes(self):
        from kollabor.state.local import LocalStateService

        turn_started = asyncio.Event()
        turn_may_finish = asyncio.Event()

        class SlowLLM:
            is_processing = False

            async def process_user_input(self, message, pre_displayed=False):
                turn_started.set()
                await turn_may_finish.wait()
                return {"ok": True}

        svc = LocalStateService.__new__(LocalStateService)
        svc._llm_service = SlowLLM()

        result = await svc.send_message("hello")
        assert result["accepted"] is True

        await asyncio.wait_for(turn_started.wait(), timeout=2)
        turn_may_finish.set()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_rejects_empty_and_busy(self):
        from kollabor.state.local import LocalStateService

        svc = LocalStateService.__new__(LocalStateService)
        svc._llm_service = None
        assert (await svc.send_message("   "))["accepted"] is False
        assert (await svc.send_message("hi"))["accepted"] is False

        class Busy:
            is_processing = True

            async def process_user_input(self, message, pre_displayed=False):
                raise AssertionError("must not run while a turn is in flight")

        svc._llm_service = Busy()
        busy = await svc.send_message("hi")
        assert busy["accepted"] is False
        assert "in flight" in busy["reason"]
