"""A session start applies its approval mode without saving it (#1111).

EngineSession.initialize asks the daemon to switch the session's approval
mode. That call must be session-only (persist=False); the saved default in
~/.kollab/config.json changes only when a person changes it on purpose.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_engine import session as session_module
from kollabor_engine.session import EngineSession


class RecordingState:
    """Stands in for the daemon's StateService; records approval calls."""

    def __init__(self):
        self.approval_calls = []

    async def set_approval_mode(self, mode, persist=True):
        self.approval_calls.append((mode, persist))
        return SimpleNamespace(approval_mode=mode)


@pytest.mark.asyncio
async def test_session_start_applies_approval_mode_without_saving_it(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    state = RecordingState()
    daemon = SimpleNamespace(alive=True, state=state, startup_error="")
    pool = SimpleNamespace(spawn=AsyncMock(return_value=daemon), stop=AsyncMock())
    monkeypatch.setattr(session_module, "get_daemon_pool", lambda: pool)

    session = EngineSession(
        "s-1111", SimpleNamespace(name="proof", provider="test"), approval_mode="trust_all"
    )
    session.refresh_history = AsyncMock()
    try:
        await session.initialize()
    finally:
        if session._event_task is not None:
            session._event_task.cancel()

    assert state.approval_calls == [("trust_all", False)]
