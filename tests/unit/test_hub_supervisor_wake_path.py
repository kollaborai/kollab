"""Supervisor wake path through the hub plugin (issue #211).

The poll loop must deliver the wake through the same pipeline self-targeted
cron uses (_on_message_received) so an idle coordinator's turn actually
re-invokes with the open-work report. Completion and claim handlers kick
the supervisor so handoff does not wait for the next poll.
"""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_plugin(tmp_path, identity_name="koordinator", coordinator=True):
    plugin = object.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(
        identity=identity_name,
        agent_id=f"{identity_name}-001",
        is_coordinator=coordinator,
        project="proj",
    )
    plugin._presence = None  # roster comes from _roster cache
    plugin._task_ledger = TaskLedger(tasks_dir=str(tmp_path / "tasks"))

    import plugins.hub.coordinator as coordinator

    coordinator.get_hub_dir = lambda: tmp_path
    plugin._work_queue = coordinator.WorkQueue()
    plugin._roster = [
        {"identity": "koordinator", "state": "idle", "is_coordinator": True},
        {"identity": "lapis", "state": "idle", "is_coordinator": False},
    ]
    plugin.config = {
        "plugins.hub.supervisor_stall_threshold": 900,
        "plugins.hub.supervisor_wake_cooldown": 120,
        "plugins.hub.supervisor_max_reassignments": 3,
    }
    plugin._supervisor_alerts = []
    plugin._supervisor_state = __import__(
        "plugins.hub.supervisor", fromlist=["SupervisorState"]
    ).SupervisorState()
    plugin._supervisor_task = MagicMock()  # pretend loop is running
    plugin._supervisor_task.done.return_value = False
    plugin._on_message_received = AsyncMock()
    plugin._deliver_to_agent = AsyncMock()
    return plugin


def test_tick_delivers_wake_through_inbound_pipeline(tmp_path):
    """Acceptance: open ledger work wakes the coordinator's turn via the
    self-targeted message path — the same re-invocation cron uses."""
    plugin = _make_plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="peridot", directive="wake me up")
    outcomes = _run(plugin._supervisor_tick(900, 120, 3))
    assert outcomes == ["wake_coordinator -> koordinator"]
    plugin._on_message_received.assert_awaited_once()
    wake = plugin._on_message_received.await_args.args[0]
    assert wake.to == "koordinator"
    assert wake.from_identity == "idle-supervisor"
    assert "wake me up" in wake.content


def test_tick_silent_when_no_open_work(tmp_path):
    plugin = _make_plugin(tmp_path)
    assert _run(plugin._supervisor_tick(900, 120, 3)) == []
    plugin._on_message_received.assert_not_awaited()


def test_tick_cooldown_prevents_wake_spam(tmp_path):
    plugin = _make_plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="peridot", directive="work")
    assert _run(plugin._supervisor_tick(900, 120, 3))
    # Second immediate tick: cooldown suppresses the duplicate wake.
    assert _run(plugin._supervisor_tick(900, 120, 3)) == []
    assert plugin._on_message_received.await_count == 1


def test_nudge_routed_through_deliver_to_agent(tmp_path):
    plugin = _make_plugin(tmp_path)
    card = plugin._task_ledger.enqueue(assigner="peridot", directive="stalled")
    plugin._task_ledger.claim(card.id, "lapis")
    now = time.time()
    plugin._task_ledger._mutate_locked(
        card.id, lambda c: setattr(c, "last_activity_at", now - 1000) or True
    )
    roster_entry = {
        "identity": "lapis",
        "state": "working",
        "is_coordinator": False,
    }
    plugin._roster = [
        {"identity": "koordinator", "state": "idle", "is_coordinator": True},
        roster_entry,
    ]
    plugin._presence = SimpleNamespace(
        discover_agents_async=AsyncMock(
            return_value=[SimpleNamespace(identity="lapis", socket_path="")]
        )
    )
    outcomes = _run(plugin._supervisor_tick(900, 120, 3))
    assert outcomes == ["nudge_worker -> lapis"]
    plugin._deliver_to_agent.assert_awaited_once()
    nudge = plugin._deliver_to_agent.await_args.args[1]
    assert "task_checkpoint" in nudge.content
    assert card.id in nudge.content


def test_kick_runs_only_on_coordinator_with_live_loop(tmp_path):
    worker_plugin = _make_plugin(tmp_path, identity_name="lapis", coordinator=False)
    _run(worker_plugin._supervisor_kick())
    worker_plugin._on_message_received.assert_not_awaited()

    coord = _make_plugin(tmp_path)
    coord._supervisor_task = None  # loop not started (e.g. disabled config)
    _run(coord._supervisor_kick())
    coord._on_message_received.assert_not_awaited()


