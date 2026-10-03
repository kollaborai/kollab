"""Panel routes: GET describe, POST action, error mapping, no-store."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from kollabor.panels import PanelError

SECRET = "sk-live-do-not-echo"


@pytest.fixture
def app():
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    return create_app()


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def make_session():
    """Register a fake session; every one is removed after the test."""
    from kollabor_engine.server import get_session_registry  # type: ignore[import-not-found]

    registry = get_session_registry()
    added = []

    def _make(sid="sess_panels", alive=True, **state):
        state.setdefault("get_panel", AsyncMock(return_value={"panel": "config"}))
        state.setdefault(
            "panel_action", AsyncMock(return_value={"ok": True, "message": "saved"})
        )
        state.setdefault(
            "get_active_profile",
            AsyncMock(return_value=SimpleNamespace(name="opus", model="opus-5")),
        )
        session = SimpleNamespace(
            session_id=sid, alive=alive, profile=None, state=SimpleNamespace(**state)
        )
        registry[sid] = session
        added.append(sid)
        return session

    yield _make
    for sid in added:
        registry.pop(sid, None)


@pytest.fixture
def mirror(monkeypatch):
    """Replace the profile mirror so no test reads a real profile file."""
    import kollabor_engine.routes.panels as panels_routes  # type: ignore[import-not-found]

    spy = MagicMock()
    monkeypatch.setattr(panels_routes, "apply_profile_mirror", spy)
    return spy


def _no_store(response):
    assert response.headers["cache-control"] == "no-store"


class TestDescribe:
    @pytest.mark.asyncio
    async def test_returns_panel_with_no_store_and_forwards_params(
        self, client, make_session
    ):
        session = make_session(
            get_panel=AsyncMock(return_value={"panel": "model", "kind": "picker"})
        )
        response = await client.get(
            "/sessions/sess_panels/panels/model", params={"provider": "openai"}
        )
        assert response.status_code == 200
        assert response.json() == {"panel": "model", "kind": "picker"}
        _no_store(response)
        session.state.get_panel.assert_awaited_once_with(
            "model", {"provider": "openai"}
        )

    @pytest.mark.asyncio
    async def test_too_many_or_too_long_params_are_400(self, client, make_session):
        make_session()
        many = {f"p{i}": "x" for i in range(17)}
        for params in (many, {"provider": "x" * 257}):
            response = await client.get(
                "/sessions/sess_panels/panels/model", params=params
            )
            assert response.status_code == 400
            assert response.json()["errors"] == {}
            _no_store(response)

    @pytest.mark.asyncio
    async def test_missing_session_is_404_and_stopped_daemon_is_409(
        self, client, make_session
    ):
        response = await client.get("/sessions/nope/panels/config")
        assert response.status_code == 404
        _no_store(response)

        make_session(sid="sess_down", alive=False)
        response = await client.get("/sessions/sess_down/panels/config")
        assert response.status_code == 409
        assert response.json()["detail"] == "Session daemon is not running"
        _no_store(response)


class TestAction:
    @pytest.mark.asyncio
    async def test_forwards_body_and_returns_result(self, client, make_session, mirror):
        session = make_session()
        body = {"changes": {"terminal.render_fps": 30}, "target": "global"}
        response = await client.post(
            "/sessions/sess_panels/panels/config/actions/save", json=body
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True, "message": "saved"}
        _no_store(response)
        session.state.panel_action.assert_awaited_once_with("config", "save", body)

    @pytest.mark.asyncio
    async def test_empty_body_is_an_empty_payload(self, client, make_session, mirror):
        session = make_session()
        response = await client.post("/sessions/sess_panels/panels/llm/actions/refresh")
        assert response.status_code == 200
        session.state.panel_action.assert_awaited_once_with("llm", "refresh", {})

    @pytest.mark.asyncio
    async def test_mirror_follows_the_daemons_active_profile(
        self, client, make_session, mirror
    ):
        session = make_session()
        await client.post("/sessions/sess_panels/panels/model/actions/select", json={})
        mirror.assert_called_once_with(session, "opus", "opus-5")

    @pytest.mark.asyncio
    async def test_mirror_failure_does_not_fail_the_action(
        self, client, make_session, mirror
    ):
        make_session(get_active_profile=AsyncMock(side_effect=RuntimeError("down")))
        response = await client.post(
            "/sessions/sess_panels/panels/model/actions/select", json={}
        )
        assert response.status_code == 200
        mirror.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", [b"{nope", b"[1, 2]", b'"text"'])
    async def test_body_must_be_a_json_object(self, client, make_session, raw):
        session = make_session()
        response = await client.post(
            "/sessions/sess_panels/panels/config/actions/save", content=raw
        )
        assert response.status_code == 400
        assert response.json()["message"] == "request body must be a JSON object"
        _no_store(response)
        session.state.panel_action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oversized_body_is_413(self, client, make_session):
        make_session()
        response = await client.post(
            "/sessions/sess_panels/panels/config/actions/save",
            content=b'{"changes": "' + b"x" * (256 * 1024) + b'"}',
        )
        assert response.status_code == 413
        _no_store(response)

    @pytest.mark.asyncio
    async def test_result_with_errors_is_400_without_errors_is_200(
        self, client, make_session, mirror
    ):
        rejected = {
            "ok": False,
            "message": "bad value",
            "errors": {"terminal.render_fps": "must be 1-60"},
            "panel": None,
            "open": None,
        }
        make_session(panel_action=AsyncMock(return_value=rejected))
        response = await client.post(
            "/sessions/sess_panels/panels/config/actions/save", json={}
        )
        assert response.status_code == 400
        assert response.json() == rejected
        _no_store(response)

        make_session(
            panel_action=AsyncMock(
                return_value={"ok": False, "message": "no route", "errors": {}}
            )
        )
        response = await client.post(
            "/sessions/sess_panels/panels/setup/actions/test", json={}
        )
        assert response.status_code == 200
        assert response.json()["ok"] is False


class TestErrors:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raised, expected",
        [
            (400, 400),
            (403, 403),
            (404, 404),
            (503, 503),
            (500, 502),  # the daemon's "panel unavailable"
            (409, 502),  # 409 is reserved for a stopped daemon
        ],
    )
    async def test_panel_error_status_passes_through(
        self, client, make_session, raised, expected
    ):
        error = PanelError("nope", {"name": "taken"}, status=raised)
        make_session(
            get_panel=AsyncMock(side_effect=error),
            panel_action=AsyncMock(side_effect=error),
        )
        for response in (
            await client.get("/sessions/sess_panels/panels/connect"),
            await client.post(
                "/sessions/sess_panels/panels/connect/actions/rename", json={}
            ),
        ):
            assert response.status_code == expected
            assert response.json() == {
                "ok": False,
                "message": "nope",
                "errors": {"name": "taken"},
                "detail": "nope",
            }
            _no_store(response)

    @pytest.mark.asyncio
    async def test_unreachable_daemon_is_502_and_never_echoes_the_error(
        self, client, make_session
    ):
        make_session(
            get_panel=AsyncMock(side_effect=TimeoutError(SECRET)),
            panel_action=AsyncMock(side_effect=ConnectionError(SECRET)),
        )
        for response in (
            await client.get("/sessions/sess_panels/panels/config"),
            await client.post(
                "/sessions/sess_panels/panels/config/actions/save",
                json={"changes": {"x": SECRET}},
            ),
        ):
            assert response.status_code == 502
            assert SECRET not in response.text
            assert response.json()["detail"].startswith("daemon unreachable")
            _no_store(response)


class TestApplyProfileMirror:
    def test_points_the_session_at_the_profile_with_the_live_model(self, monkeypatch):
        from kollabor_engine.routes.sessions import (  # type: ignore[import-not-found]
            apply_profile_mirror,
        )

        import kollabor_ai

        profile = SimpleNamespace(name="opus", model="stale")
        monkeypatch.setattr(
            kollabor_ai,
            "ProfileManager",
            lambda: SimpleNamespace(get_profile=lambda name: profile),
        )
        session = SimpleNamespace(session_id="s", profile=None)
        apply_profile_mirror(session, "opus", "opus-5")
        assert session.profile is profile
        assert profile.model == "opus-5"

        apply_profile_mirror(session, "opus")  # no model: keep the profile's own
        assert profile.model == "opus-5"

    def test_never_raises(self, monkeypatch):
        from kollabor_engine.routes.sessions import (  # type: ignore[import-not-found]
            apply_profile_mirror,
        )

        import kollabor_ai

        def boom():
            raise RuntimeError("no profile file")

        monkeypatch.setattr(kollabor_ai, "ProfileManager", boom)
        session = SimpleNamespace(session_id="s", profile="keep")
        apply_profile_mirror(session, "opus", "opus-5")
        assert session.profile == "keep"
