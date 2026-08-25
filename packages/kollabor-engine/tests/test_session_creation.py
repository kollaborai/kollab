"""Regression tests for session admission and initialization cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from kollabor_engine.routes import sessions
from starlette.requests import Request


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/sessions",
            "headers": [],
            "query_string": b"",
        }
    )


def _body(session_id: str | None = None) -> sessions.CreateSessionRequest:
    return sessions.CreateSessionRequest(
        profile="default",
        credentials=sessions.Credentials(
            provider="custom",
            api_key="test-key",
            model="test-model",
            base_url="http://127.0.0.1:9/v1",
        ),
        session_id=session_id,
    )


class _FakeSession:
    def __init__(self, session_id: str, initialize_error: BaseException | None = None):
        self.session_id = session_id
        self.initialize_error = initialize_error
        self.shutdown = AsyncMock()

    async def initialize(self) -> None:
        if self.initialize_error is not None:
            raise self.initialize_error

    def to_dict(self) -> dict[str, str]:
        return {"session_id": self.session_id}


@pytest.fixture
def registry(monkeypatch):
    value: dict[str, object] = {}
    monkeypatch.setattr(sessions, "get_session_registry", lambda: value)
    with sessions._session_reservation_lock:
        sessions._pending_session_ids.clear()
    yield value
    with sessions._session_reservation_lock:
        sessions._pending_session_ids.clear()


@pytest.mark.asyncio
async def test_registered_caller_session_id_conflicts_before_spawn(
    monkeypatch, registry
):
    existing = SimpleNamespace(session_id="sess-existing")
    registry["sess-existing"] = existing
    constructor = AsyncMock()
    monkeypatch.setattr(sessions, "EngineSession", constructor)

    with pytest.raises(HTTPException) as raised:
        await sessions.create_session(_body("sess-existing"), _request())

    assert raised.value.status_code == 409
    assert "sess-existing" in str(raised.value.detail)
    constructor.assert_not_called()
    assert registry["sess-existing"] is existing


@pytest.mark.asyncio
async def test_generated_session_id_collision_conflicts_before_spawn(
    monkeypatch, registry
):
    registry["generated-session"] = SimpleNamespace(session_id="generated-session")
    monkeypatch.setattr(sessions, "generate_session_name", lambda: "generated-session")
    constructor = AsyncMock()
    monkeypatch.setattr(sessions, "EngineSession", constructor)

    with pytest.raises(HTTPException) as raised:
        await sessions.create_session(_body(), _request())

    assert raised.value.status_code == 409
    constructor.assert_not_called()


@pytest.mark.asyncio
async def test_in_flight_session_id_reservation_conflicts_before_second_spawn(
    monkeypatch, registry
):
    started = asyncio.Event()
    release = asyncio.Event()
    created: list[_BlockingSession] = []

    class _BlockingSession(_FakeSession):
        async def initialize(self) -> None:
            started.set()
            await release.wait()

    def construct(**kwargs):
        session = _BlockingSession(kwargs["session_id"])
        created.append(session)
        return session

    monkeypatch.setattr(sessions, "EngineSession", construct)
    first = asyncio.create_task(
        sessions.create_session(_body("sess-in-flight"), _request())
    )
    await started.wait()

    with pytest.raises(HTTPException) as raised:
        await sessions.create_session(_body("sess-in-flight"), _request())

    assert raised.value.status_code == 409
    assert len(created) == 1
    release.set()
    await first
    assert "sess-in-flight" in registry


@pytest.mark.asyncio
async def test_cancelled_initialize_shuts_down_and_releases_reservation(
    monkeypatch, registry
):
    created: list[_FakeSession] = []

    def construct(**kwargs):
        session = _FakeSession(
            kwargs["session_id"], asyncio.CancelledError()
        )
        created.append(session)
        return session

    monkeypatch.setattr(sessions, "EngineSession", construct)

    with pytest.raises(asyncio.CancelledError):
        await sessions.create_session(_body("sess-cancelled"), _request())

    created[0].shutdown.assert_awaited_once_with()
    assert "sess-cancelled" not in registry
    with sessions._session_reservation_lock:
        assert "sess-cancelled" not in sessions._pending_session_ids


@pytest.mark.asyncio
async def test_oserror_initialize_shuts_down_and_returns_503(
    monkeypatch, registry
):
    created: list[_FakeSession] = []

    def construct(**kwargs):
        session = _FakeSession(
            kwargs["session_id"], OSError("unix socket unavailable")
        )
        created.append(session)
        return session

    monkeypatch.setattr(sessions, "EngineSession", construct)

    with pytest.raises(HTTPException) as raised:
        await sessions.create_session(_body("sess-oserror"), _request())

    assert raised.value.status_code == 503
    assert "unix socket unavailable" in str(raised.value.detail)
    created[0].shutdown.assert_awaited_once_with()
    assert "sess-oserror" not in registry
    with sessions._session_reservation_lock:
        assert "sess-oserror" not in sessions._pending_session_ids