def test_claim_command_falls_back_to_open_ledger_cards(tmp_path):
    """hub_claim with an empty WorkQueue pulls from the durable open pool."""
    plugin = _make_plugin(tmp_path, identity_name="lapis", coordinator=False)
    plugin._task_ledger.enqueue(
        assigner="koordinator", directive="claimable work", project="proj"
    )
    result = _run(plugin._handle_claim_command(""))
    assert result.startswith("claimed: [")
    assert "claimable work" in result
    card = plugin._task_ledger.get_open()
    assert card == []
    active = plugin._task_ledger.get_active_for("lapis")
    assert len(active) == 1 and active[0].status == "active"


def test_work_format_lists_open_cards(tmp_path):
    plugin = _make_plugin(tmp_path, identity_name="koordinator")
    plugin._task_ledger.enqueue(assigner="peridot", directive="visible work")
    text = plugin._format_work()
    assert "open ledger cards: 1" in text
    assert "visible work" in text


# --- Wake efficacy and stall alerts (issue #283) -------------------------


def test_wakes_without_a_completed_turn_alert(tmp_path):
    """Acceptance (#283): repeated wakes that produce no completed turn
    raise a stalled-coordinator alert instead of looping in silence."""
    plugin = _make_plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="peridot", directive="work")
    state = plugin._supervisor_state
    state.last_turn_end = time.time() - 3600  # no turn for an hour
    for _ in range(3):
        _run(plugin._supervisor_tick(900, 0, 3))
    assert state.wakes_since_turn == 3
    assert plugin._supervisor_alerts, "no alert after three ineffective wakes"
    assert "not completed a turn" in plugin._supervisor_alerts[-1][1]
    # The wake still went out: it doubles as the recovery path.
    assert plugin._on_message_received.await_count == 3


def test_completed_turn_resets_wake_counter(tmp_path):
    plugin = _make_plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="peridot", directive="work")
    for _ in range(2):
        _run(plugin._supervisor_tick(900, 0, 3))
    plugin._stamp_supervisor_turn("turn_complete", {})
    assert plugin._supervisor_state.wakes_since_turn == 0
    assert plugin._supervisor_state.last_turn_end > 0
    _run(plugin._supervisor_tick(900, 0, 3))
    assert plugin._supervisor_alerts == []


def test_failed_turn_error_is_quoted_in_the_alert(tmp_path):
    plugin = _make_plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="peridot", directive="work")
    plugin._stamp_supervisor_turn(
        "error", {"error": "Your input exceeds the context window"}
    )
    state = plugin._supervisor_state
    assert state.last_error == "Your input exceeds the context window"
    # A failed turn does NOT reset the counter: it is not proof of life.
    state.last_turn_end = time.time() - 7200
    for _ in range(3):
        _run(plugin._supervisor_tick(900, 0, 3))
    assert any(
        "exceeds the context window" in text for _, text in plugin._supervisor_alerts
    )


def test_alert_respects_its_cooldown(tmp_path):
    plugin = _make_plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="peridot", directive="work")
    plugin._supervisor_state.last_turn_end = time.time() - 3600
    for _ in range(6):
        _run(plugin._supervisor_tick(900, 0, 3))
    # Six wakes, one alert: the 900s alert cooldown suppresses repeats.
    stalled_alerts = [
        t for _, t in plugin._supervisor_alerts if "not completed a turn" in t
    ]
    assert len(stalled_alerts) == 1


def test_undelivered_nudge_is_recorded_not_swallowed(tmp_path):
    """Acceptance (#283): a nudge nobody received surfaces as an alert."""
    plugin = _make_plugin(tmp_path)
    card = plugin._task_ledger.enqueue(assigner="peridot", directive="stalled")
    plugin._task_ledger.claim(card.id, "lapis")
    plugin._task_ledger._mutate_locked(
        card.id,
        lambda c: setattr(c, "last_activity_at", time.time() - 1000) or True,
    )
    # Delivery race: discovery listed lapis for the roster (so evaluate
    # chose a nudge), then found nobody on the delivery pass.
    plugin._presence = SimpleNamespace(
        discover_agents_async=AsyncMock(
            side_effect=[
                [
                    {
                        "identity": "koordinator",
                        "state": "idle",
                        "is_coordinator": True,
                    },
                    {
                        "identity": "lapis",
                        "state": "idle",
                        "is_coordinator": False,
                    },
                ],
                [],
            ]
        )
    )
    _run(plugin._supervisor_tick(900, 120, 3))
    assert any(
        "was not delivered to lapis" in text for _, text in plugin._supervisor_alerts
    )


def test_status_lists_supervisor_section_for_coordinator(tmp_path):
    """Acceptance (#283): /hub status shows the supervisor even while
    healthy — visibility on request, not only on alert."""
    plugin = _make_plugin(tmp_path)
    lines = plugin._supervisor_status_lines()
    assert lines and lines[0].startswith("supervisor: 0 open, 0 stalled")
    # Workers running no loop show nothing.
    worker = _make_plugin(tmp_path, identity_name="lapis", coordinator=False)
    worker._supervisor_task = None
    assert worker._supervisor_status_lines() == []
