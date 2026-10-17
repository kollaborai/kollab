"""Tests for issue #207: silent except swallows replaced with logging.

Covers the notification-delivery warnings (grant/revoke/mcp-connect),
kill-path debug logs, status-indicator and RSS fallback debug logs, ACP
grant inventory debug log, the BackgroundTaskManager janitor loop, and
removal of unconsumed LLMService task forwards.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor_ai.notifications import EnvQueue


class _FakeBus:
    """Minimal event bus: just enough for push_env to find the queue."""

    def __init__(self, queue: Optional[EnvQueue]):
        self._services: Dict[str, Any] = {}
        if queue is not None:
            self._services["env_queue"] = queue

    def get_service(self, name: str) -> Any:
        return self._services.get(name)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# llm_coordinator: inject_tool_grant / inject_tool_revoke delivery warnings
# ---------------------------------------------------------------------------


def _build_llm_service(queue: Optional[EnvQueue]):
    """Build a minimal LLMService for inject_tool_grant/revoke tests."""
    from kollabor.llm.llm_coordinator import LLMService
    from kollabor_agent.tool_executor import ToolExecutor

    bus = _FakeBus(queue)
    service = LLMService.__new__(LLMService)
    service.event_bus = bus
    service.conversation_logger = None
    service.current_parent_uuid = None

    service.tool_executor = ToolExecutor(
        mcp_integration=None,
        event_bus=None,
        terminal_timeout=30,
        mcp_timeout=30,
    )
    service.tool_executor.set_bundle_scope([])
    service.inject_system_message = MagicMock(side_effect=_async_noop)
    service._native_tools = MagicMock()
    service._native_tools.load_tools = AsyncMock()
    return service


async def _async_noop(*args, **kwargs):
    return None


def test_grant_env_notification_undelivered_is_logged(caplog):
    """When the env rail is down the grant still works and the outage
    shows up as a warning."""
    service = _build_llm_service(queue=None)

    with caplog.at_level(logging.WARNING, logger="kollabor.llm.llm_coordinator"):
        _run(service.inject_tool_grant("file-read", reason="mcp connect"))

    warnings = [r for r in caplog.records if "NOT delivered" in r.getMessage()]
    assert warnings, "expected a NOT-delivered warning for grant notification"
    assert "file-read" in warnings[0].getMessage()


def test_revoke_env_notification_undelivered_is_logged(caplog):
    """When the env rail is down the revoke still works and the outage
    shows up as a warning."""
    service = _build_llm_service(queue=None)
    service.tool_executor.set_bundle_scope(["file-read"])

    with caplog.at_level(logging.WARNING, logger="kollabor.llm.llm_coordinator"):
        _run(service.inject_tool_revoke("file-read", reason="plugin shutdown"))

    warnings = [r for r in caplog.records if "NOT delivered" in r.getMessage()]
    assert warnings, "expected a NOT-delivered warning for revoke notification"
    assert "file-read" in warnings[0].getMessage()


def test_grant_env_notification_delivered_when_queue_present(caplog):
    """With the env rail healthy, no NOT-delivered warning is emitted."""
    queue = EnvQueue()
    service = _build_llm_service(queue)

    with caplog.at_level(logging.WARNING, logger="kollabor.llm.llm_coordinator"):
        _run(service.inject_tool_grant("file-read"))

    assert not [r for r in caplog.records if "NOT delivered" in r.getMessage()]
    assert queue.size() == 1


# ---------------------------------------------------------------------------
# mcp_integration: _push_env_mcp_connect delivery warning
# ---------------------------------------------------------------------------


def test_mcp_connect_env_notification_undelivered_is_logged(caplog):
    from kollabor_agent.mcp_integration import MCPIntegration

    integration = MCPIntegration.__new__(MCPIntegration)
    integration.event_bus = _FakeBus(None)

    with caplog.at_level(logging.WARNING, logger="kollabor_agent.mcp_integration"):
        integration._push_env_mcp_connect("testserver", 3)

    warnings = [r for r in caplog.records if "NOT delivered" in r.getMessage()]
    assert warnings, "expected a NOT-delivered warning for mcp-connect"
    assert "testserver" in warnings[0].getMessage()


def test_mcp_connect_env_notification_delivered_when_queue_present(caplog):
    from kollabor_agent.mcp_integration import MCPIntegration

    queue = EnvQueue()
    integration = MCPIntegration.__new__(MCPIntegration)
    integration.event_bus = _FakeBus(queue)

    with caplog.at_level(logging.WARNING, logger="kollabor_agent.mcp_integration"):
        integration._push_env_mcp_connect("testserver", 3)

    assert not [r for r in caplog.records if "NOT delivered" in r.getMessage()]
    assert queue.size() == 1


# ---------------------------------------------------------------------------
# shell_executor: kill-path debug logs
# ---------------------------------------------------------------------------


def test_kill_process_group_process_lookup_logged(caplog):
    from kollabor_agent.shell_executor import ShellExecutor

    executor = ShellExecutor()
    executor._current_process = MagicMock()
    executor._current_process.pid = 12345

    with patch(
        "kollabor_agent.shell_executor.os.getpgid",
        side_effect=ProcessLookupError("no such process"),
    ):
        with caplog.at_level(logging.DEBUG, logger="kollabor_agent.shell_executor"):
            _run(executor._kill_process_group())

    assert any(
        "Process kill skipped" in r.getMessage() for r in caplog.records
    ), "expected debug log for process-kill skip"


def test_kill_unix_process_group_process_lookup_logged(caplog):
    from kollabor_agent.shell_executor import ShellExecutor

    executor = ShellExecutor()
    fake_process = MagicMock()
    fake_process.pid = 12345
    executor._current_process = fake_process

    with patch(
        "kollabor_agent.shell_executor.os.getpgid",
        side_effect=ProcessLookupError("no such process"),
    ):
        with caplog.at_level(logging.DEBUG, logger="kollabor_agent.shell_executor"):
            _run(executor._kill_unix_process_group())

    assert any(
        "Process kill skipped" in r.getMessage() for r in caplog.records
    ), "expected debug log for process-kill skip"


# ---------------------------------------------------------------------------
# shell_command_service: status indicator fallback debug log
# ---------------------------------------------------------------------------


class _ExplodingRenderer:
    """Renderer whose status_area access raises to trigger the fallback."""

    @property
    def status_area(self):
        raise RuntimeError("renderer gone")


def _build_shell_service(bus: Any) -> Any:
    from kollabor_agent.shell_command_service import ShellCommandService

    service = ShellCommandService.__new__(ShellCommandService)
    service.event_bus = bus
    return service


def _bus_with_service(name: str, value: Any) -> Any:
    bus = MagicMock()
    bus.get_service = MagicMock(return_value=value)
    return bus


def test_show_executing_failure_is_logged(caplog):

    bus = MagicMock()
    bus.get_service = MagicMock(side_effect=_exploding_get_service)
    service = _build_shell_service(bus)

    with caplog.at_level(logging.DEBUG, logger="kollabor_agent.shell_command_service"):
        service._show_executing("echo hi")

    assert any(
        "Could not show shell exec status indicator" in r.getMessage()
        for r in caplog.records
    ), "expected debug log when status indicator fails"


def _exploding_get_service(name: str) -> Any:
    if name == "renderer":
        return _ExplodingRenderer()
    return None


# ---------------------------------------------------------------------------
# permissions/manager.py: ACP grant inventory malformed record debug log
# ---------------------------------------------------------------------------


def test_acp_grant_inventory_malformed_record_logged(caplog):
    from kollabor_agent.permissions.manager import PermissionManager

    manager = PermissionManager.__new__(PermissionManager)
    manager._config = {}
    manager._event_bus = MagicMock()
    manager._acp_session_grants = {"key-ok"}
    manager._acp_project_grants = set()
    manager._acp_session_grant_metadata = {
        "key-ok": {"operation": {"type": "file"}, "workspace": 12345, "profile": "p"}
    }
    manager._acp_project_grant_metadata = {}

    # Path(12345).expanduser() raises TypeError inside the guarded block.
    with caplog.at_level(logging.DEBUG, logger="kollabor_agent.permissions.manager"):
        records = manager.get_acp_grant_inventory()

    assert records, "record should still be emitted despite malformed metadata"
    assert records[0]["grant_key"] == "key-ok"
    assert records[0]["operation"] is None
    assert any(
        "Skipping malformed ACP grant record" in r.getMessage() for r in caplog.records
    ), "expected debug log for malformed ACP grant record"


# ---------------------------------------------------------------------------
# process_manager: macOS RSS fallback debug log
# ---------------------------------------------------------------------------


def test_macos_rss_fallback_failure_logged(caplog):
    from kollabor_agent.process_manager import SubprocessStrategy

    with patch("kollabor_agent.process_manager.Path.exists", return_value=False):
        with patch(
            "kollabor_agent.process_manager.subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "ps"),
        ):
            with caplog.at_level(
                logging.DEBUG, logger="kollabor_agent.process_manager"
            ):
                rss = SubprocessStrategy._get_rss(999999)

    assert rss == 0
    assert any(
        "macOS RSS lookup failed" in r.getMessage() for r in caplog.records
    ), "expected debug log for RSS fallback failure"


# ---------------------------------------------------------------------------
# background_task_manager: janitor loop cleanup behavior
# ---------------------------------------------------------------------------


def _build_task_manager(cleanup_interval: int = 1):
    from kollabor_agent.background_task_manager import BackgroundTaskManager
    from kollabor_config.llm_task_config import (
        BackgroundTasksConfig,
        LLMTaskConfig,
        QueueConfig,
    )

    config = LLMTaskConfig(
        background_tasks=BackgroundTasksConfig(
            max_concurrent=10,
            cleanup_interval=cleanup_interval,
            enable_monitoring=True,
        ),
        queue=QueueConfig(),
    )
    return BackgroundTaskManager(
        task_config=config, queue_metrics={}, enable_metrics=False
    )


def test_task_monitor_janitor_reaps_completed_tasks(caplog):
    """The monitor loop's contract: it reaps done tasks so the tracked
    set does not grow unbounded."""
    manager = _build_task_manager(cleanup_interval=1)

    async def quick():
        await asyncio.sleep(0)

    async def scenario():
        task = manager.create_background_task(quick(), name="reap-me")
        await task
        # The done-callback discards on completion; re-add to simulate a
        # task observed done-but-not-yet-reaped (the janitor's contract).
        manager._background_tasks.add(task)
        assert task in manager._background_tasks
        monitor = asyncio.create_task(manager._monitor_tasks())
        try:
            for _ in range(50):
                await asyncio.sleep(0.02)
                if task not in manager._background_tasks:
                    break
            assert (
                task not in manager._background_tasks
            ), "janitor loop should reap completed tasks"
        finally:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

    _run(scenario())


# ---------------------------------------------------------------------------
# llm_coordinator: unconsumed forwards removed
# ---------------------------------------------------------------------------


def test_llm_service_unconsumed_task_forwards_removed():
    from kollabor.llm.llm_coordinator import LLMService

    assert not hasattr(
        LLMService, "get_task_status"
    ), "get_task_status forward had no production caller and was removed"
    assert not hasattr(
        LLMService, "wait_for_tasks"
    ), "wait_for_tasks forward had no production caller and was removed"
    # cancel_all_tasks stays: consumed by shutdown()
    assert hasattr(LLMService, "cancel_all_tasks")
    assert hasattr(LLMService, "start_task_monitor")


# ---------------------------------------------------------------------------
# import syntax
# ---------------------------------------------------------------------------


def test_issue_207_files_parse():
    import ast
    from pathlib import Path

    files = [
        "kollabor/llm/llm_coordinator.py",
        "packages/kollabor-agent/src/kollabor_agent/shell_executor.py",
        "packages/kollabor-agent/src/kollabor_agent/shell_command_service.py",
        "packages/kollabor-agent/src/kollabor_agent/process_manager.py",
        "packages/kollabor-agent/src/kollabor_agent/mcp_integration.py",
        "packages/kollabor-agent/src/kollabor_agent/permissions/manager.py",
    ]
    for rel in files:
        src = Path(rel).read_text()
        ast.parse(src, filename=rel)
