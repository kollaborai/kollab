"""Regression coverage for linked work slots and durable task cards."""

from types import SimpleNamespace

import pytest

from plugins.hub import coordinator
from plugins.hub.coordinator import WorkQueue
from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger


@pytest.fixture(autouse=True)
def _clear_terminal_cache():
    coordinator._TERMINAL_CACHE.clear()
    yield
    coordinator._TERMINAL_CACHE.clear()


def _hub_dir(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: hub_dir)
    return hub_dir


def _plugin(tmp_path, monkeypatch):
    _hub_dir(tmp_path, monkeypatch)
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._work_queue = WorkQueue()
    plugin._task_ledger = TaskLedger(tasks_dir=str(tmp_path / "tasks"))
    plugin._identity = SimpleNamespace(identity="lapis", project="test-project")
    return plugin


def test_task_card_link_is_durable_idempotent_and_conflict_safe(tmp_path, monkeypatch):
    _hub_dir(tmp_path, monkeypatch)
    queue = WorkQueue()
    slot = queue.add("work tied to #640", upstream_refs=["#640"])

    linked = queue.link_task_card(slot.id, "card-0001")

    assert linked.task_card_id == "card-0001"
    assert WorkQueue().get_all()[0].task_card_id == "card-0001"
    assert queue.link_task_card(slot.id, "card-0001").task_card_id == "card-0001"
    assert queue.link_task_card(slot.id, "card-0002") is None


def test_sweep_returns_linked_retirement_for_retryable_cleanup(tmp_path, monkeypatch):
    _hub_dir(tmp_path, monkeypatch)
    queue = WorkQueue()
    slot = queue.add("work tied to #640", upstream_refs=["#640"])
    queue.link_task_card(slot.id, "card-0001")

    retired = queue.sweep_stale(lambda ref: f"{ref} merged")
    retry = queue.sweep_stale(
        lambda ref: pytest.fail("retired refs must not be checked")
    )

    assert [(s.id, s.retired_reason, s.task_card_id) for s in retired] == [
        (slot.id, "#640 merged", "card-0001")
    ]
    assert [(s.id, s.retired_reason, s.task_card_id) for s in retry] == [
        (slot.id, "#640 merged", "card-0001")
    ]


def test_queue_command_links_open_card_to_slot(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)

    response = plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])

    cards = plugin._task_ledger.get_open(project="test-project")
    slot = plugin._work_queue.get_all()[0]
    assert len(cards) == 1
    assert slot.task_card_id == cards[0].id
    assert f"[{cards[0].id}]" in response
    assert any(f"queued as slot [{slot.id}]" in c["note"] for c in cards[0].checkpoints)


def test_stale_sweep_terminalizes_paired_card_and_keeps_retry_state(
    tmp_path, monkeypatch
):
    plugin = _plugin(tmp_path, monkeypatch)
    plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])
    card = plugin._task_ledger.get_open(project="test-project")[0]
    monkeypatch.setattr(
        coordinator,
        "github_terminal_state",
        lambda ref, project=None: f"{ref} merged",
    )
    terminalize_calls = []
    original_terminalize = plugin._task_ledger.terminalize

    def count_terminalize(*args, **kwargs):
        terminalize_calls.append(args[0])
        return original_terminalize(*args, **kwargs)

    monkeypatch.setattr(plugin._task_ledger, "terminalize", count_terminalize)

    retired = plugin._sweep_stale_work_queue()

    saved = plugin._task_ledger.get(card.id)
    assert [slot.task_card_id for slot in retired] == [card.id]
    assert saved.status == "cancelled"
    assert saved.terminal_actor == "work-queue-sweep"
    assert "#640 merged" in saved.terminal_reason
    assert plugin._task_ledger.get_open(project="test-project") == []

    plugin._sweep_stale_work_queue()

    assert terminalize_calls == [card.id]


def test_queue_command_cancels_card_if_slot_retires_before_link(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)
    original_add = WorkQueue.add

    def add_then_retire(queue, *args, **kwargs):
        slot = original_add(queue, *args, **kwargs)
        queue.sweep_stale(lambda ref: f"{ref} merged")
        return slot

    monkeypatch.setattr(WorkQueue, "add", add_then_retire)

    plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])

    slots = plugin._work_queue.get_all()
    card = plugin._task_ledger.get_all()[0]
    assert slots[0].task_card_id == card.id
    assert slots[0].status == "retired"
    assert card.status == "cancelled"
    assert plugin._task_ledger.get_open(project="test-project") == []


def test_stale_sweep_retries_card_terminalization_after_failure(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)
    plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])
    card = plugin._task_ledger.get_open(project="test-project")[0]
    monkeypatch.setattr(
        coordinator,
        "github_terminal_state",
        lambda ref, project=None: f"{ref} merged",
    )
    original_terminalize = plugin._task_ledger.terminalize
    attempts = []

    def fail_once(task_id, **kwargs):
        attempts.append(task_id)
        if len(attempts) == 1:
            raise OSError("temporary ledger write failure")
        return original_terminalize(task_id, **kwargs)

    monkeypatch.setattr(plugin._task_ledger, "terminalize", fail_once)

    plugin._sweep_stale_work_queue()
    assert plugin._task_ledger.get(card.id).status == "open"
    plugin._sweep_stale_work_queue()

    saved = plugin._task_ledger.get(card.id)
    assert saved.status == "cancelled"
    assert attempts == [card.id, card.id]


