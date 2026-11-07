"""The read-only cron_jobs socket view (#612).

The Web UI has no way to see scheduled hub cron jobs: /hub cron list is
terminal-only and lives inside each agent. The agent's unix socket now
answers a local-operator-only `cron_jobs` action with the same data
`_cron_list` renders, and the engine aggregates it across live agents
(GET /hub/cron-jobs). These tests pin the payload shape and the wire.

Task-ledger reminders are not cron jobs and never appear here.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.messenger import AgentSocketServer
from plugins.hub.plugin import HubCronJob, HubPlugin


def _plugin_with_jobs(monkeypatch, clock=1000.0):
    """A HubPlugin skeleton holding two cron jobs, time frozen at `clock`."""
    plugin = object.__new__(HubPlugin)
    plugin._hub_cron_jobs = [
        HubCronJob(
            id="ab12cd34",
            target="lapis",
            message="report the pulse",
            interval_seconds=3600,
            next_fire=clock + 1800,
            recurring=True,
            created_at=clock - 3600,
            last_error="",
        ),
        HubCronJob(
            id="9876fedc",
            target="infra@home-server",
            message="check the tunnel",
            interval_seconds=90,
            next_fire=clock - 10,  # overdue: next_in_seconds clamps to 0
            recurring=False,
            created_at=clock - 200,
            last_error="peer has no conversation grant for this agent",
        ),
    ]
    monkeypatch.setattr("plugins.hub.plugin.time.time", lambda: clock)
    return plugin


def test_cron_jobs_payload_serializes_every_field(monkeypatch):
    plugin = _plugin_with_jobs(monkeypatch)

    jobs = plugin._cron_jobs_payload()

    assert jobs[0] == {
        "id": "ab12cd34",
        "target": "lapis",
        "message": "report the pulse",
        "interval_seconds": 3600,
        "interval": "1h",
        "next_fire": 2800.0,
        "next_in_seconds": 1800,
        "recurring": True,
        "mode": "recurring",
        "created_at": -2600.0,
        "last_error": "",
    }
    # Overdue one-shot: countdown clamped, error surfaced, mode named.
    assert jobs[1]["next_in_seconds"] == 0
    assert jobs[1]["mode"] == "one-shot"
    assert jobs[1]["last_error"].startswith("peer has no conversation grant")
    # JSON-clean: the frame goes straight through the socket encoder.
    json.dumps(jobs)


def test_empty_schedule_is_an_empty_list(monkeypatch):
    plugin = object.__new__(HubPlugin)
    plugin._hub_cron_jobs = []
    assert plugin._cron_jobs_payload() == []


async def _ask(sock_path: str, action: str, timeout: float = 3.0):
    """One action over the socket, one JSON line back (#612 wire)."""
    reader, writer = await asyncio.open_unix_connection(sock_path)
    try:
        writer.write((json.dumps({"action": action}) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return json.loads(line.decode().strip())
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
        except Exception:
            pass


@pytest.mark.skipif(os.name == "nt", reason="unix socket")
def test_cron_jobs_frame_round_trips_over_a_real_socket(monkeypatch):
    async def run():
        plugin = _plugin_with_jobs(monkeypatch)
        server = AgentSocketServer(
            "cron-view-id",
            lambda _message: None,
            on_cron_jobs=plugin._cron_jobs_payload,
            socket_name=f"cron-view-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await _ask(sock_path, "cron_jobs")
        finally:
            await server.stop()

    frame = asyncio.run(run())
    assert frame["type"] == "cron_jobs"
    assert frame["error"] == ""
    assert [job["id"] for job in frame["jobs"]] == ["ab12cd34", "9876fedc"]
    assert frame["jobs"][0]["target"] == "lapis"


@pytest.mark.skipif(os.name == "nt", reason="unix socket")
def test_cron_jobs_without_a_handler_is_empty_not_an_error():
    async def run():
        server = AgentSocketServer(
            "cron-none-id",
            lambda _message: None,
            socket_name=f"cron-none-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await _ask(sock_path, "cron_jobs")
        finally:
            await server.stop()

    assert asyncio.run(run()) == {
        "type": "cron_jobs",
        "jobs": [],
        "error": "",
    }


@pytest.mark.skipif(os.name == "nt", reason="unix socket")
def test_a_failing_callback_reports_the_error_and_keeps_the_frame():
    async def run():
        async def broken():
            raise RuntimeError("ledger exploded")

        server = AgentSocketServer(
            "cron-broken-id",
            lambda _message: None,
            on_cron_jobs=broken,
            socket_name=f"cron-broken-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await _ask(sock_path, "cron_jobs")
        finally:
            await server.stop()

    frame = asyncio.run(run())
    assert frame["type"] == "cron_jobs"
    assert frame["jobs"] == []
    assert "ledger exploded" in frame["error"]


@pytest.mark.skipif(os.name == "nt", reason="unix socket")
def test_get_status_still_round_trips_after_the_cron_view_landed():
    """Regression guard: the new dispatcher branch did not break get_status."""

    async def run():
        server = AgentSocketServer(
            "cron-status-id",
            lambda _message: None,
            socket_name=f"cron-status-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await _ask(sock_path, "get_status")
        finally:
            await server.stop()

    status = asyncio.run(run())
    assert status["type"] == "status"
    assert status["pid"] == os.getpid()
