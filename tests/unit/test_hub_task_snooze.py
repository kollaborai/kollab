"""Snooze quiets task reminders without faking progress.

Before this existed, an agent holding a task had two options: checkpoint
(a lie if nothing happened) or ignore the reminder (which brought it back
every 5 minutes). Snooze is the honest third option.
"""

import re
import time

import pytest

from plugins.hub.nudge_engine import CHECKPOINT_IDLE_THRESHOLD, NudgeEngine
from plugins.hub.task_ledger import TaskLedger


@pytest.fixture
def ledger(tmp_path):
    return TaskLedger(tasks_dir=str(tmp_path))


def _card(ledger):
    return ledger.create(
        assigner="koordinator", assignee="lapis", directive="do the thing"
    )


def test_snooze_suppresses_cron(ledger):
    card = _card(ledger)
    card.updated_at = time.time() - 999  # long overdue
    ledger._save_preserve(card)
    assert [c.id for c in ledger.get_cron_due()] == [card.id]

    ledger.snooze(card.id, minutes=30)
    assert ledger.get_cron_due() == []


def test_snooze_expires(ledger):
    card = _card(ledger)
    card.updated_at = time.time() - 999
    ledger._save_preserve(card)
    ledger.snooze(card.id, minutes=-5)  # clamped to 0 -> already expired
    assert [c.id for c in ledger.get_cron_due()] == [card.id]


def test_snooze_is_capped(ledger):
    card = _card(ledger)
    snoozed = ledger.snooze(card.id, minutes=10_000)
    assert snoozed.snoozed_until - time.time() <= TaskLedger.MAX_SNOOZE_SECONDS + 1


def test_snooze_keeps_task_active_and_visible(ledger):
    card = _card(ledger)
    ledger.snooze(card.id, minutes=30)
    reloaded = ledger.get(card.id)
    assert reloaded.status == "active"
    assert reloaded.is_snoozed()
    # still injected into the system prompt -- snooze silences reminders,
    # it does not hide the work
    assert card.id in [c.id for c in ledger.get_active_for("lapis")]


def test_snooze_does_not_reset_cron_ttl(ledger):
    card = _card(ledger)
    original = card.updated_at - 100
    card.updated_at = original
    ledger._save_preserve(card)
    ledger.snooze(card.id, minutes=5)
    assert ledger.get(card.id).updated_at == pytest.approx(original)


def test_qa_review_stays_visible_until_review_ttl(ledger):
    card = _card(ledger)
    qa_card = ledger.request_qa(card.id, "ready for review")

    assert qa_card.qa_requested_at is not None
    assert card.id in [item.id for item in ledger.get_active_for("lapis")]


def test_expired_qa_review_is_terminalized_before_prompt_injection(ledger):
    card = _card(ledger)
    qa_card = ledger.request_qa(card.id, "old QA result")
    qa_card.qa_requested_at = time.time() - 10
    qa_card.updated_at = qa_card.qa_requested_at
    qa_card.cron_ttl_seconds = 1
    ledger._save_preserve(qa_card)

    assert ledger.get_active_for("lapis") == []
    stale = ledger.get(card.id)
    assert stale.status == "obsolete"
    assert stale.cron_active is False
    assert stale.terminal_actor == "task-ledger"
    assert stale.terminal_reason == "QA review expired without reviewer action"


def test_snooze_survives_roundtrip(ledger):
    card = _card(ledger)
    ledger.snooze(card.id, minutes=30)
    assert ledger.get(card.id).snoozed_until > time.time()


def test_create_rejects_blank_assignment_fields(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path))

    with pytest.raises(ValueError, match="non-empty assignee"):
        ledger.create(assigner="koordinator", assignee="", directive="inspect")

    ledger.expect_reply(
        task_id="task-1",
        assignee="lapis",
        requested_by="koordinator",
        message_id="message-1",
        deadline_seconds=120,
    )
    assert ledger.get_all() == []


def test_cron_ttl_terminalizes_stale_active_task(ledger):
    card = _card(ledger)
    card.updated_at = time.time() - 10
    card.cron_ttl_seconds = 1
    card.cron_active = False
    ledger._save_preserve(card)

    assert ledger.get_cron_due() == []
    stale = ledger.get(card.id)
    assert stale.status == "obsolete"
    assert stale.cron_active is False
    assert "task-cron expired" in stale.terminal_reason


def test_nudge_quotes_real_task_id_and_offers_snooze():
    engine = NudgeEngine()
    engine.observe_task_assignment("lapis", has_task=True, task_ids=["abc12345"])
    for _ in range(CHECKPOINT_IDLE_THRESHOLD):
        engine.observe_response(identity="lapis", response="thinking", used_real_tools=True)

    nudge = engine.evaluate("lapis")
    assert nudge is not None
    assert "abc12345" in nudge
    assert "TASK_ID" not in nudge  # placeholder is not actionable
    assert re.search(r'<task_snooze id="abc12345" minutes="\d+"\s*/>', nudge)


def test_nudge_silent_without_tasks():
    engine = NudgeEngine()
    engine.observe_task_assignment("lapis", has_task=False, task_ids=[])
    for _ in range(CHECKPOINT_IDLE_THRESHOLD):
        engine.observe_response(identity="lapis", response="thinking", used_real_tools=True)
    assert "task" not in (engine.evaluate("lapis") or "").lower()
