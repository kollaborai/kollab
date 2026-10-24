"""A cancelled ACP turn keeps the reply streamed before the cancel, once (#266)."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from kollabor.llm import acp_orchestrator as mod
from kollabor.state.local import LocalStateService
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_ai.profile_manager import LLMProfile

# Streams "chunk N. " every 50 ms until cancelled. A prompt containing "late"
# flushes three more chunks after the cancel arrives, before it answers
# "cancelled": what a real adapter does with output already in flight.
STREAMING_AGENT = r"""
import asyncio, acp
from acp.schema import (
    AgentMessageChunk, AgentThoughtChunk, InitializeResponse, NewSessionResponse,
    PromptResponse, TextContentBlock, ToolCallProgress, ToolCallStart,
)
class Agent:
    def on_connect(self, connection):
        self.connection = connection
        self.cancelled = asyncio.Event()
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, **kwargs):
        return NewSessionResponse(sessionId="stream-session")
    async def say(self, session_id, text):
        await self.connection.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk",
            content=TextContentBlock(type="text", text=text)))
    async def prompt(self, session_id, prompt, **kwargs):
        self.cancelled.clear()
        text = "\n".join(getattr(block, "text", "") for block in prompt)
        for n in range(1, 200):
            await self.say(session_id, f"chunk {n}. ")
            try:
                await asyncio.wait_for(self.cancelled.wait(), 0.05)
            except asyncio.TimeoutError:
                continue
            if "late" in text:
                await self.connection.session_update(session_id, AgentThoughtChunk(
                    sessionUpdate="agent_thought_chunk",
                    content=TextContentBlock(type="text", text="late thought")))
                await self.connection.session_update(session_id, ToolCallStart(
                    sessionUpdate="tool_call", toolCallId="t1", title="Late tool",
                    kind="read", status="in_progress"))
                for k in range(1, 4):
                    await self.say(session_id, f"late {k}. ")
                await self.connection.session_update(session_id, ToolCallProgress(
                    sessionUpdate="tool_call_update", toolCallId="t1", status="failed"))
            return PromptResponse(stopReason="cancelled")
        return PromptResponse(stopReason="end_turn")
    async def cancel(self, session_id, **kwargs):
        self.cancelled.set()
asyncio.run(acp.run_agent(Agent()))
"""


class Tap:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class Bus:
    def __init__(self):
        self.services = {"display_tap": Tap()}

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service


async def cancelled_turn(tmp_path, monkeypatch, prompt):
    """Run one turn against the streaming agent, cancel after 3 chunks."""
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")
    configuration = SimpleNamespace(
        _conversations_dir=tmp_path / "conversations", get=lambda key, default=None: default
    )
    logger = KollaborConversationLogger(configuration._conversations_dir)
    manager = ConversationManager(configuration, logger)
    llm = SimpleNamespace(conversation_manager=manager, conversation_logger=logger, conversation_history=[])
    bus = Bus()
    bus.services["llm_service"] = llm
    orchestrator = mod.ACPOrchestrator(bus)
    (tmp_path / "home").mkdir()
    profile = LLMProfile(name="stream", provider="acp", command=(sys.executable, "-c", STREAMING_AGENT),
                         cwd=str(tmp_path), env={"HOME": str(tmp_path / "home")})
    events = bus.services["display_tap"].events
    try:
        await orchestrator.start(profile)
        orchestrator.admit(prompt, request_id="stopped", source="rpc")
        async with asyncio.timeout(10):
            while sum(1 for e in events if e.get("type") == "token") < 3:
                await asyncio.sleep(0.01)
        before = len(events)
        await asyncio.wait_for(orchestrator.cancel(), 15)
        await asyncio.gather(*tuple(orchestrator._completion_tasks))
        snapshot = (await LocalStateService(llm, None).get_conversation()).to_dict()
        assistants = [m for m in snapshot["messages"] if m["role"] == "assistant"]
        return assistants, events, before
    finally:
        await orchestrator.close()


@pytest.mark.asyncio
async def test_cancelled_turn_is_stored_once_with_the_reply_streamed_before_the_cancel(tmp_path, monkeypatch):
    assistants, events, before = await cancelled_turn(tmp_path, monkeypatch, "stream")
    assert len(assistants) == 1
    assert assistants[0]["metadata"]["stop_reason"] == "cancelled"
    streamed = "".join(e["text"] for e in events[:before] if e.get("type") == "token")
    # Everything shown before the cancel is stored; the row is that text, once.
    assert assistants[0]["content"].startswith(streamed)
    assert assistants[0]["content"].count("chunk 1. ") == 1
    assert len([e for e in events if e.get("type") == "turn_complete"]) == 1


@pytest.mark.asyncio
async def test_prose_the_agent_sends_after_the_cancel_is_neither_stored_nor_streamed(tmp_path, monkeypatch):
    assistants, events, before = await cancelled_turn(tmp_path, monkeypatch, "stream late")
    assert len(assistants) == 1
    row = assistants[0]
    assert "late" not in row["content"]
    assert "late thought" not in "".join(row["metadata"]["thinking"])
    after = events[before:]
    assert not [e for e in after if e.get("type") == "token"]
    assert not [e for e in after if e.get("type") == "thinking"]
    # The card the agent reports after the cancel is state, not prose: kept.
    assert [c["status"] for c in row["metadata"]["tool_calls"]] == ["failed"]
