"""Regression tests for detached agent lifecycle bookkeeping."""

import asyncio
from unittest.mock import Mock

from plugins.agent_orchestrator.activity_monitor import ActivityMonitor
from plugins.agent_orchestrator.models import AgentSession
from plugins.agent_orchestrator.orchestrator import AgentOrchestrator


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def _orchestrator(agent, *, delay=0.0):
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.agents = {agent.name: agent}
    orchestrator.kollab_init_delay = delay
    return orchestrator


def _run(coro):
    return asyncio.run(coro)


def test_failed_startup_is_not_marked_running():
    process = FakeProcess(returncode=23)
    agent = AgentSession(
        name="zircon",
        full_name="project-zircon",
        status="initializing",
        start_time=0.0,
        proc=process,
    )
    orchestrator = _orchestrator(agent)

    _run(orchestrator._wait_and_mark_ready("zircon", "project-zircon"))

    assert agent.status == "error"


def test_startup_task_cannot_mark_replacement_session_running():
    old_process = FakeProcess()
    replacement_process = FakeProcess()
    replacement = AgentSession(
        name="zircon",
        full_name="project-zircon",
        status="initializing",
        start_time=0.0,
        proc=replacement_process,
    )
    orchestrator = _orchestrator(replacement)

    _run(
        orchestrator._wait_and_mark_ready(
            "zircon", "project-zircon", expected_proc=old_process
        )
    )

    assert replacement.status == "initializing"


def test_refresh_removes_dead_initializing_session():
    process = FakeProcess(returncode=1)
    agent = AgentSession(
        name="sapphire",
        full_name="project-sapphire",
        status="initializing",
        start_time=0.0,
        proc=process,
    )
    orchestrator = _orchestrator(agent)

    orchestrator._refresh_agents()

    assert orchestrator.agents == {}


def test_cleanup_removes_dead_initializing_session():
    process = FakeProcess(returncode=1)
    agent = AgentSession(
        name="sapphire",
        full_name="project-sapphire",
        status="initializing",
        start_time=0.0,
        proc=process,
    )
    orchestrator = _orchestrator(agent)
    orchestrator._kill_session = Mock(return_value=True)

    assert orchestrator.cleanup_stale_sessions() == 1
    orchestrator._kill_session.assert_called_once_with("project-sapphire")
    assert orchestrator.agents == {}


def test_activity_monitor_untracks_dead_session_with_no_output():
    process = FakeProcess(returncode=1)
    agent = AgentSession(
        name="sapphire",
        full_name="project-sapphire",
        status="error",
        start_time=0.0,
        proc=process,
    )
    orchestrator = _orchestrator(agent)
    monitor = ActivityMonitor(orchestrator, on_agent_complete=Mock())
    monitor.track("sapphire")

    _run(monitor._check_agents())

    assert monitor.get_tracked_agents() == []
