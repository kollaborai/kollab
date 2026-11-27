"""Focused terminal timeout lifecycle tests."""

import asyncio
from unittest.mock import Mock

import pytest

from plugins.agent_orchestrator.ring_buffer import RingBuffer
from plugins.terminal_plugin import TerminalSession, TmuxPlugin


@pytest.mark.asyncio
async def test_shutdown_cancels_and_clears_timeout_tasks():
    plugin = TmuxPlugin()
    task = asyncio.create_task(asyncio.sleep(60))
    plugin._timeout_tasks["session"] = task

    await plugin.shutdown()

    assert task.cancelled()
    assert not plugin._timeout_tasks


@pytest.mark.asyncio
async def test_killing_session_cancels_its_timeout_task():
    plugin = TmuxPlugin()
    process = Mock()
    process.poll.return_value = 0
    plugin.sessions["session"] = TerminalSession(
        name="session", command="true", proc=process
    )
    task = asyncio.create_task(asyncio.sleep(60))
    plugin._timeout_tasks["session"] = task

    result = await plugin.kill_background_session("session")

    assert result["success"]
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert "session" not in plugin.sessions
    assert "session" not in plugin._timeout_tasks


@pytest.mark.asyncio
async def test_timeout_task_failure_is_observed(caplog):
    plugin = TmuxPlugin()

    async def fail():
        raise RuntimeError("timer boom")

    task = asyncio.create_task(fail())
    plugin._timeout_tasks["session"] = task

    await asyncio.sleep(0)
    with caplog.at_level("ERROR", logger="plugins.terminal_plugin"):
        plugin._on_timeout_task_done("session", task)

    assert "session" not in plugin._timeout_tasks
    assert "Terminal timeout task failed for session 'session'" in caplog.text


@pytest.mark.asyncio
async def test_natural_exit_cancels_timer_and_preserves_output():
    plugin = TmuxPlugin()
    process = Mock()
    process.poll.return_value = 0
    ring_buffer = RingBuffer()
    ring_buffer.append("completed output")
    session = TerminalSession(
        name="session",
        command="true",
        proc=process,
        ring_buffer=ring_buffer,
    )
    plugin.sessions["session"] = session
    task = asyncio.create_task(asyncio.sleep(60))
    plugin._timeout_tasks["session"] = task

    plugin._handle_session_exit("session", session)
    await asyncio.gather(task, return_exceptions=True)
    captured = await plugin.capture_session_output("session")

    assert task.cancelled()
    assert captured["output"] == ["completed output"]
    assert "session" not in plugin._timeout_tasks


@pytest.mark.asyncio
async def test_new_session_duplicate_explains_recovery_tools():
    plugin = TmuxPlugin()
    process = Mock()
    process.poll.return_value = None
    plugin.sessions["terminal"] = TerminalSession(
        name="terminal", command="python", proc=process
    )

    result = await plugin._handle_new_session(["terminal"])

    assert not result.success
    assert "terminal_output" in result.message
    assert "terminal_send" in result.message


@pytest.mark.asyncio
async def test_background_session_duplicate_explains_recovery_tools():
    plugin = TmuxPlugin()
    process = Mock()
    process.poll.return_value = None
    plugin.sessions["terminal"] = TerminalSession(
        name="terminal", command="python", proc=process
    )

    result = await plugin.execute_background("python", name="terminal")

    assert not result["success"]
    assert "terminal_output" in result["message"]
    assert "terminal_send" in result["message"]


@pytest.mark.asyncio
async def test_background_shell_command_runs_and_captures_output():
    plugin = TmuxPlugin()
    result = await plugin.execute_background(
        "VALUE=$(printf background-shell-ok); printf '%s\\n' \"$VALUE\"",
        name="shell-command",
    )

    assert result["success"] is True
    session = plugin.sessions["shell-command"]
    await asyncio.wait_for(asyncio.to_thread(session.proc.wait), timeout=5)
    captured = await plugin.capture_session_output("shell-command")

    assert captured["output"] == ["background-shell-ok"]