def test_queue_command_cancels_card_when_durable_link_fails(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin._work_queue, "link_task_card", lambda *_: None)
    original_terminalize = plugin._task_ledger.terminalize
    canceled_ids = []

    def record_terminalize(task_id, **kwargs):
        canceled_ids.append(task_id)
        return original_terminalize(task_id, **kwargs)

    monkeypatch.setattr(plugin._task_ledger, "terminalize", record_terminalize)

    response = plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])

    assert len(canceled_ids) == 1
    assert plugin._task_ledger.get(canceled_ids[0]).status == "cancelled"
    assert plugin._work_queue.get_all()[0].task_card_id is None
    assert "warning:" in response


@pytest.mark.asyncio
async def test_claiming_linked_task_card_assigns_its_work_slot(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)
    plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])
    card = plugin._task_ledger.get_open(project="test-project")[0]

    async def no_op_supervisor_kick():
        return None

    monkeypatch.setattr(plugin, "_supervisor_kick", no_op_supervisor_kick)

    response = await plugin._handle_claim_command(card.id)

    slot = plugin._work_queue.get_all()[0]
    claimed_card = plugin._task_ledger.get(card.id)
    assert response.startswith(f"claimed: [{card.id}]")
    assert slot.status == "assigned"
    assert slot.assigned_to == "lapis"
    assert claimed_card.status == "active"
    assert claimed_card.assignee == "lapis"
    monkeypatch.setattr(
        coordinator,
        "github_terminal_state",
        lambda ref, project=None: f"{ref} merged",
    )
    assert plugin._sweep_stale_work_queue() == []
    slot = plugin._work_queue.get_all()[0]
    assert slot.status == "assigned"
    assert slot.stale_upstream_note == "terminal upstream: #640 merged"
    assert plugin._task_ledger.get(card.id).status == "active"


def test_stale_sweep_keeps_active_paired_card_pending_with_note(
    tmp_path, monkeypatch, caplog
):
    plugin = _plugin(tmp_path, monkeypatch)
    plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])
    card = plugin._task_ledger.get_open(project="test-project")[0]
    claimed = plugin._task_ledger.claim(card.id, "ruby", project="test-project")
    assert claimed.status == "active"
    assert claimed.assignee == "ruby"
    monkeypatch.setattr(
        coordinator,
        "github_terminal_state",
        lambda ref, project=None: f"{ref} merged",
    )

    retired = plugin._sweep_stale_work_queue()

    slot = plugin._work_queue.get_all()[0]
    saved = plugin._task_ledger.get(card.id)
    assert retired == []
    assert slot.status == "pending"
    assert slot.assigned_to is None
    assert slot.stale_upstream_note == "terminal upstream: #640 merged"
    assert saved.status == "active"
    assert saved.assignee == "ruby"
    assert saved.terminal_reason is None
    assert "leaving active task card" in caplog.text.lower()


def test_claim_racing_stale_cleanup_preserves_pending_slot_and_card(
    tmp_path, monkeypatch
):
    plugin = _plugin(tmp_path, monkeypatch)
    plugin._handle_queue_command("work tied to #640", upstream_refs=["#640"])
    card = plugin._task_ledger.get_open(project="test-project")[0]
    monkeypatch.setattr(
        coordinator,
        "github_terminal_state",
        lambda ref, project=None: f"{ref} merged",
    )
    original_sweep = plugin._work_queue.sweep_stale

    def claim_after_sweep_snapshot(*args, **kwargs):
        retired = original_sweep(*args, **kwargs)
        assert retired
        assert plugin._task_ledger.claim(card.id, "ruby", project="test-project")
        return retired

    monkeypatch.setattr(plugin._work_queue, "sweep_stale", claim_after_sweep_snapshot)

    plugin._sweep_stale_work_queue()

    slot = plugin._work_queue.get_all()[0]
    saved = plugin._task_ledger.get(card.id)
    assert slot.status == "pending"
    assert slot.assigned_to is None
    assert slot.stale_upstream_note == "terminal upstream: #640 merged"
    assert saved.status == "active"
    assert saved.assignee == "ruby"


@pytest.mark.parametrize("status", sorted(TaskLedger.TERMINAL_STATUSES))
def test_pending_slot_retires_when_linked_task_card_is_terminal(
    status, tmp_path, monkeypatch
):
    plugin = _plugin(tmp_path, monkeypatch)
    plugin._handle_queue_command("work with no GitHub upstream")
    card = plugin._task_ledger.get_open(project="test-project")[0]
    card.status = status
    plugin._task_ledger._save(card)

    retired = plugin._sweep_stale_work_queue()

    slot = plugin._work_queue.get_all()[0]
    saved = plugin._task_ledger.get(card.id)
    assert [item.id for item in retired] == [slot.id]
    assert slot.status == "retired"
    assert slot.retired_reason == f"TaskLedger card [{card.id}] terminal ({status})"
    assert saved.status == status
