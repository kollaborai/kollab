"""Admission and recovery behavior for durable Hub mailbox controls."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.hub import messenger
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage


def _control(message_id: str, *, action: str = "message") -> HubMessage:
    return HubMessage(
        id=message_id,
        action=action,
        from_identity="coordinator",
        to="receiver",
        content=f"control {message_id}",
        metadata={"task_assignment": True},
    )


def _send(target: str, message: HubMessage) -> None:
    asyncio.run(AgentMessenger.send_to_file(target, message))


def test_full_control_mailbox_rejects_without_evicting_accepted_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 2)
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    first = _control("accepted-1")
    second = _control("accepted-2")

    _send("receiver", first)
    _send("receiver", second)

    with pytest.raises(RuntimeError, match="durable inbox capacity"):
        _send("receiver", _control("rejected-3"))

    stored = AgentMessenger.read_mailbox("receiver", restart_boundary=0)
    assert [message.id for message in stored] == [first.id, second.id]


def test_control_admission_is_idempotent_and_ordinary_limit_is_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    monkeypatch.setattr(messenger, "INBOX_MAX_SIZE", 1)
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    control = _control("same-id")

    _send("receiver", control)
    _send("receiver", control)
    _send(
        "receiver",
        HubMessage(
            id="ordinary-1",
            action="message",
            from_identity="coordinator",
            to="receiver",
            content="ordinary chatter",
        ),
    )

    stored = AgentMessenger.read_mailbox("receiver", restart_boundary=0)
    assert [message.id for message in stored].count(control.id) == 1
    assert len(list((tmp_path / "receiver").glob("*.json"))) == 0


def test_concurrent_control_admission_never_exceeds_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)

    def attempt(index: int) -> str:
        try:
            _send("receiver", _control(f"concurrent-{index}"))
        except RuntimeError as exc:
            assert "durable inbox capacity" in str(exc)
            return "rejected"
        return "accepted"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    assert results.count("accepted") == 1
    control_files = list((tmp_path / "receiver").glob("*.json"))
    assert len(control_files) == 1
    assert json.loads(control_files[0].read_text())["metadata"]["task_assignment"]


def test_storage_failure_is_visible_and_does_not_accept_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    with patch("plugins.hub.messenger._atomic_write", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            _send("receiver", _control("storage-failure"))

    assert list((tmp_path / "receiver").glob("*.json")) == []


def test_task_cron_control_survives_restart_boundary_and_capacity_drains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(messenger, "INBOX_MAX_CONTROLS", 1)
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    cron = _control("cron-1")
    cron.metadata = {"task_cron": True}
    _send("receiver", cron)

    replayed = AgentMessenger.read_mailbox("receiver", restart_boundary=10**12)
    assert [message.id for message in replayed] == [cron.id]

    retry = _control("cron-2")
    _send("receiver", retry)
    assert [message.id for message in AgentMessenger.read_mailbox("receiver")] == [
        retry.id
    ]