def test_send_to_session_writes_one_line_to_live_session():
    plugin = TmuxPlugin()
    process = Mock()
    process.poll.return_value = None
    process.stdin = Mock()
    plugin.sessions["terminal"] = TerminalSession(
        name="terminal", command="python", proc=process
    )

    result = plugin.send_to_session("terminal", "print('ok')")

    assert result["success"]
    process.stdin.write.assert_called_once_with(b"print('ok')\n")
    process.stdin.flush.assert_called_once_with()


def test_send_to_session_reports_missing_and_exited_sessions():
    plugin = TmuxPlugin()

    missing = plugin.send_to_session("missing", "y")

    process = Mock()
    process.poll.return_value = 0
    plugin.sessions["finished"] = TerminalSession(
        name="finished", command="true", proc=process
    )
    exited = plugin.send_to_session("finished", "y")

    assert not missing["success"]
    assert "not found" in missing["message"]
    assert not exited["success"]
    assert "already exited" in exited["message"]


@pytest.mark.asyncio
async def test_capture_after_exit_waits_for_the_reader_thread(monkeypatch):
    """A capture right after a command exits still gets its output (#1465)."""
    import time

    original = TmuxPlugin._pump_output

    def slow_pump(*args, **kwargs):
        time.sleep(0.5)  # the reader thread falls behind the process
        return original(*args, **kwargs)

    monkeypatch.setattr(TmuxPlugin, "_pump_output", staticmethod(slow_pump))
    plugin = TmuxPlugin()
    result = await plugin.execute_background("printf 'done\\n'", name="quick")
    assert result["success"] is True
    session = plugin.sessions["quick"]
    await asyncio.wait_for(asyncio.to_thread(session.proc.wait), timeout=5)

    captured = await plugin.capture_session_output("quick")

    assert captured["output"] == ["done"]


@pytest.mark.asyncio
async def test_capture_waits_for_the_reader_without_blocking_the_loop():
    """A child that keeps stdout open after the shell exits holds the reader; the
    capture's wait for it must not stall the event loop (#1465 review)."""
    import time

    plugin = TmuxPlugin()
    result = await plugin.execute_background("sleep 1.5 & printf 'x\\n'", name="held")
    assert result["success"] is True
    session = plugin.sessions["held"]
    await asyncio.wait_for(asyncio.to_thread(session.proc.wait), timeout=5)

    gaps = []

    async def ticker():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    try:
        await asyncio.sleep(0.12)  # the ticker is running before the capture
        await plugin.capture_session_output("held")
        await asyncio.sleep(0.12)  # and records the gap a stall leaves
    finally:
        tick.cancel()

    assert max(gaps, default=0) < 0.5, gaps


@pytest.mark.asyncio
async def test_capture_after_exit_gets_output_a_held_reader_still_copies():
    """The wait is real: output a background child writes after the shell exits
    reaches the capture (#1465 review)."""
    plugin = TmuxPlugin()
    result = await plugin.execute_background(
        "(sleep 0.5; printf 'late\\n') & printf 'early\\n'", name="late"
    )
    assert result["success"] is True
    session = plugin.sessions["late"]
    await asyncio.wait_for(asyncio.to_thread(session.proc.wait), timeout=5)

    captured = await plugin.capture_session_output("late")

    assert captured["output"] == ["early", "late"]


@pytest.mark.asyncio
async def test_capture_reads_the_session_it_waited_for_even_if_the_name_is_reused():
    """A new session that takes the name during the wait does not answer for the
    old one (#1465 review)."""
    plugin = TmuxPlugin()
    result = await plugin.execute_background(
        "(sleep 0.5; printf 'old-late\\n') & printf 'old\\n'", name="reused"
    )
    assert result["success"] is True
    old = plugin.sessions["reused"]
    await asyncio.wait_for(asyncio.to_thread(old.proc.wait), timeout=5)

    capture = asyncio.create_task(plugin.capture_session_output("reused"))
    await asyncio.sleep(0.1)
    del plugin.sessions["reused"]
    await plugin.execute_background("printf 'replacement\\n'", name="reused")

    captured = await capture

    assert captured["output"] == ["old", "old-late"]
