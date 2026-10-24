"""The engine issues a voice capability; the daemon, a separate process, must honor it.

The daemon runs as a child process with its own capability registry and the real
`state.*` RPC handlers. The engine side is the real EngineSession and the engine's
registry in this process. Registries are process-local, so before the
`state.voice_admission` RPC every real voice turn was refused by the daemon.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kollabor_engine.session import EngineSession

from kollabor.state.remote import RemoteStateService
from kollabor_agent.voice_admission import (
    issue_voice_admission_context,
    revoke_voice_admissions_for_generation,
    revoke_voice_admissions_for_turn,
)

DAEMON = textwrap.dedent(
    """\
    import asyncio, json, sys
    from kollabor.state import handlers
    from kollabor.state.local import LocalStateService


    class Logger:
        async def log_assistant_message(self, content, parent_uuid, **kwargs):
            return "logged"


    class LLM:
        def __init__(self):
            self.conversation_history, self.current_parent_uuid = [], "root"
            self.conversation_logger = Logger()

        def _add_conversation_message(self, message, parent_uuid=None):
            self.conversation_history.append(message)
            return "added"

        async def submit_human_input(self, message, **kwargs):
            return {"status": "queued", "request_id": kwargs.get("request_id")}

        def create_background_task(self, coroutine, *, name=None):
            return asyncio.get_running_loop().create_task(coroutine, name=name)


    class Bus:
        def get_service(self, name):
            return None


    methods = {}


    class Rpc:
        def register(self, name, fn):
            methods[name] = fn


    llm = LLM()
    handlers.register_state_handlers(
        Rpc(), LocalStateService(llm, profile_manager=object(), event_bus=Bus())
    )


    async def history(params):
        return [[m.role, m.content, m.metadata] for m in llm.conversation_history]


    methods["test.history"] = history


    async def main():
        for line in sys.stdin:
            request = json.loads(line)
            result = await methods[request["method"]](request["params"])
            print(json.dumps(result), flush=True)


    asyncio.run(main())
    """
)

BINDING = {
    "owner_id": "engine",
    "voice_session_id": "voice-1",
    "control_session_id": "s1",
    "conversation_session_id": "s1",
    "agent_id": "lapis",
    "thread_id": None,
    "voice_provider_profile_id": "p1",
    "voice_prompt_revision": "r1",
    "voice_tool_policy": "voice_terminal_hub_v1",
    "generation": 1,
    "service_lease_epoch": 0,
}


class DaemonRpc:
    """JSON lines to the daemon child process, as the engine's RPC client would."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process

    async def call(self, method, params, timeout=None):
        def roundtrip():
            self.process.stdin.write(json.dumps({"method": method, "params": params}) + "\n")
            self.process.stdin.flush()
            return json.loads(self.process.stdout.readline())

        return await asyncio.to_thread(roundtrip)


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    process = subprocess.Popen(
        [sys.executable, "-c", DAEMON],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    rpc = DaemonRpc(process)
    session = EngineSession(
        session_id="s1", profile=SimpleNamespace(name="p1", model=None, provider="openai")
    )
    session.daemon = SimpleNamespace(
        alive=True, rpc=rpc, state=RemoteStateService(rpc), fan_out=Mock()
    )
    yield session
    process.kill()
    process.wait()


async def test_daemon_refuses_a_capability_it_was_never_given(daemon):
    context = issue_voice_admission_context(BINDING, "turn-a")
    refused = await daemon.state.send_message("hi", request_id="turn-a", origin_context=context)
    assert refused == {"accepted": False, "reason": "invalid voice admission capability"}


async def test_engine_session_admits_the_capability_in_the_daemon(daemon):
    context = issue_voice_admission_context(BINDING, "turn-b")
    result = await daemon.send_message(
        "hi", request_id="turn-b", thread_id=None, origin_context=context
    )
    assert result["accepted"] is True


async def test_engine_revokes_reach_the_daemon(daemon):
    turn = issue_voice_admission_context(BINDING, "turn-c")
    other = issue_voice_admission_context(BINDING, "turn-d")
    for context in (turn, other):
        assert (await daemon.voice_admission("admit", context=context))["admitted"] is True

    async def daemon_accepts(context):
        reply = await daemon.state.send_message(
            "hi", request_id=context["turn_id"], origin_context=context
        )
        return reply.get("accepted") is True

    assert await daemon_accepts(turn) and await daemon_accepts(other)
    await daemon.voice_admission(
        "revoke_turn", voice_session_id="voice-1", generation=1, turn_id="turn-c"
    )
    assert not await daemon_accepts(turn) and await daemon_accepts(other)
    await daemon.voice_admission("revoke_generation", voice_session_id="voice-1", generation=1)
    assert not await daemon_accepts(other)


async def test_a_failed_send_does_not_leave_the_capability_in_the_daemon(daemon):
    context = issue_voice_admission_context(BINDING, "turn-e")
    sent = []

    async def rejects(message, **kwargs):
        sent.append(kwargs)
        return {"accepted": False, "reason": "busy"}

    daemon.daemon.state = SimpleNamespace(send_message=rejects)
    result = await daemon.send_message("hi", request_id="turn-e", thread_id=None, origin_context=context)
    assert result["accepted"] is False and sent
    # the daemon no longer knows the token, even though the engine registry still does
    assert (await daemon.voice_admission("admit", context={"source": "livekit_voice"}))["admitted"] is False
    daemon.daemon.state = RemoteStateService(daemon.daemon.rpc)
    refused = await daemon.state.send_message("hi", request_id="turn-e", origin_context=context)
    assert refused["accepted"] is False


async def test_the_engine_will_not_mirror_a_capability_it_revoked(daemon):
    context = issue_voice_admission_context(BINDING, "turn-f")
    revoke_voice_admissions_for_turn("voice-1", 1, "turn-f")
    result = await daemon.send_message("hi", request_id="turn-f", thread_id=None, origin_context=context)
    assert result == {"accepted": False, "reason": "invalid voice admission capability"}
    revoke_voice_admissions_for_generation("voice-1", 1)


async def test_engine_session_keeps_the_played_text_in_the_daemon_history(daemon):
    refreshed = []

    async def refresh_history(notify=False):
        refreshed.append(notify)

    daemon.refresh_history = refresh_history  # pulling the mirror is the history tests' business
    assert await daemon.record_interrupted_reply("turn-i", "So the build") == {"recorded": True}
    assert await daemon.record_interrupted_reply("turn-i", "So the build") == {
        "recorded": False,
        "duplicate": True,
    }
    assert await daemon.record_interrupted_reply("turn-j", " ") == {"recorded": False}
    assert refreshed == [True]  # open windows hear about the one new row, and only it
    held = await daemon.daemon.rpc.call("test.history", {})
    assert held == [
        ["assistant", "So the build", {"interrupted": True, "source": "voice", "turn_id": "turn-i"}]
    ]
