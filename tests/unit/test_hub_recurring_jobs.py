"""Recurring hub jobs (#728): opt-in jobs the coordinator sends on a schedule.

These drive the real plugin tick (`HubPlugin._run_hub_jobs`) with a real state
file; only the hub router is replaced, to record what would have been sent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.plugin import HubPlugin

JOBS = (
    "koordinator_refresh",
    "idle_sweep",
    "re_review",
    "kpi_rebuild",
    "hub_heartbeat",
)
HOUR = 3600.0


class Config:
    """Just enough of the config service: dot-path get over a dict."""

    def __init__(self, values):
        self.values = values

    def get(self, path, default=None):
        return self.values.get(path, default)


def make_plugin(tmp_path, monkeypatch, *, jobs=None, coordinator=True, route=None):
    monkeypatch.setattr("plugins.hub.presence.get_hub_dir", lambda: tmp_path)
    values = {}
    for name, fields in (jobs or {}).items():
        for key, value in fields.items():
            values[f"plugins.hub.hub_jobs.{name}.{key}"] = value
    plugin = HubPlugin(event_bus=MagicMock())
    plugin.config = Config(values)
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="koordinator",
        agent_id="koordinator-id",
        is_coordinator=coordinator,
    )
    plugin._route_message = route or AsyncMock(return_value=[])
    return plugin


def sent_to(plugin):
    return [call.args[0] for call in plugin._route_message.await_args_list]


def test_every_job_ships_off_by_default():
    jobs = HubPlugin.get_default_config()["plugins"]["hub"]["hub_jobs"]
    assert set(jobs) == set(JOBS)
    assert all(job["enabled"] is False for job in jobs.values())
    assert all(job["schedule"] and job["target"] for job in jobs.values())


@pytest.mark.asyncio
async def test_disabled_job_never_sends(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    await plugin._run_hub_jobs(now=0.0)
    await plugin._run_hub_jobs(now=10 * HOUR)
    assert sent_to(plugin) == []


@pytest.mark.asyncio
async def test_enabled_job_waits_one_schedule_then_sends_with_rules(
    tmp_path, monkeypatch
):
    plugin = make_plugin(
        tmp_path,
        monkeypatch,
        jobs={"hub_heartbeat": {"enabled": True, "schedule": "5m", "target": "lapis"}},
    )
    await plugin._run_hub_jobs(now=1000.0)  # first sight starts the clock
    await plugin._run_hub_jobs(now=1000.0 + 299)
    assert sent_to(plugin) == []

    await plugin._run_hub_jobs(now=1000.0 + 300)
    (msg,) = sent_to(plugin)
    assert msg.to == "lapis"
    assert msg.from_identity == "hub-cron"
    for word in ("merge", "approve", "close", "push", "restart"):
        assert word in msg.content.lower()
    assert "never" in msg.content.lower()

    await plugin._run_hub_jobs(now=1000.0 + 301)  # same interval: nothing new
    assert len(sent_to(plugin)) == 1


@pytest.mark.asyncio
async def test_rules_survive_a_custom_prompt(tmp_path, monkeypatch):
    plugin = make_plugin(
        tmp_path,
        monkeypatch,
        jobs={
            "idle_sweep": {
                "enabled": True,
                "schedule": "1m",
                "target": "lapis",
                "prompt": "wake the idle coders",
            }
        },
    )
    await plugin._run_hub_jobs(now=0.0)
    await plugin._run_hub_jobs(now=60.0)
    (msg,) = sent_to(plugin)
    assert "wake the idle coders" in msg.content
    assert "never merge, approve, close, push or restart" in msg.content.lower()


@pytest.mark.asyncio
async def test_last_run_survives_a_restart(tmp_path, monkeypatch):
    job = {"kpi_rebuild": {"enabled": True, "schedule": "10m", "target": "lapis"}}
    first = make_plugin(tmp_path, monkeypatch, jobs=job)
    await first._run_hub_jobs(now=0.0)
    await first._run_hub_jobs(now=600.0)
    assert len(sent_to(first)) == 1

    second = make_plugin(tmp_path, monkeypatch, jobs=job)  # fresh process
    await second._run_hub_jobs(now=650.0)
    assert sent_to(second) == []
    await second._run_hub_jobs(now=1200.0)
    assert len(sent_to(second)) == 1


@pytest.mark.asyncio
async def test_overlapping_runs_are_skipped(tmp_path, monkeypatch):
    gate = asyncio.Event()

    async def slow_route(msg):
        await gate.wait()
        return []

    plugin = make_plugin(
        tmp_path,
        monkeypatch,
        jobs={"re_review": {"enabled": True, "schedule": "1m", "target": "lapis"}},
        route=AsyncMock(side_effect=slow_route),
    )
    await plugin._run_hub_jobs(now=0.0)
    running = asyncio.create_task(plugin._run_hub_jobs(now=60.0))
    await asyncio.sleep(0)
    await plugin._run_hub_jobs(now=61.0)  # the first run is still sending
    gate.set()
    await running
    assert len(sent_to(plugin)) == 1


@pytest.mark.asyncio
async def test_message_cap_limits_sends_per_hour(tmp_path, monkeypatch):
    plugin = make_plugin(
        tmp_path,
        monkeypatch,
        jobs={
            "idle_sweep": {
                "enabled": True,
                "schedule": "1m",
                "target": ["a", "b", "c", "d", "e"],
                "max_messages_per_hour": 3,
            }
        },
    )
    await plugin._run_hub_jobs(now=0.0)
    await plugin._run_hub_jobs(now=60.0)
    assert [m.to for m in sent_to(plugin)] == ["a", "b", "c"]

    await plugin._run_hub_jobs(now=120.0)  # window still full
    assert len(sent_to(plugin)) == 3

    await plugin._run_hub_jobs(now=60.0 + HOUR + 1)  # old sends age out
    assert len(sent_to(plugin)) == 6


@pytest.mark.asyncio
async def test_only_the_coordinator_runs_jobs(tmp_path, monkeypatch):
    plugin = make_plugin(
        tmp_path,
        monkeypatch,
        coordinator=False,
        jobs={"hub_heartbeat": {"enabled": True, "schedule": "1m", "target": "x"}},
    )
    await plugin._run_hub_jobs(now=0.0)
    await plugin._run_hub_jobs(now=HOUR)
    assert sent_to(plugin) == []


@pytest.mark.asyncio
async def test_schedule_below_a_minute_is_refused(tmp_path, monkeypatch):
    plugin = make_plugin(
        tmp_path,
        monkeypatch,
        jobs={"hub_heartbeat": {"enabled": True, "schedule": "5s", "target": "x"}},
    )
    await plugin._run_hub_jobs(now=0.0)
    await plugin._run_hub_jobs(now=HOUR)
    assert sent_to(plugin) == []


@pytest.mark.asyncio
async def test_failed_send_is_not_counted_and_does_not_retry_early(
    tmp_path, monkeypatch
):
    plugin = make_plugin(
        tmp_path,
        monkeypatch,
        jobs={"hub_heartbeat": {"enabled": True, "schedule": "5m", "target": "x"}},
        route=AsyncMock(side_effect=RuntimeError("boom")),
    )
    await plugin._run_hub_jobs(now=0.0)
    await plugin._run_hub_jobs(now=300.0)  # raises inside; must not escape
    await plugin._run_hub_jobs(now=301.0)  # not retried before the next interval
    assert len(sent_to(plugin)) == 1
    await plugin._run_hub_jobs(now=600.0)
    assert len(sent_to(plugin)) == 2
