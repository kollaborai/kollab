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
        coordinator, "github_terminal_state", lambda ref: f"{ref} merged"
    )

    retired = plugin._sweep_stale_work_queue()

    saved = plugin._task_ledger.get(card.id)
    assert [slot.task_card_id for slot in retired] == [card.id]
    assert saved.status == "cancelled"
    assert saved.terminal_actor == "work-queue-sweep"
    assert "#640 merged" in saved.terminal_reason
    assert plugin._task_ledger.get_open(project="test-project") == []


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
        coordinator, "github_terminal_state", lambda ref: f"{ref} merged"
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
