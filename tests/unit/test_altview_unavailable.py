"""No terminal, no fullscreen view: push() raises and web commands answer in a line."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.state.local import LocalStateService
from kollabor_tui.altview.stack_manager import (
    AltViewStackManager,
    AltViewUnavailable,
    interactive_terminal_available,
    unavailable_attempts,
)


def _manager(renderer=None):
    bus = SimpleNamespace(
        emit_with_hooks=AsyncMock(), get_service=lambda *_a, **_k: None
    )
    return AltViewStackManager(bus, renderer), bus


def _push(mgr, name="config"):
    return asyncio.run(mgr.push(object(), name))


def test_push_raises_in_pipe_mode():
    mgr, bus = _manager(SimpleNamespace(pipe_mode=True))
    with pytest.raises(AltViewUnavailable):
        _push(mgr)
    assert mgr.stack_depth == 0
    bus.emit_with_hooks.assert_not_awaited()


@pytest.mark.parametrize("flag", ["--detached", "-d"])
def test_push_raises_in_detached_daemon(monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["kollab", flag, "--as", "web"])
    mgr, _ = _manager(SimpleNamespace(pipe_mode=False))
    with pytest.raises(AltViewUnavailable):
        _push(mgr)
    assert mgr.stack_depth == 0


def test_attach_client_and_plain_tui_stay_interactive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kollab", "--attach", "web"])
    assert interactive_terminal_available(SimpleNamespace(pipe_mode=False))
    # a mock renderer's pipe_mode is a truthy Mock, not True
    assert interactive_terminal_available(MagicMock())
    assert interactive_terminal_available(None)


def test_attempt_sink_records_the_view():
    mgr, _ = _manager(SimpleNamespace(pipe_mode=False))
    sink: list = []
    token = unavailable_attempts.set(sink)
    try:
        with pytest.raises(AltViewUnavailable):
            _push(mgr, "model-picker")
    finally:
        unavailable_attempts.reset(token)
    assert sink == ["model-picker"]


# -- the web slash path --------------------------------------------------------


def _run_web(text, name, handler):
    """Run one web slash command; return the saved (role, content) messages."""
    service = LocalStateService.__new__(LocalStateService)
    saved: list = []
    service._llm_service = SimpleNamespace(
        _add_conversation_message=lambda role, content, metadata=None: saved.append(
            (role, content)
        )
    )
    service._event_bus = None
    mgr, _ = _manager(SimpleNamespace(pipe_mode=False))
    aliases = {"ld": "llm", "m": "model", "wizard": "setup"}

    async def execute_command(command, event_bus):
        # what SlashCommandExecutor does: every handler error becomes a failure
        try:
            return await handler(mgr)
        except Exception as exc:
            return SimpleNamespace(
                success=False, message=f"Command failed: {exc}", ui_config=None
            )

    executor = SimpleNamespace(
        execute_command=execute_command,
        command_registry=SimpleNamespace(
            get_command=lambda n: SimpleNamespace(name=aliases.get(n, n))
        ),
    )
    parser = SimpleNamespace(parse_command=lambda _t: SimpleNamespace(name=name))
    sys.argv = ["kollab"]  # a plain process: only the web sink makes push() raise
    asyncio.run(service._execute_slash_command(text, parser, executor))
    assert mgr.stack_depth == 0
    return saved


async def _opens_view(mgr):
    await mgr.push(object(), "any-view")
    return SimpleNamespace(success=True, message="", ui_config=None)


@pytest.mark.parametrize(
    ("typed", "name", "reply"),
    [
        (
            "/config",
            "config",
            "/config opens in Settings → Configuration in the web UI",
        ),
        ("/ld", "ld", "/llm opens in Settings → Loadouts in the web UI"),
        ("/m", "m", "/model opens in Settings → Model in the web UI"),
        ("/wizard", "wizard", "/setup opens in Settings → Setup in the web UI"),
        (
            "/connect knocks",
            "connect",
            "/connect opens in Settings → Network in the web UI",
        ),
        ("/matrix", "matrix", "/matrix needs the terminal UI"),
    ],
)
def test_fullscreen_command_answers_in_one_line(typed, name, reply):
    saved = _run_web(typed, name, _opens_view)
    assert saved[-1] == ("assistant", reply)


def test_command_that_opens_no_view_is_unchanged():
    async def plain(_mgr):
        return SimpleNamespace(success=True, message="kollab 0.0.0", ui_config=None)

    assert _run_web("/version", "version", plain)[-1] == ("assistant", "kollab 0.0.0")
