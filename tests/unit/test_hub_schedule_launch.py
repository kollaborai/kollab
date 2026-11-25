"""The schedule launcher must observe the instance it started, not a sibling."""

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_config import config_utils
from plugins.hub import plugin as hub_plugin
from plugins.hub import presence
from plugins.hub.schedules import ScheduleRow


def _record(pid: int, socket: str, started_at: float) -> dict:
    return {
        "identity": "coder",
        "pid": pid,
        "socket_path": socket,
        "started_at": started_at,
        "state": "working",
    }


async def _fire_with_presence(tmp_path, monkeypatch, presence_records):
    monkeypatch.setattr(config_utils, "get_config_directory", lambda: tmp_path)
    monkeypatch.setattr(presence, "get_hub_dir", lambda: "hub-A")
    monkeypatch.setattr(presence, "live_agents_on_machine", presence_records)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=42),
    )

    ticks = iter(range(1000))
    monkeypatch.setattr(
        hub_plugin,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None),
    )
    captured = {}

    class CaptureTimer:
        def __init__(self, store, runs, fire, **kwargs):
            captured["fire"] = fire

        async def run(self):
            return None

    monkeypatch.setattr(hub_plugin, "ScheduleTimer", CaptureTimer)
    hub = object.__new__(hub_plugin.HubPlugin)
    hub._relay_device_name = lambda: "device-A"
    hub._start_schedule_timer()
    await asyncio.sleep(0)
    row = ScheduleRow("s1", ["coder"], "run", 60, 1, when_busy="new_instance")
    result = await captured["fire"](row, "coder", 1, 2)
    return result


@pytest.mark.asyncio
async def test_existing_busy_socket_does_not_confirm_new_schedule_instance(
    tmp_path, monkeypatch
):
    old = _record(5, "/existing/coder.sock", 1)
    ok, error = await _fire_with_presence(
        tmp_path, monkeypatch, lambda: [("hub-A", old)]
    )

    assert not ok
    assert "did not come online" in error


@pytest.mark.asyncio
async def test_schedule_instance_succeeds_when_its_own_pid_appears(
    tmp_path, monkeypatch
):
    old = _record(5, "/existing/coder.sock", 1)
    launched = _record(42, "/new/coder.sock", 2)
    calls = 0

    def records():
        nonlocal calls
        calls += 1
        record = old if calls <= 3 else launched
        return [("hub-A", record)]

    ok, error = await _fire_with_presence(tmp_path, monkeypatch, records)

    assert ok, error
