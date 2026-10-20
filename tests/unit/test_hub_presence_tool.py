"""Presence carries the running tool, so every web UI sidebar gem can act it out."""

import json
from unittest.mock import MagicMock

from kollabor_agent.runtime import AgentRuntime
from kollabor_tui.display_tap import DisplayTap
from plugins.hub.models import AgentState
from plugins.hub.plugin import HubPlugin
from plugins.hub.presence import PresenceManager


def _hub(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: tmp_path)
    bus = MagicMock()
    bus.get_service.return_value = None  # no LLM service: the chain is settled
    hub = HubPlugin(event_bus=bus)
    hub._identity = AgentRuntime(name="coder", identity="lapis", state=AgentState.WORKING.value)
    hub._presence = PresenceManager(hub._identity)
    tap = DisplayTap(on_event=hub._follow_tool)

    def record():
        return json.loads((tmp_path / f"{hub._identity.agent_id}.json").read_text())

    return hub, tap, record


def test_the_running_tool_rides_in_presence_without_its_arguments(tmp_path, monkeypatch):
    hub, tap, record = _hub(tmp_path, monkeypatch)

    tap.publish({"type": "tool_start", "tool_id": "t1", "tool_name": "terminal: rm build", "tool_type": "terminal"})
    assert record()["current_tool"] == {"name": "terminal", "type": "terminal"}

    # The newest running tool wins; its result hands back to the one still running.
    tap.publish({"type": "tool_start", "tool_id": "t2", "tool_name": "web-search: plans", "tool_type": "web_search"})
    assert record()["current_tool"] == {"name": "web-search", "type": "web_search"}
    tap.publish({"type": "tool_result", "tool_id": "t2", "tool_name": "web-search: plans"})
    assert record()["current_tool"] == {"name": "terminal", "type": "terminal"}

    tap.publish({"type": "tool_result", "tool_id": "t1"})
    assert record()["current_tool"] == {}


def test_a_harness_call_goes_by_its_acp_kind_not_its_title(tmp_path, monkeypatch):
    hub, tap, record = _hub(tmp_path, monkeypatch)

    tap.publish({"type": "tool_start", "tool_id": "c1", "tool_name": "Read /home/dev/.env",
                 "tool_type": "read", "acp_passthrough": True})
    assert record()["current_tool"] == {"name": "read", "type": "read"}
    tap.publish({"type": "turn_complete"})
    assert record()["current_tool"] == {}


def test_only_tool_changes_write_presence(tmp_path, monkeypatch):
    hub, tap, _record = _hub(tmp_path, monkeypatch)
    writes = []
    hub._presence = MagicMock(publish=lambda: writes.append(dict(hub._identity.current_tool)))

    tap.publish({"type": "token", "text": "hi"})
    tap.publish({"type": "tool_result", "tool_id": "never-started"})
    assert writes == []
    tap.publish({"type": "tool_start", "tool_id": "t1", "tool_name": "file_read", "tool_type": "file_read"})
    tap.publish({"type": "thinking", "text": "..."})
    assert writes == [{"name": "file_read", "type": "file_read"}]


def test_going_idle_drops_a_tool_whose_result_never_came(tmp_path, monkeypatch):
    hub, tap, record = _hub(tmp_path, monkeypatch)
    tap.publish({"type": "tool_start", "tool_id": "t1", "tool_name": "terminal: sleep 60", "tool_type": "terminal"})

    hub._publish_idle_if_settled()

    assert record()["state"] == AgentState.IDLE.value
    assert record()["current_tool"] == {}
    assert hub._running_tools == {}


def test_the_tap_listener_is_not_a_subscriber_and_cannot_break_publish():
    seen = []
    tap = DisplayTap(on_event=seen.append)
    tap.publish({"type": "tool_start", "tool_id": "t1"})
    assert seen[0]["seq"] == 1 and tap.subscriber_count == 0

    def boom(_event):
        raise RuntimeError("listener failed")

    broken = DisplayTap(on_event=boom)
    broken.publish({"type": "output", "rendered": "x"})
    assert broken.get_snapshot()[0]["type"] == "output"


def test_presence_round_trip_keeps_the_tool():
    runtime = AgentRuntime(name="coder", identity="lapis", current_tool={"name": "hub_msg", "type": "hub_msg"})
    restored = AgentRuntime.from_presence_dict(json.loads(json.dumps(runtime.to_presence_dict())))
    assert restored.current_tool == {"name": "hub_msg", "type": "hub_msg"}
    assert AgentRuntime.from_presence_dict({"agent_name": "coder"}).current_tool == {}
