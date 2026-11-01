"""Compaction replay ordering must parse record stamps, not compare text.

Sessions written before stamps became true UTC carry naive local
timestamps. On a non-UTC machine (Marco's MST Mac), resuming one of them
used to drop every message after the compaction point because the loader
compared the raw strings. These fixtures pin the parse-don't-compare
behavior; stamps are derived from a fixed UTC instant and rendered in
whatever local zone the runner uses, so every test is green under both
TZ=America/Phoenix and TZ=UTC (the old code was red under Phoenix).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor.state.local import LocalStateService
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events import EventBus
from plugins.context_compaction_plugin import _append_jsonl_durable

BASE = datetime(2026, 10, 31, 8, 31, 0, tzinfo=timezone.utc)


def manager(directory):
    config = SimpleNamespace(
        _conversations_dir=directory,
        get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
    )
    return ConversationManager(config)


async def resume(directory, session_id):
    reader = manager(directory)
    history = []
    state = LocalStateService(
        SimpleNamespace(conversation_manager=reader, conversation_history=history),
        None, event_bus=EventBus(),
    )
    result = await state.resume_conversation(session_id)
    return state, reader, history, result


def z_stamp(moment_utc):
    return moment_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def naive_local_stamp(moment_utc):
    """Render moment_utc as naive local wall-clock text (pre-#476 style)."""
    return moment_utc.astimezone().replace(tzinfo=None).isoformat()


def offset_stamp(moment_utc):
    return moment_utc.isoformat()


def write_session(directory, session_id, records):
    session_file = directory / f"{session_id}.jsonl"
    with open(session_file, "a") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return session_id


def compaction_record(stamp, summary):
    return {
        "type": "context_compaction",
        "sessionId": "fixture",
        "uuid": f"u-compaction-{summary}",
        "timestamp": stamp,
        "compaction_round": 1,
        "messages_summarized": 2,
        "messages_kept": 0,
        "summary_length": len(summary),
        "pre_message_count": 2,
        "post_message_count": 0,
        "tasks_preserved": 0,
        "content": summary,
        "role": "user",
        "subtype": "compaction_summary",
    }


def chat_record(stamp, content):
    return {
        "type": "user",
        "uuid": f"u-{content}",
        "timestamp": stamp,
        "message": {"role": "user", "content": content},
    }


def contents(reader):
    return [row["content"] for row in reader.messages]


@pytest.mark.asyncio
async def test_naive_local_tail_survives_z_compaction(tmp_path):
    # Old behavior: "2026-10-31T01:45:00" (naive local, actually 08:45Z on
    # MST) sorts before "2026-10-31T08:31:00Z" as text and was dropped.
    compaction_at = BASE
    tail_at = compaction_at + timedelta(minutes=14)
    sid = write_session(tmp_path, "naive-tail", [
        compaction_record(z_stamp(compaction_at), "Compacted"),
        chat_record(naive_local_stamp(tail_at), "Post naive"),
    ])
    _, reader, _, _ = await resume(tmp_path, sid)
    assert contents(reader) == ["Compacted", "Post naive"]


@pytest.mark.asyncio
async def test_microsecond_z_tail_survives_z_compaction(tmp_path):
    # Old behavior: "...00.120000+00:00" compares < "...00Z" because "."
    # sorts before "Z", so a tail 120ms after compaction was dropped.
    compaction_at = BASE
    tail_at = compaction_at + timedelta(milliseconds=120)
    sid = write_session(tmp_path, "micro-tail", [
        compaction_record(z_stamp(compaction_at), "Compacted"),
        chat_record(offset_stamp(tail_at), "Tail micros"),
    ])
    _, reader, _, _ = await resume(tmp_path, sid)
    assert contents(reader) == ["Compacted", "Tail micros"]


@pytest.mark.asyncio
async def test_naive_compaction_suppresses_only_genuinely_earlier_records(tmp_path):
    # Naive local compaction stamp read as local time: an earlier Z record
    # is suppressed, a later one is kept. Old behavior kept both because
    # "08:30:00+00:00" and "08:32:00+00:00" both sort after "01:31:00".
    compaction_at = BASE
    sid = write_session(tmp_path, "naive-compaction", [
        chat_record(z_stamp(compaction_at - timedelta(minutes=1)), "Before"),
        compaction_record(naive_local_stamp(compaction_at), "Compacted"),
        chat_record(z_stamp(compaction_at + timedelta(minutes=1)), "After"),
    ])
    _, reader, _, _ = await resume(tmp_path, sid)
    assert contents(reader) == ["Compacted", "After"]


def test_unparseable_and_missing_stamps_fail_open(tmp_path):
    # A stamp the parser cannot understand never suppresses its record.
    # Asserted at the loader level: resume also persists, and persisting a
    # None-stamped record trips a separate save-path bug that real writes
    # never hit (every live record carries a stamp).
    compaction_at = BASE
    sid = write_session(tmp_path, "fail-open", [
        compaction_record(z_stamp(compaction_at), "Compacted"),
        chat_record("not-a-timestamp", "Weird stamp"),
        {"type": "user", "uuid": "u-no-stamp", "message": {"role": "user", "content": "No stamp"}},
    ])
    reader = manager(tmp_path)
    assert reader.load_session(sid)
    assert contents(reader) == ["Compacted", "Weird stamp", "No stamp"]


def test_durable_append_lands_on_primary(tmp_path):
    target = tmp_path / "session_ok.jsonl"
    record = {"type": "context_compaction", "content": "summary"}
    assert _append_jsonl_durable(str(target), record) is None
    assert target.read_text() == json.dumps(record) + "\n"


def test_durable_append_spills_to_sidecar_after_two_failures(tmp_path, monkeypatch):
    target = tmp_path / "session_full.jsonl"
    real_open = open

    def flaky_open(file, mode="r", *args, **kwargs):
        if Path(file) == target:
            raise OSError("disk hiccup")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", flaky_open)
    record = {"type": "context_compaction", "content": "summary"}
    spilled = _append_jsonl_durable(str(target), record)
    sidecar = str(target) + ".compaction-recovery.jsonl"
    assert spilled == sidecar
    assert Path(sidecar).read_text() == json.dumps(record) + "\n"


def test_stampless_compaction_keeps_prior_boundary(tmp_path):
    # A later compaction record with a missing stamp must not reset a
    # valid earlier boundary to None: resetting it un-suppressed the
    # pre-boundary history on replay. The prior stamp is kept, the
    # superseded first summary is dropped (last-round-wins), and the
    # stampless second summary still replays. The stamp is a non-None
    # unparseable string: parse -> None is the exact hazard, while a
    # literal None field or any non-isoformat string trips the separate
    # save-path parse bug (tracked in the follow-up issue) once resume
    # persists, so this drives load_session directly like the fail-open
    # test above. The hazard itself lives entirely in the loader.
    compaction_at = BASE
    sid = write_session(tmp_path, "stampless-keeps-prior", [
        chat_record(z_stamp(compaction_at - timedelta(minutes=1)), "Before"),
        compaction_record(z_stamp(compaction_at), "First"),
        chat_record(z_stamp(compaction_at + timedelta(minutes=1)), "Between"),
        compaction_record("not-a-timestamp", "Second"),
        chat_record(z_stamp(compaction_at + timedelta(minutes=2)), "Tail"),
    ])
    reader = manager(tmp_path)
    assert reader.load_session(sid)
    # Order: final summary first, then post-boundary tail records. Under
    # the old code the reset to None would un-suppress "Before" here.
    assert contents(reader) == ["Second", "Between", "Tail"]
