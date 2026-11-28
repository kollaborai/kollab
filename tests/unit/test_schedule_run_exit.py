"""Schedule new-instance lifecycle tests."""

import asyncio
from types import SimpleNamespace

from kollabor.application import TerminalLLMChat


def test_exit_after_turn_settles_exits_when_initial_turn_already_completed(monkeypatch):
    app = object.__new__(TerminalLLMChat)
    app.running = True
    app.llm_service = SimpleNamespace(is_processing=False, turn_completed=True)

    async def unexpected_wait(_seconds):
        raise AssertionError("completed initial turn should exit without waiting for grace")

    monkeypatch.setattr("kollabor.application.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("kollabor.application.asyncio.sleep", unexpected_wait)
    asyncio.run(app._exit_after_turn_settles(turn_grace_seconds=10.0))

    assert app.running is False
