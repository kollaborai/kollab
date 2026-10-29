"""`kollab --hub status` shows the handoff health sections (issue #283).

Inside an agent, `/hub status` renders the supervisor section (open work,
stalled cards, "handoff awaiting ack") and the recent dispatch failures.
The CLI printed only who is online. These tests pin the wire: the CLI asks
the coordinator's socket (a `health_status` frame, local operator only) for
the same lines `_format_status` renders, and prints them unchanged.
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.messenger import AgentMessenger, AgentSocketServer
from plugins.hub.plugin import HubPlugin
from plugins.hub.supervisor import SupervisorState, begin_handoff
from plugins.hub.task_ledger import TaskLedger


def _coordinator(tmp_path):
    """A coordinator like the live one: supervisor loop on, ledger tracked."""
    plugin = object.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(
        identity="koordinator",
        agent_id="koordinator-001",
        is_coordinator=True,
        state="idle",
        pid=os.getpid(),
        project="proj",
    )
    plugin._task_ledger = TaskLedger(tasks_dir=str(tmp_path / "tasks"))
    plugin.config = {}
    plugin._supervisor_alerts = []
    plugin._supervisor_state = SupervisorState()
    plugin._supervisor_task = MagicMock()  # pretend the loop is running
    return plugin


@pytest.mark.asyncio
async def test_health_status_handler_returns_the_sections_the_tui_renders(tmp_path):
    plugin = _coordinator(tmp_path)
    card = plugin._task_ledger.enqueue(assigner="koordinator", directive="do it")
    begin_handoff(plugin._task_ledger, card.id, "koordinator", "ghost", "do it")
    plugin._record_dispatch_failure("ghost", "not online, no match")

    result = await plugin._handle_health_status_request()

    # The handed-off card is assigned, so not open — but it shows as
    # awaiting ack, which is the point of the section.
    assert result["supervisor"][0].startswith("supervisor: 0 open, 0 stalled")
    assert any(
        "handoff awaiting ack: " + card.id in line for line in result["supervisor"]
    )
    assert result["dispatch"][0] == "dispatch failures: 1 recent"
    assert any("ghost: not online" in line for line in result["dispatch"])

    # An agent without the sections shows nothing, not blank noise.
    plugin._supervisor_task = None
    plugin._dispatch_failures = []
    assert await plugin._handle_health_status_request() == {
        "supervisor": [],
        "dispatch": [],
    }


def test_lost_messages_publish_a_banner_queued_ones_do_not(tmp_path, monkeypatch):
    plugin = _coordinator(tmp_path)
    plugin._display_tap = MagicMock()
    clock = [1000.0]
    monkeypatch.setattr("plugins.hub.plugin.time.time", lambda: clock[0])

    plugin._record_dispatch_failure(
        "lapis", "offline, queued in its mailbox", queued=True
    )
    plugin._record_dispatch_failure("lapis", "not online, no match")
    plugin._record_dispatch_failure("lapis", "not online, no match")
    clock[0] += 61
    plugin._record_dispatch_failure("ruby", "not online, no match")
    plugin._record_dispatch_failure("lapis", "not online, no match")
    clock[0] += 300
    plugin._record_dispatch_failure("lapis", "not online, no match")

    messages = [
        c.args[0]["message"] for c in plugin._display_tap.publish.call_args_list
    ]
    assert messages == [
        "A message to lapis was not delivered: not online, no match",
        "A message to ruby was not delivered: not online, no match",
        "A message to lapis was not delivered: not online, no match",
    ]
    assert (
        len(plugin._dispatch_failure_lines()) >= 1
    )  # /hub status still lists them all


def test_a_failing_banner_never_breaks_the_send_path(tmp_path):
    plugin = _coordinator(tmp_path)
    plugin._display_tap = MagicMock()
    plugin._display_tap.publish.side_effect = RuntimeError("tap closed")

    plugin._record_dispatch_failure("lapis", "not online, no match")

    assert plugin._dispatch_failures[-1][1] == "lapis: not online, no match"


def test_health_status_frame_round_trips_over_a_real_socket():
    async def run():
        async def on_health():
            return {
                "supervisor": [
                    "supervisor: 0 open, 0 stalled, 0 alert(s)",
                    "  handoff awaiting ack: task0001 -> ghost",
                ],
                "dispatch": [
                    "dispatch failures: 1 recent",
                    "  (1m ago) ghost: not online, no match",
                ],
            }

        server = AgentSocketServer(
            "health-id",
            lambda _message: None,
            on_health_status=on_health,
            socket_name=f"health-status-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await AgentMessenger.request_health_status(sock_path)
        finally:
            await server.stop()

    assert asyncio.run(run()) == {
        "type": "health_status",
        "supervisor": [
            "supervisor: 0 open, 0 stalled, 0 alert(s)",
            "  handoff awaiting ack: task0001 -> ghost",
        ],
        "dispatch": [
            "dispatch failures: 1 recent",
            "  (1m ago) ghost: not online, no match",
        ],
    }


def test_health_status_with_no_handler_is_empty_not_an_error():
    async def run():
        server = AgentSocketServer(
            "health-none-id",
            lambda _message: None,
            socket_name=f"health-none-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await AgentMessenger.request_health_status(sock_path)
        finally:
            await server.stop()

    assert asyncio.run(run()) == {
        "type": "health_status",
        "supervisor": [],
        "dispatch": [],
    }


def test_hub_status_cli_prints_the_coordinator_sections(capsys):
    from kollabor.cli import _print_hub_health

    _print_hub_health(
        {
            "supervisor": [
                "supervisor: 0 open, 0 stalled, 0 alert(s)",
                "  handoff awaiting ack: task0001 -> ghost",
            ],
            "dispatch": ["dispatch failures: 1 recent"],
        }
    )
    out = capsys.readouterr().out
    assert "handoff awaiting ack: task0001 -> ghost" in out
    assert "dispatch failures: 1 recent" in out

    # No agent online, or nothing to report: no extra noise.
    _print_hub_health(None)
    _print_hub_health({"supervisor": [], "dispatch": []})
    assert capsys.readouterr().out == ""
