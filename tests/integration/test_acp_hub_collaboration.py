"""Hub/MCP correlation over real Unix sockets and SDK stdio children.

Turn admissions are controlled here; model/adapter execution is qualified by
the separate application runs. Every send uses the hosting Hub's real router.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kollabor_agent.runtime import AgentRuntime
from kollabor_events import EventBus
from plugins.hub.messenger import AgentMessenger, AgentSocketServer
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin
from plugins.hub.presence import PresenceManager


class HubPeer:
    def __init__(self, name: str, workspace: Path, presence_path: Path):
        self.inputs = asyncio.Queue()
        self.received = []
        self.displayed = []
        self.notifications = []
        self.controller = SimpleNamespace(
            active=True, runner=SimpleNamespace(_active_context=None),
            observe_notification=self.notifications.append,
        )
        self.llm = SimpleNamespace(
            is_processing=False, conversation_history=[],
            profile_manager=SimpleNamespace(
                get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "acp")
            ),
            submit_human_input=self.admit,
        )
        bus = EventBus()
        bus.register_service("llm_service", self.llm)
        bus.register_service("acp_orchestrator", self.controller)
        self.hub = HubPlugin(event_bus=bus)
        self.hub._identity = AgentRuntime(name=name, identity=name, project=str(workspace), state="ready")
        self.hub._task_ledger = None
        self.hub._vault = None
        self.hub._nudge_engine = None
        self.hub._display_hub_message = self.displayed.append
        self.hub._display_outgoing_message = lambda *args: None
        self.hub._presence = PresenceManager.__new__(PresenceManager)
        self.hub._presence.identity = self.hub._identity
        self.hub._presence._presence_dir = presence_path
        self.hub._presence._presence_file = presence_path / f"{self.hub._identity.agent_id}.json"
        self.hub._presence._cached_agents = []
        self.hub._presence._cache_time = 0
        self.server = AgentSocketServer(
            self.hub._identity.agent_id, self.receive,
            on_acp_hub_status=self.hub._handle_acp_hub_status,
            on_acp_hub_call=self.hub._handle_acp_hub_call,
        )
        self.session = None
        self.mcp_stop = asyncio.Event()
        self.mcp_task = None
        self.call_tasks = set()

    async def receive(self, message):
        self.received.append(message)
        await self.hub._on_message_received(message)

    async def admit(self, content, **kwargs):
        self.inputs.put_nowait({"content": content, **kwargs})
        return {"status": "queued", "request_id": kwargs["request_id"]}

    async def next(self):
        request = await asyncio.wait_for(self.inputs.get(), 3)
        self.llm.is_processing = True
        self.controller.runner._active_context = SimpleNamespace(
            request_id=request["request_id"], origin=request["origin_context"],
        )
        return request

    async def complete(self, request, text, outcome="end_turn"):
        await self.hub.on_acp_completion(
            request_id=request["request_id"], thread_id=request["thread_id"],
            turn_id=request["request_id"] + "-controlled-turn", source="agent",
            text=text, end_frame={"stopReason": outcome}, failed=outcome != "end_turn",
        )
        self.llm.is_processing = False
        self.controller.runner._active_context = None

    async def call(self, name, arguments):
        self.call_tasks.add(asyncio.current_task())
        response = await asyncio.wait_for(self.session.call_tool(name, arguments), 5)
        if response.isError:
            raise ValueError(response.content[0].text)
        return json.loads(response.content[0].text)


@pytest_asyncio.fixture
async def peers(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="khub-", dir="/tmp") as scratch:
        root = Path(scratch)
        sockets = root / "sockets"
        sockets.mkdir(mode=0o700)
        monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: sockets)
        created = []

        async def create(name, workspace_name="first"):
            workspace = root / workspace_name
            workspace.mkdir(exist_ok=True)
            presence_path = workspace / "presence"
            presence_path.mkdir(mode=0o700, exist_ok=True)
            peer = HubPeer(name, workspace, presence_path)
            created.append(peer)
            peer.hub._identity.socket_path = await peer.server.start()
            peer.hub._presence.publish()
            token, generation = peer.hub.rotate_acp_hub_binding()
            parameters = StdioServerParameters(command=sys.executable, args=["-m", "kollabor.mcp_hub"], env={
                **os.environ, "KOLLAB_ACP_HUB_SOCKET": peer.hub._identity.socket_path,
                "KOLLAB_ACP_HUB_TOKEN": token, "KOLLAB_ACP_GENERATION": str(generation),
            })
            ready = asyncio.get_running_loop().create_future()

            async def transport():
                try:
                    async with stdio_client(parameters) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            ready.set_result(session)
                            await peer.mcp_stop.wait()
                except BaseException as exc:
                    if not ready.done():
                        ready.set_exception(exc)
                    raise

            peer.mcp_task = asyncio.create_task(transport())
            peer.session = await ready
            return peer

        try:
            yield create
        finally:
            for peer in created:
                peer.hub._acp_tools().stop()
            calls = [task for peer in created for task in tuple(peer.call_tasks)]
            await asyncio.gather(*calls, return_exceptions=True)
            for peer in created:
                peer.mcp_stop.set()
            tasks = [peer.mcp_task for peer in created if peer.mcp_task is not None]
            try:
                await asyncio.gather(*tasks)
            finally:
                for peer in created:
                    await peer.server.stop()
                    peer.hub._presence.remove()


@pytest.mark.asyncio
async def test_mcp_request_interim_duplicate_final_and_hidden_end(peers):
    alpha, beta = await peers("alpha"), await peers("beta")
    pending = asyncio.create_task(alpha.call("hub_ask", {"to": "beta", "question": "Give the final answer"}))
    request = await beta.next()
    await beta.call("hub_msg", {"to": "alpha", "thread": request["thread_id"], "message": "checking"})
    interim = next(message for message in alpha.received if message.content == "checking")
    assert await AgentMessenger.send_to_agent(alpha.hub._identity.socket_path, interim)
    assert not pending.done()
    await beta.complete(request, "final answer")
    result = await pending
    assert result == {"status": "answered", "from": "beta", "thread": request["thread_id"],
                      "text": "final answer", "earlier": ["checking"]}
    assert all(message.action != "turn_end" for message in alpha.displayed)
    assert alpha.inputs.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("continue_with_ask", [False, True])
async def test_question_back_pins_its_id_and_continues_original_request(peers, continue_with_ask):
    alpha, beta = await peers("alpha"), await peers("beta")
    first = asyncio.create_task(alpha.call("hub_ask", {"to": "beta", "question": "Original question"}))
    original = await beta.next()
    await beta.call("hub_msg", {"to": "alpha", "thread": original["thread_id"], "message": "checking"})
    back = asyncio.create_task(beta.call("hub_ask", {"to": "alpha", "question": "Which file?"}))
    question = await first
    assert question["status"] == "question" and question["earlier"] == ["checking"]
    received_question = next(message for message in alpha.received if message.content == "Which file?")
    later = HubMessage(from_identity="beta", from_agent=beta.hub._identity.agent_id,
                       to="alpha", content="Later note", thread_id=question["thread"], reply_to="another-message")
    assert await AgentMessenger.send_to_agent(alpha.hub._identity.socket_path, later)
    name, field = ("hub_ask", "question") if continue_with_ask else ("hub_msg", "message")
    answer = asyncio.create_task(alpha.call(name, {"to": "beta", "thread": question["thread"], field: "src/app.py"}))
    assert (await back)["text"] == "src/app.py"
    sent_answer = next(message for message in beta.received if message.content == "src/app.py")
    assert sent_answer.reply_to == received_question.id != later.id
    await beta.complete(original, "Original final")
    result = await answer
    if continue_with_ask:
        assert result["status"] == "answered" and result["text"] == "Original final"
        assert result["thread"] == original["thread_id"] and result["earlier"] == ["checking"]
    else:
        assert result["status"] == "sent"
        assert any(message.content == "Original final" for message in alpha.notifications)
    assert all(message.action != "turn_end" for message in alpha.displayed)


@pytest.mark.asyncio
async def test_original_final_arriving_before_question_answer_is_buffered(peers):
    alpha, beta = await peers("alpha"), await peers("beta")
    first = asyncio.create_task(alpha.call("hub_ask", {"to": "beta", "question": "Original"}))
    original = await beta.next()
    back = asyncio.create_task(beta.call("hub_ask", {"to": "alpha", "question": "Question back"}))
    question = await first
    await beta.complete(original, "Fast final")
    continued = await alpha.call("hub_ask", {"to": "beta", "thread": question["thread"], "question": "Answer"})
    assert continued["status"] == "answered" and continued["text"] == "Fast final"
    assert (await back)["status"] == "answered"


@pytest.mark.asyncio
async def test_three_agent_cycle_returns_busy_and_keeps_original_waits(peers):
    alpha, beta, gamma = await peers("alpha"), await peers("beta"), await peers("gamma")
    ab = asyncio.create_task(alpha.call("hub_ask", {"to": "beta", "question": "A to B"}))
    original = await beta.next()
    bc = asyncio.create_task(beta.call("hub_ask", {"to": "gamma", "question": "B to C"}))
    delegated = await gamma.next()
    ca = await gamma.call("hub_ask", {"to": "alpha", "question": "C to A"})
    assert ca["status"] == "busy"
    assert not ab.done() and not bc.done()
    await gamma.complete(delegated, "C finished")
    assert (await bc)["text"] == "C finished"
    await beta.complete(original, "B finished")
    assert (await ab)["text"] == "B finished"


@pytest.mark.asyncio
async def test_late_reply_after_timeout_is_observed_and_frame_hidden(peers):
    alpha, beta = await peers("alpha"), await peers("beta")
    pending = asyncio.create_task(alpha.call("hub_ask", {"to": "beta", "question": "Slow answer", "timeout": 1}))
    request = await beta.next()
    assert (await pending)["status"] == "timeout"
    await beta.complete(request, "Late final")
    assert any(message.content == "Late final" for message in alpha.notifications)
    assert alpha.inputs.empty()
    assert all(message.action != "turn_end" for message in alpha.displayed)


@pytest.mark.asyncio
async def test_duplicate_names_in_distinct_workspaces_and_child_token_retirement(peers):
    first, second = await peers("claude", "first"), await peers("claude", "second")
    alpha = await peers("alpha", "first")
    status = await alpha.call("hub_status", {})
    assert next(row for row in status if row["identity"] == "claude")["agent_id"] == first.hub._identity.agent_id
    pending = asyncio.create_task(alpha.call("hub_ask", {"to": "claude", "question": "First folder"}))
    request = await first.next()
    assert second.inputs.empty()
    await first.complete(request, "First result")
    assert (await pending)["text"] == "First result"
    alpha.hub.rotate_acp_hub_binding()
    with pytest.raises(ValueError, match="stale|unavailable"):
        await alpha.call("hub_agents", {})


@pytest.mark.asyncio
async def test_self_ask_and_unowned_thread_fail_before_delivery(peers):
    alpha, beta = await peers("alpha"), await peers("beta")
    with pytest.raises(ValueError, match="itself"):
        await alpha.call("hub_ask", {"to": "alpha", "question": "Self"})
    with pytest.raises(ValueError, match="not owned"):
        await alpha.call("hub_msg", {"to": "beta", "message": "Unknown", "thread": "foreign"})
    assert beta.inputs.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, status, marker", [
    ("cancelled", "ended", "stopped"), ("refusal", "ended", "refused"),
    ("max_tokens", "ended", "cut off"), ("max_turn_requests", "ended", "cut off"),
    ("busy", "busy", "busy"), ("skipped", "ended", "skipped"),
    ("timeout", "ended", "timed out"), ("error", "ended", "could not complete"),
])
async def test_stop_reason_message_and_frame_release_mcp_wait(peers, outcome, status, marker):
    alpha, beta = await peers("alpha"), await peers("beta")
    pending = asyncio.create_task(alpha.call("hub_ask", {"to": "beta", "question": "Outcome"}))
    request = await beta.next()
    await beta.complete(request, "", outcome)
    result = await pending
    assert result["status"] == status and marker in result["text"]
    frame = next(message for message in alpha.received if message.action == "turn_end")
    assert frame.reply_to == request["request_id"] and frame.thread_id == request["thread_id"]
    assert frame.metadata["turn_end"]["outcome"] == outcome
    assert len([message for message in alpha.received if message.action == "message"]) == 1
    assert all(message.action != "turn_end" for message in alpha.displayed)
