"""Stall detection for idle agents holding an open task (#533)."""

import time

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.presence import STALL_AFTER_SECONDS, stall_status


def _record(state, changed, task="ship the release", **extra):
    data = {"state": state, "current_task": task, "state_changed_at": changed}
    data.update(extra)
    return data


def test_stall_fires_on_idle_agent_with_stale_open_task():
    now = time.time()
    assert stall_status(_record("idle", now - STALL_AFTER_SECONDS - 1), now=now) == (
        "stalled (idle 15m with open task)"
    )


def test_stall_rounds_minutes_and_scales_with_idle_time():
    now = time.time()
    marker = stall_status(_record("idle", now - 90 * 60), now=now)
    assert marker == "stalled (idle 90m with open task)"


def test_ready_state_counts_as_idle():
    now = time.time()
    assert stall_status(_record("ready", now - STALL_AFTER_SECONDS - 1), now=now)


def test_no_stall_below_threshold():
    now = time.time()
    assert stall_status(_record("idle", now - STALL_AFTER_SECONDS + 10), now=now) is None


def test_no_stall_while_working_or_dreaming():
    now = time.time()
    stale = now - STALL_AFTER_SECONDS * 3
    for state in ("working", "thinking", "dreaming", "booting"):
        assert stall_status(_record(state, stale), now=now) is None


def test_no_stall_while_waiting():
    # waiting is a deliberate park (peer reply, question, scheduled wait)
    now = time.time()
    stale = now - STALL_AFTER_SECONDS * 3
    assert stall_status(_record("waiting", stale), now=now) is None


def test_no_stall_without_open_task():
    now = time.time()
    stale = now - STALL_AFTER_SECONDS * 3
    for task in ("", "   ", None, 7):
        assert stall_status(_record("idle", stale, task=task), now=now) is None


def test_no_stall_without_state_changed_at():
    now = time.time()
    assert stall_status({"state": "idle", "current_task": "x"}, now=now) is None
    assert stall_status(_record("idle", 0), now=now) is None
    assert stall_status(_record("idle", -5), now=now) is None
    assert stall_status(_record("idle", None), now=now) is None
    assert stall_status(_record("idle", "recently"), now=now) is None


def test_custom_threshold_overrides_default():
    now = time.time()
    assert stall_status(_record("idle", now - 30), now=now, threshold=10)
    assert stall_status(_record("idle", now - 30), now=now, threshold=60) is None


def test_state_changed_at_survives_presence_round_trip():
    runtime = AgentRuntime(name="coder", identity="alpha", project="/workspace")
    runtime.state_changed_at = 1234.5
    record = runtime.to_presence_dict()
    assert record["state_changed_at"] == 1234.5
    import json as _json

    restored = AgentRuntime.from_presence_dict(_json.loads(_json.dumps(record)))
    assert restored.state_changed_at == 1234.5
