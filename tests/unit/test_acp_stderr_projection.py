"""Adapter diagnostics retain their data without writing over active TUI input."""

import asyncio
import io
import logging
import sys
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor_tui.terminal_state import TerminalMode


def runner_with_stderr(tmp_path, data):
    runner = ACPRunner(ACPProfile("owned-diagnostic", (sys.executable,), cwd=str(tmp_path)))
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    runner._process = SimpleNamespace(stderr=reader)
    return runner


@pytest.mark.asyncio
async def test_stderr_callback_owns_output_while_last_line_stays_bounded(tmp_path, monkeypatch):
    output = io.BytesIO()
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(buffer=output))
    received = []
    lines = b"first diagnostic\n" + b"x" * 1200 + b"\n"
    runner = runner_with_stderr(tmp_path, lines)
    runner.stderr_callback = received.append

    await runner._drain_stderr()

    assert received == [b"first diagnostic\n", b"x" * 1200 + b"\n"]
    assert runner.last_stderr == "x" * 1000
    assert output.getvalue() == b""


@pytest.mark.asyncio
async def test_standalone_runner_preserves_original_stderr_bytes(tmp_path, monkeypatch):
    output = io.BytesIO()
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(buffer=output))
    runner = runner_with_stderr(tmp_path, b"owned \xff diagnostic\n")

    await runner._drain_stderr()

    assert output.getvalue() == b"[acp] owned \xff diagnostic\n"
    assert runner.last_stderr == "owned \ufffd diagnostic"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,pipe_mode", [(TerminalMode.RAW, False), (TerminalMode.NORMAL, False), (TerminalMode.RAW, True)])
async def test_application_routes_only_active_tui_diagnostics_to_file_log(tmp_path, monkeypatch, caplog, mode, pipe_mode):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path)
    output = io.BytesIO()
    # TTY alone is not an active TUI: detached and pipe paths still forward bytes.
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(buffer=output, isatty=lambda: True))
    renderer = SimpleNamespace(pipe_mode=pipe_mode, terminal_state=SimpleNamespace(current_mode=mode))
    services = {"llm_service": SimpleNamespace(message_display_service=SimpleNamespace(renderer=renderer))}
    bus = SimpleNamespace(get_service=services.get)
    orchestrator = ACPOrchestrator(bus)
    orchestrator._profile_name = "owned-diagnostic"
    caplog.set_level(logging.INFO, logger="kollabor.llm.acp_orchestrator")
    runner = runner_with_stderr(tmp_path, b"Controlled startup diagnostic\n")
    runner.stderr_callback = orchestrator._on_runner_stderr

    await runner._drain_stderr()

    assert runner.last_stderr == "Controlled startup diagnostic"
    assert "Controlled startup diagnostic" in caplog.text
    assert output.getvalue() == (b"" if mode == TerminalMode.RAW and not pipe_mode
                                 else b"[acp] Controlled startup diagnostic\n")


def test_gemini_missing_api_key_gets_supported_auth_guidance(tmp_path):
    import acp

    runner = ACPRunner(ACPProfile("gemini-cli", (sys.executable,), cwd=str(tmp_path)))
    err = runner._request_error(acp.RequestError(-32000, "Gemini API key is missing or not configured."))
    assert "GEMINI_API_KEY" in str(err) and "Vertex" in str(err)
    assert "sign in on this device" not in str(err)
