"""Regression tests for session admission and initialization cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from kollabor_engine.routes import sessions
from kollabor_engine.session import EngineSession
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


def test_session_request_defaults_to_trust_all():
    assert sessions.CreateSessionRequest().approval_mode == "trust_all"


def test_engine_session_keeps_opaque_id_but_serializes_friendly_name():
    session_id = "1c753a7d2def481084e3a64bcb09a7d2"
    session = EngineSession(
        session_id=session_id,
        profile=SimpleNamespace(name="default", model="test-model"),
    )
    session.conversation_id = "conversation-stable"

    first = session.to_dict()
    second = session.to_dict()

    assert first["session_id"] == session_id
    assert first["conversation_id"] == "conversation-stable"
    assert first["name"] != session_id
    assert first["name"] == second["name"]


class _FakeSession:
    def __init__(self, session_id: str, initialize_error: BaseException | None = None):
        self.session_id = session_id
        self.initialize_error = initialize_error
        self.shutdown = AsyncMock()

    async def initialize(self, on_spawned=None) -> None:
        if on_spawned is not None:
            await on_spawned(self)
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
        async def initialize(self, on_spawned=None) -> None:
            if on_spawned is not None:
                await on_spawned(self)
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


@pytest.mark.asyncio
async def test_webui_create_persists_immediately_after_spawn_and_after_initialize(
    monkeypatch, registry
):
    events = []
    constructed = {}

    class Store:
        def register_session(self, session):
            events.append("persisted")

    store = Store()
    request = _request()
    request.scope["app"] = SimpleNamespace(state=SimpleNamespace(handover_store=store))

    request.scope["headers"] = [(b"x-kollab-session-source", b"webui")]

    class PersistentSession(_FakeSession):
        def __init__(self, **kwargs):
            super().__init__(kwargs["session_id"])
            constructed.update(kwargs)
            self.webui_owned = kwargs["webui_owned"]
            self.daemon = SimpleNamespace(pid=1234)

        async def initialize(self, on_spawned=None):
            assert on_spawned is not None
            await on_spawned(self)
            events.append("initialized")

    monkeypatch.setattr(sessions, "EngineSession", PersistentSession)
    response = await sessions.create_session(_body("web-session-test"), request)

    assert constructed["webui_owned"] is True
    assert events == ["persisted", "initialized", "persisted"]
    assert response["session_id"] == "web-session-test"
    assert registry["web-session-test"].webui_owned is True


@pytest.mark.asyncio
async def test_unmarked_api_session_remains_nonpersistent_with_store_available(
    monkeypatch, registry
):
    events = []
    request = _request()
    request.scope["app"] = SimpleNamespace(
        state=SimpleNamespace(
            handover_store=SimpleNamespace(register_session=lambda session: events.append(session))
        )
    )

    class UnmarkedSession(_FakeSession):
        def __init__(self, **kwargs):
            super().__init__(kwargs["session_id"])
            self.webui_owned = kwargs["webui_owned"]

        async def initialize(self, on_spawned=None):
            assert on_spawned is None

    monkeypatch.setattr(sessions, "EngineSession", UnmarkedSession)
    await sessions.create_session(_body("api-session-test"), request)

    assert registry["api-session-test"].webui_owned is False
    assert events == []


@pytest.mark.asyncio
async def test_webui_delete_stops_pid_before_manifest_removal(monkeypatch, registry):
    events = []

    class Store:
        def remove_session(self, session_id):
            events.append(("remove", session_id))

    async def stop():
        events.append(("stop", "web-session-delete"))

    session = SimpleNamespace(webui_owned=True, shutdown=stop)
    registry["web-session-delete"] = session
    monkeypatch.setattr(sessions, "_handover_store", Store())

    response = await sessions.delete_session("web-session-delete")

    assert response == {"ok": True, "session_id": "web-session-delete"}
    assert events == [("stop", "web-session-delete"), ("remove", "web-session-delete")]
    assert "web-session-delete" not in registry


@pytest.mark.asyncio
async def test_failed_webui_delete_retains_manifest_and_reports_action(
    monkeypatch, registry
):
    from fastapi import HTTPException

    events = []

    class Store:
        def remove_session(self, session_id):
            events.append(("remove", session_id))

    async def fail_stop():
        events.append(("stop", "web-session-stuck"))
        raise RuntimeError("simulated PID stop failure")

    session = SimpleNamespace(webui_owned=True, shutdown=fail_stop)
    registry["web-session-stuck"] = session
    monkeypatch.setattr(sessions, "_handover_store", Store())

    with pytest.raises(HTTPException) as error:
        await sessions.delete_session("web-session-stuck")

    assert error.value.status_code == 503
    assert "/quit" in error.value.detail
    assert events == [("stop", "web-session-stuck")]
    assert registry["web-session-stuck"] is session


@pytest.mark.asyncio
async def test_webui_delete_manifest_failure_is_retryable_without_stopping_twice(
    monkeypatch, registry
):
    """A stopped agent stays visible until its owner record is durably removed."""
    events = []

    class Store:
        attempts = 0

        def remove_session(self, session_id):
            self.attempts += 1
            events.append(("remove", session_id))
            if self.attempts == 1:
                raise OSError("private manifest path")

    async def stop():
        events.append(("stop", "web-session-retry"))

    session = SimpleNamespace(
        session_id="web-session-retry",
        webui_owned=True,
        shutdown=stop,
        to_dict=lambda: {
            "session_id": "web-session-retry",
            "active": False,
            "deletion_pending": getattr(session, "deletion_pending", False),
            "deletion_warning": getattr(session, "deletion_warning", None),
        },
    )
    registry[session.session_id] = session
    monkeypatch.setattr(sessions, "_handover_store", Store())

    with pytest.raises(HTTPException) as error:
        await sessions.delete_session(session.session_id)

    assert error.value.status_code == 503
    assert "stopped" in error.value.detail.lower()
    assert "retry" in error.value.detail.lower()
    assert registry[session.session_id] is session
    assert session.deletion_pending is True
    assert session.deletion_warning
    assert events == [("stop", session.session_id), ("remove", session.session_id)]

    response = await sessions.delete_session(session.session_id)

    assert response == {"ok": True, "session_id": session.session_id}
    assert session.session_id not in registry
    assert events == [
        ("stop", session.session_id),
        ("remove", session.session_id),
        ("remove", session.session_id),
    ]
