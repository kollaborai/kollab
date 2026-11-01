"""GET /sessions/{session_id}/state includes daemon runtime status DTOs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from kollabor.state.snapshots import ContextCompactionSnapshot, SessionStats


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
    from kollabor_engine.server import get_session_registry  # type: ignore[import-not-found]

    registry = get_session_registry()
    added: list[str] = []

    def _make(session_id: str = "sess_runtime", **state_methods):
        state_methods.setdefault(
            "get_active_profile", AsyncMock(return_value={"name": "profile"})
        )
        state_methods.setdefault("get_active_agent", AsyncMock(return_value=None))
        state_methods.setdefault("get_system_info", AsyncMock(return_value={}))
        state_methods.setdefault("get_hub_state", AsyncMock(return_value={}))
        state_methods.setdefault("get_processing_state", AsyncMock(return_value={}))
        state_methods.setdefault(
            "get_session_stats",
            AsyncMock(
                return_value=SessionStats(
                    input_tokens=0,
                    input_tokens_estimated=False,
                    cache_read_tokens=16,
                    total_cache_read_tokens=64,
                )
            ),
        )
        state_methods.setdefault(
            "get_context_compaction_snapshot",
            AsyncMock(
                return_value=ContextCompactionSnapshot(
                    available=True,
                    model_context_window_tokens=100_000,
                    automatic_compaction_threshold_tokens=75_000,
                    tokens_until_automatic_compaction=25_000,
                )
            ),
        )
        session = SimpleNamespace(state=SimpleNamespace(**state_methods))
        registry[session_id] = session
        added.append(session_id)
        return session

    yield _make
    for session_id in added:
        registry.pop(session_id, None)


@pytest.mark.asyncio
async def test_session_state_includes_session_stats_and_compaction_snapshot(
    client, make_session
):
    session = make_session()

    response = await client.get("/sessions/sess_runtime/state")

    assert response.status_code == 200
    body = response.json()
    assert body["session_stats"]["input_tokens"] == 0
    assert body["session_stats"]["input_tokens_estimated"] is False
    assert body["session_stats"]["cache_read_tokens"] == 16
    assert body["session_stats"]["total_cache_read_tokens"] == 64
    assert body["context_compaction"]["model_context_window_tokens"] == 100_000
    assert body["context_compaction"]["automatic_compaction_threshold_tokens"] == 75_000
    assert body["context_compaction"]["tokens_until_automatic_compaction"] == 25_000
    session.state.get_session_stats.assert_awaited_once_with()
    session.state.get_context_compaction_snapshot.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_session_state_returns_null_for_unavailable_compaction_rpc(
    client, make_session
):
    make_session(
        get_context_compaction_snapshot=AsyncMock(
            side_effect=RuntimeError("daemon is unavailable")
        )
    )

    response = await client.get("/sessions/sess_runtime/state")

    assert response.status_code == 200
    assert response.json()["context_compaction"] is None


@pytest.mark.asyncio
async def test_session_state_missing_session_is_404(client):
    response = await client.get("/sessions/missing/state")

    assert response.status_code == 404
