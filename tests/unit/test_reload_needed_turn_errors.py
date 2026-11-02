"""A daemon whose code changed on disk fails turns with a reload-needed contract (#571).

A long-lived daemon lazy-imports modules on first use. When such a module has
left the checkout after the daemon started, every turn fails with
ModuleNotFoundError. The chain must surface that as an actionable reload
message instead of an opaque 500:

  daemon handler {"error": ..., "reload_needed": True}
    -> RemoteStateService raises ReloadNeededError
    -> EngineSession enriches it with the agent name
    -> message route answers 409 with "Restart it."
    -> voice route answers 409 the same way
    -> the voice worker includes the Engine's detail in TurnError
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kollabor_engine.routes.voice import router as voice_router
from kollabor_engine.session import EngineSession
from kollabor_engine.voice_types import VoiceTurnRequest
from kollabor_voice.turn_errors import rejected_detail

from kollabor.state.handlers import register_state_handlers
from kollabor.state.remote import ReloadNeededError, RemoteStateService

# === daemon side: the handler flags a vanished module ===


def _registered(state_service) -> dict:
    registered: dict = {}
    register_state_handlers(
        SimpleNamespace(register=registered.__setitem__), state_service
    )
    return registered


@pytest.mark.asyncio
async def test_daemon_flags_module_not_found_on_send_message() -> None:
    state_service = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=ModuleNotFoundError(
                "No module named 'kollabor.state.goal_replies'"
            )
        )
    )
    handler = _registered(state_service)["state.send_message"]

    result = await handler({"message": "hi"})

    assert result["reload_needed"] is True
    assert "goal_replies" in result["error"]


@pytest.mark.asyncio
async def test_daemon_plain_error_is_not_flagged() -> None:
    state_service = SimpleNamespace(
        send_message=AsyncMock(side_effect=ValueError("conversation is busy"))
    )
    handler = _registered(state_service)["state.send_message"]

    result = await handler({"message": "hi"})

    assert "reload_needed" not in result
    assert result["error"] == "conversation is busy"


# === engine client: the flag becomes a typed error ===


class _FakeRpc:
    def __init__(self, result: object) -> None:
        self._result = result

    async def call(self, *args: object, **kwargs: object) -> object:
        return self._result


@pytest.mark.asyncio
async def test_remote_raises_reload_needed_error() -> None:
    service = RemoteStateService(
        _FakeRpc(
            {
                "error": "No module named 'kollabor.state.goal_replies'",
                "reload_needed": True,
            }
        )
    )

    with pytest.raises(ReloadNeededError) as excinfo:
        await service.send_message("hi")

    assert "goal_replies" in str(excinfo.value)


@pytest.mark.asyncio
async def test_remote_plain_error_stays_value_error() -> None:
    service = RemoteStateService(_FakeRpc({"error": "conversation is busy"}))

    with pytest.raises(ValueError) as excinfo:
        await service.send_message("hi")

    assert type(excinfo.value) is ValueError
    assert "conversation is busy" in str(excinfo.value)


# === engine session: the typed error gains the agent name ===


def _bare_session(agent: str = "coder") -> EngineSession:
    session = object.__new__(EngineSession)
    session.agent = agent
    session.daemon = SimpleNamespace(agent_name=agent)
    session.session_id = "test-session"
    session.reload_needed = None
    session.voice_admission = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_session_names_the_agent_and_records_reload_needed() -> None:
    session = _bare_session("coder")
    session.daemon.state = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=ReloadNeededError(
                "No module named 'kollabor.state.goal_replies'"
            )
        )
    )

    with pytest.raises(ReloadNeededError) as excinfo:
        await session.send_message("hi", request_id="req-1")

    message = str(excinfo.value)
    assert message.startswith("coder could not run the turn:")
    assert "Its code changed on disk after it started. Restart it." in message
    assert session.reload_needed == "No module named 'kollabor.state.goal_replies'"


# === voice route: 409 with the actionable detail ===


@pytest.mark.asyncio
async def test_voice_route_answers_reload_needed_as_409() -> None:
    app = FastAPI()
    app.include_router(voice_router)

    async def open_turn(*args: object, **kwargs: object) -> object:
        raise ReloadNeededError(
            "coder could not run the turn: No module named 'gone'. "
            "Its code changed on disk after it started. Restart it."
        )

    app.state.voice = SimpleNamespace(open_turn=open_turn)

    body = VoiceTurnRequest(
        transcript_id="1c2d1bf8-93e2-4f6c-9f74-a4b83c00ba11",
        voice_session_id="2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27",
        turn_id="6a9f4d34-9c98-4a2e-86a5-f0eb8e02a7c3",
        generation=1,
        speaker="user",
        revision=1,
        final=True,
        display_text="hello",
        spoken_text=None,
        language=None,
        observed_at_utc_ms=0,
    ).model_dump()

    client = TestClient(app)
    response = client.post(
        "/voice/sessions/2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27/turns", json=body
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "reload_needed"
    assert "Its code changed on disk after it started. Restart it." in payload["detail"]


# === voice worker: the Engine's explanation reaches the speaker ===


def test_worker_detail_parses_engine_error_body() -> None:
    body = '{"detail": "coder could not run the turn: gone. Restart it."}'
    assert rejected_detail(body) == "coder could not run the turn: gone. Restart it."


def test_worker_detail_ignores_body_without_detail() -> None:
    assert rejected_detail('{"error": "x"}') == ""
    assert rejected_detail("not json at all") == "not json at all"


# === the session's voice-admission cleanup never masks the real error ===


@pytest.mark.asyncio
async def test_failed_voice_revoke_keeps_the_reload_needed_error() -> None:
    from kollabor_agent.voice_admission import issue_voice_admission_context

    session = _bare_session("coder")
    session.daemon.state = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=ReloadNeededError(
                "No module named 'kollabor.state.goal_replies'"
            )
        )
    )
    origin_context = issue_voice_admission_context(
        {
            "owner_id": "owner-1",
            "voice_session_id": "2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27",
            "control_session_id": "ctl-1",
            "conversation_session_id": "conv-1",
            "agent_id": "agent-1",
            "voice_provider_profile_id": "profile-1",
            "voice_prompt_revision": 1,
            "voice_tool_policy": "policy-1",
            "generation": 1,
            "service_lease_epoch": 1,
        },
        "6a9f4d34-9c98-4a2e-86a5-f0eb8e02a7c3",
    )
    session.voice_admission = AsyncMock(
        side_effect=[{"admitted": True}, ValueError("admission service unreachable")]
    )

    with pytest.raises(ReloadNeededError) as excinfo:
        await session.send_message(
            "hi", request_id="req-1", origin_context=origin_context
        )

    assert str(excinfo.value).startswith("coder could not run the turn:")
    assert "Restart it." in str(excinfo.value)
    assert session.reload_needed == "No module named 'kollabor.state.goal_replies'"
    assert session.voice_admission.await_count == 2

# === one request across the whole chain, only the transport faked ===
#
# A single POST /voice/sessions/<id>/turns crosses: the route handler ->
# open_turn (the session's real send path, admission included) ->
# EngineSession.send_message -> RemoteStateService -> the real registered
# daemon handler. Fakes: the _WireRpc transport and the simulated fault
# (a state service whose lazy import has left the checkout).


class _WireRpc:
    """Carries method calls to real registered handlers: the fake transport."""

    def __init__(self, handlers: dict) -> None:
        self._handlers = handlers

    async def call(self, method: str, params=None, **kwargs):
        return await self._handlers[method](params or {})


@pytest.mark.asyncio
async def test_one_request_crosses_the_whole_chain_to_409() -> None:
    from kollabor_agent.voice_admission import issue_voice_admission_context

    # daemon side: the real handlers over a service whose lazy import broke
    state_service = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=ModuleNotFoundError(
                "No module named 'kollabor.state.goal_replies'"
            )
        )
    )
    wire = _WireRpc(_registered(state_service))

    # engine side: a real session; its admission calls go over the same wire
    session = _bare_session("coder")
    session.daemon.state = RemoteStateService(wire)
    session.daemon.rpc = wire
    del session.voice_admission  # the real EngineSession.voice_admission runs

    origin_context = issue_voice_admission_context(
        {
            "owner_id": "owner-1",
            "voice_session_id": "2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27",
            "control_session_id": "ctl-1",
            "conversation_session_id": "conv-1",
            "agent_id": "agent-1",
            "voice_provider_profile_id": "profile-1",
            "voice_prompt_revision": 1,
            "voice_tool_policy": "policy-1",
            "generation": 1,
            "service_lease_epoch": 1,
        },
        "6a9f4d34-9c98-4a2e-86a5-f0eb8e02a7c3",
    )

    # open_turn: the actual send path the route's VoiceControl drives
    async def open_turn(voice_session_id, snapshot, *, kind="speech", talk_over=False):
        return await session.send_message(
            snapshot.display_text,
            request_id=f"voice-{snapshot.turn_id}",
            origin_context=origin_context,
        )

    app = FastAPI()
    app.include_router(voice_router)
    app.state.voice = SimpleNamespace(open_turn=open_turn)

    body = VoiceTurnRequest(
        transcript_id="1c2d1bf8-93e2-4f6c-9f74-a4b83c00ba11",
        voice_session_id="2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27",
        turn_id="6a9f4d34-9c98-4a2e-86a5-f0eb8e02a7c3",
        generation=1,
        speaker="user",
        revision=1,
        final=True,
        display_text="hello",
        spoken_text=None,
        language=None,
        observed_at_utc_ms=0,
    ).model_dump()

    response = TestClient(app).post(
        "/voice/sessions/2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27/turns", json=body
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "reload_needed"
    assert "goal_replies" in payload["detail"]
    assert "Its code changed on disk after it started. Restart it." in payload["detail"]
    assert session.reload_needed == "No module named 'kollabor.state.goal_replies'"
    # admit ran once over the wire; the failed turn's revoke ran once too
    assert state_service.send_message.await_count == 1


# === the route maps the enriched error to 409: open_turn stubbed on purpose,
# this test covers only the route's own error mapping ===


@pytest.mark.asyncio
async def test_voice_route_answers_reload_needed_as_409() -> None:
    app = FastAPI()
    app.include_router(voice_router)

    async def open_turn(*args: object, **kwargs: object) -> object:
        raise ReloadNeededError(
            "coder could not run the turn: No module named 'gone'. "
            "Its code changed on disk after it started. Restart it."
        )

    app.state.voice = SimpleNamespace(open_turn=open_turn)

    body = VoiceTurnRequest(
        transcript_id="1c2d1bf8-93e2-4f6c-9f74-a4b83c00ba11",
        voice_session_id="2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27",
        turn_id="6a9f4d34-9c98-4a2e-86a5-f0eb8e02a7c3",
        generation=1,
        speaker="user",
        revision=1,
        final=True,
        display_text="hello",
        spoken_text=None,
        language=None,
        observed_at_utc_ms=0,
    ).model_dump()

    response = TestClient(app).post(
        "/voice/sessions/2f7a1c3e-5b8d-4a90-b2e1-6c9d0e4f8a27/turns", json=body
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == "reload_needed"
    assert "Its code changed on disk after it started. Restart it." in payload["detail"]
