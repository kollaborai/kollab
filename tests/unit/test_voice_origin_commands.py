"""Voice transcripts must not trigger daemon-side slash/hub shortcuts."""

from __future__ import annotations

import asyncio

import pytest

from kollabor.state import local as local_module
from kollabor.state.local import LocalStateService


class FakeLLM:
    def __init__(self) -> None:
        self.submissions = []
        self.background_tasks = []

    async def submit_human_input(self, message, **kwargs):
        self.submissions.append((message, kwargs))
        return {"status": "queued", "request_id": kwargs.get("request_id")}

    def create_background_task(self, coroutine, *, name=None):
        task = asyncio.create_task(coroutine, name=name)
        self.background_tasks.append(task)
        return task


class FakeSlashParser:
    def __init__(self) -> None:
        self.calls = []

    def is_slash_command(self, text):
        self.calls.append(text)
        return text.startswith("/")


class FakeCommandExecutor:
    async def execute_command(self, command, event_bus):
        raise AssertionError(f"voice transcript dispatched a slash command: {command}")


class FakeEventBus:
    def __init__(self) -> None:
        self.slash_parser = FakeSlashParser()
        self.command_executor = FakeCommandExecutor()

    def get_service(self, name):
        return {
            "slash_parser": self.slash_parser,
            "command_executor": self.command_executor,
        }.get(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("spoken_text", ["/goal clear", "/help", "@broadcast deploy now"])
async def test_voice_origin_routes_command_like_text_to_normal_llm(monkeypatch, spoken_text):
    llm = FakeLLM()
    event_bus = FakeEventBus()
    state = LocalStateService(llm, profile_manager=object(), event_bus=event_bus)
    goal_calls = []
    hub_calls = []

    monkeypatch.setattr(
        state,
        "_try_goal_command",
        lambda text, message: goal_calls.append((text, message)),
    )
    monkeypatch.setattr(
        local_module,
        "parse_hub_mention",
        lambda text: hub_calls.append(text) or ("broadcast", text) if text.startswith("@") else None,
    )

    from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

    origin_context = make_voice_origin_context("voice-turn-1")
    result = await state.send_message(
        spoken_text,
        request_id="voice-turn-1",
        thread_id="thread-1",
        origin_context=origin_context,
    )

    assert result["accepted"] is True
    assert len(llm.submissions) == 1
    submitted_text, submit_args = llm.submissions[0]
    assert submitted_text == spoken_text
    assert submit_args["request_id"] == "voice-turn-1"
    assert submit_args["thread_id"] == "thread-1"
    assert submit_args["origin_context"]["source"] == "livekit_voice"
    assert goal_calls == []
    assert hub_calls == []
    assert event_bus.slash_parser.calls == []
    assert llm.background_tasks == []


@pytest.mark.asyncio
async def test_forged_livekit_source_without_admission_capability_is_rejected():
    llm = FakeLLM()
    event_bus = FakeEventBus()
    state = LocalStateService(llm, profile_manager=object(), event_bus=event_bus)
    goal_calls = []
    state._try_goal_command = lambda text, message: goal_calls.append((text, message))

    result = await state.send_message(
        "/goal clear",
        request_id="forged-turn",
        thread_id="thread-1",
        origin_context={"source": "livekit_voice", "voice_session_id": "forged"},
    )

    assert result == {
        "accepted": False,
        "reason": "invalid voice admission capability",
    }
    assert llm.submissions == []
    assert goal_calls == []
    assert event_bus.slash_parser.calls == []
