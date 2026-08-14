"""Focused behavior tests for ActivityMonitor completion transitions."""

from types import SimpleNamespace

import pytest

from plugins.agent_orchestrator.activity_monitor import ActivityMonitor


class FakeOrchestrator:
    def __init__(self, output="stable output"):
        self.output = output
        self.agent = SimpleNamespace(status="running", duration="0m01s", is_alive=True)

    def capture_output(self, name, lines):
        return self.output

    def get_agent(self, name):
        return self.agent


@pytest.mark.asyncio
async def test_completion_marks_live_agent_idle_before_callback():
    orchestrator = FakeOrchestrator()
    observed = []

    async def on_complete(name, duration, output):
        observed.append((name, duration, output, orchestrator.agent.status))

    monitor = ActivityMonitor(
        orchestrator,
        on_complete,
        idle_threshold=1,
    )
    monitor.track("worker")

    await monitor._check_agents()
    await monitor._check_agents()

    assert orchestrator.agent.status == "idle"
    assert observed == [("worker", "0m01s", "stable output", "idle")]
    assert not monitor.is_tracking("worker")


@pytest.mark.asyncio
async def test_completion_callback_still_runs_when_agent_record_is_gone():
    orchestrator = FakeOrchestrator()
    orchestrator.agent = None
    observed = []

    async def on_complete(name, duration, output):
        observed.append((name, duration, output))

    monitor = ActivityMonitor(orchestrator, on_complete, idle_threshold=1)
    monitor.track("worker")

    await monitor._check_agents()
    await monitor._check_agents()

    assert observed == [("worker", "?", "stable output")]
    assert not monitor.is_tracking("worker")
