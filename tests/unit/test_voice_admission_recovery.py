"""A voice batch caught mid-dispatch by a restart is recovered or surfaced (#599).

Plugin A is "killed" part way through dispatch (a BaseException that no
``except Exception`` in the plugin swallows, so nothing after it runs). A fresh
plugin B, with a new client id, then starts against the same voice root.
"""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import plugins.voice_plugin as voice_plugin
from plugins.voice_plugin import VoicePlugin


class Crash(BaseException):
    """The process dies here."""


class FakeBus:
    def __init__(self):
        self.services = {}

    def register_service(self, name, service):
        self.services[name] = service

    def get_service(self, name):
        return self.services.get(name)


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def batch(text="Check the tests"):
    now = time.time()
    return [
        {
            "event_id": "evt-1",
            "text": text,
            "final": True,
            "started_at": utc(now - 1),
            "ended_at": utc(now),
        }
    ]


def started(monkeypatch, tmp_path):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.validate_turn = AsyncMock()
    plugin._lease = {"epoch": "epoch", "token": "t", "stream_id": "s", "cursor": 0}
    return plugin


async def die_during_dispatch(plugin, records, deliver):
    plugin._deliver = deliver
    with pytest.raises(Crash):
        await plugin._admit_speech(records, "epoch", time.time())


async def restart(monkeypatch, tmp_path):
    """A fresh plugin in a new process: the old client id is dead."""
    monkeypatch.setattr(voice_plugin, "pid_alive", lambda pid: False, raising=False)
    plugin = started(monkeypatch, tmp_path)
    recover = getattr(plugin, "recover_attempts", None)
    if recover:
        await recover()
    return plugin


@pytest.mark.asyncio
async def test_batch_the_host_never_received_is_not_lost_or_called_delivered(
    monkeypatch, tmp_path
):
    old = started(monkeypatch, tmp_path)

    async def deliver(data):
        raise Crash  # before the host reserved anything

    await die_during_dispatch(old, batch(), deliver)

    new = await restart(monkeypatch, tmp_path)
    status = new.status()
    assert status["pending"] == 1, "the unreceived batch must be delivered again"
    assert not status.get("unknown_attempts")


@pytest.mark.asyncio
async def test_batch_the_host_queued_is_attributed_and_never_resent(
    monkeypatch, tmp_path
):
    old = started(monkeypatch, tmp_path)
    submit = AsyncMock(return_value={"status": "queued"})

    async def deliver(data):
        await old.admit(data, submit)
        raise Crash  # the response never reached the dispatcher

    await die_during_dispatch(old, batch(), deliver)

    new = await restart(monkeypatch, tmp_path)
    status = new.status()
    assert status["pending"] == 0, "an admitted batch must not be sent again"
    assert not status.get("unknown_attempts")
    assert status.get("recovered_attempts") == 1, "the admitted batch is attributed"
    assert [r["event_id"] for r in new._recent_transcripts] == ["evt-1"]
    submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_with_unknown_host_outcome_is_surfaced_until_dismissed(
    monkeypatch, tmp_path
):
    old = started(monkeypatch, tmp_path)

    async def submit(context):
        raise Crash  # the host reserved the admission, then died

    async def deliver(data):
        return await old.admit(data, submit)

    await die_during_dispatch(old, batch("Delete the old branch"), deliver)

    new = await restart(monkeypatch, tmp_path)
    status = new.status()
    assert status["pending"] == 0, "an unknown outcome must not be resent"
    unknown = status.get("unknown_attempts")
    assert unknown and unknown[0]["text"] == "Delete the old branch"

    # Another restart still shows it: the mark is on disk, not in memory.
    again = await restart(monkeypatch, tmp_path)
    assert len(again.status().get("unknown_attempts") or []) == 1
    assert again.status()["pending"] == 0

    await again.dismiss_unknown_attempts()
    assert not again.status().get("unknown_attempts")
    final = await restart(monkeypatch, tmp_path)
    assert not final.status().get("unknown_attempts")


@pytest.mark.asyncio
async def test_batch_cut_off_by_voice_off_in_the_same_process_is_recovered(
    monkeypatch, tmp_path
):
    plugin = started(monkeypatch, tmp_path)

    async def deliver(data):
        raise Crash

    await die_during_dispatch(plugin, batch(), deliver)
    await plugin.recover_attempts()
    assert plugin.status()["pending"] == 1
