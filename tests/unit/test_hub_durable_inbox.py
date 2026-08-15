"""Tests for durable per-agent inbox with bounded replay-on-reconnect.

Design: messages for offline agents are persisted in
  get_messages_dir() / <identity> / <ts>-<uuid>-from-<sender>.json

Bounds:
  - At write time: inbox pruned to INBOX_MAX_SIZE (oldest evicted).
  - At read time:  TTL-expired messages silently discarded.
  - Replay cap:    when total > max_replay, a summary HubMessage is
                   prepended and only the newest max_replay messages returned.

Single-block delivery: when a bounded replay batch is detected,
  ordinary messages are coalesced into one inject_system_message call.
  Task-cron controls retain normal _on_message_received semantics and are
  excluded from the replay block.

Coordinator visibility: get_all_inbox_counts() scans dirs without consuming.
"""

import asyncio
import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.messenger import (
    INBOX_MAX_REPLAY,
    INBOX_MAX_SIZE,
    INBOX_TTL_SECS,
    AgentMessenger,
    _prune_inbox,
)
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.task_ledger import TaskLedger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(from_identity: str = "koordinator", to: str = "lapis", content: str = "hello") -> HubMessage:
    return HubMessage(
        action="message",
        from_identity=from_identity,
        to=to,
        content=content,
    )


def _write_raw(msg_dir: Path, msg: HubMessage, ts_offset: float = 0.0) -> None:
    """Write a message file with a specific timestamp offset (for age tests)."""
    msg_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ts = time.time() + ts_offset
    filename = f"{ts:.6f}-testid-from-{msg.from_identity}.json"
    data = msg.to_dict()
    data["timestamp"] = ts
    (msg_dir / filename).write_text(json.dumps(data))


async def _send(target: str, msg: HubMessage, msg_base: Path) -> None:
    with patch("plugins.hub.messenger.get_messages_dir", return_value=msg_base):
        await AgentMessenger.send_to_file(target, msg)


# ---------------------------------------------------------------------------
# 1. Inbox size bound at write time
# ---------------------------------------------------------------------------

class TestInboxSizeBound:
    def test_prune_inbox_evicts_oldest_when_over_limit(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        # Write 10 files named by timestamp (sorted oldest-first)
        for i in range(10):
            f = inbox / f"{i:06d}.000000-x-from-sender.json"
            f.write_text(json.dumps({"id": str(i)}))

        _prune_inbox(inbox, max_size=5)

        remaining = sorted(inbox.glob("*.json"))
        assert len(remaining) == 5
        # The 5 newest (indices 5-9) survive
        names = [f.name for f in remaining]
        assert all(name.startswith(f"00000{i}") for i, name in zip(range(5, 10), names))

    def test_prune_inbox_noop_when_under_limit(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        for i in range(3):
            (inbox / f"{i:06d}.json").write_text("{}")

        _prune_inbox(inbox, max_size=5)

        assert len(list(inbox.glob("*.json"))) == 3

    def test_send_to_file_prunes_on_write(self, tmp_path: Path) -> None:
        """After each write, inbox must not exceed INBOX_MAX_SIZE."""

        async def run():
            for i in range(INBOX_MAX_SIZE + 5):
                msg = _make_msg(content=f"msg {i}")
                await _send("lapis", msg, tmp_path)

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            asyncio.run(run())

        inbox = tmp_path / "lapis"
        count = sum(1 for _ in inbox.glob("*.json"))
        assert count <= INBOX_MAX_SIZE


# ---------------------------------------------------------------------------
# 2. TTL expiry at read time
# ---------------------------------------------------------------------------

class TestInboxTTLExpiry:
    def test_expired_messages_not_returned(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        msg = _make_msg()
        data = msg.to_dict()
        # Stamp as older than TTL
        data["timestamp"] = time.time() - INBOX_TTL_SECS - 1
        f = inbox / "0000001.000000-x-from-koordinator.json"
        f.write_text(json.dumps(data))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis")

        assert result == []
        # File should be deleted
        assert not f.exists()

    def test_fresh_messages_are_returned(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        msg = _make_msg(content="fresh")
        data = msg.to_dict()
        data["timestamp"] = time.time()
        f = inbox / "9999999.000000-x-from-koordinator.json"
        f.write_text(json.dumps(data))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis")

        assert len(result) == 1
        assert result[0].content == "fresh"

    def test_mixed_ages_only_fresh_returned(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        now = time.time()

        for i, (age, content) in enumerate([
            (INBOX_TTL_SECS + 100, "old1"),
            (INBOX_TTL_SECS + 200, "old2"),
            (10, "recent1"),
            (5, "recent2"),
        ]):
            data = _make_msg(content=content).to_dict()
            data["timestamp"] = now - age
            (inbox / f"{i:06d}.000000-x-from-k.json").write_text(json.dumps(data))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis")

        assert len(result) == 2
        contents = {m.content for m in result}
        assert contents == {"recent1", "recent2"}


# ---------------------------------------------------------------------------
# 3. Bounded replay with summary header
# ---------------------------------------------------------------------------

class TestBoundedReplay:
    def _write_n_fresh_messages(self, inbox: Path, n: int, sender: str = "koordinator") -> None:
        inbox.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for i in range(n):
            data = _make_msg(from_identity=sender, content=f"msg {i}").to_dict()
            data["timestamp"] = now - (n - i)  # oldest first
            (inbox / f"{i:08d}.000000-x-from-{sender}.json").write_text(json.dumps(data))

    def test_under_limit_no_summary(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        self._write_n_fresh_messages(inbox, 5)

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=20)

        assert len(result) == 5
        assert all(m.action == "message" for m in result)

    def test_over_limit_prepends_summary(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        self._write_n_fresh_messages(inbox, 30)

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=20)

        assert len(result) == 21  # summary + 20 messages
        summary = result[0]
        assert summary.action == "inbox_summary"
        assert "30" in summary.content
        assert "20" in summary.content
        assert summary.metadata["total"] == 30
        assert summary.metadata["showing"] == 20
        assert summary.metadata["dropped"] == 10

    def test_over_limit_returns_newest(self, tmp_path: Path) -> None:
        """The last max_replay messages (newest) must be in the result."""
        inbox = tmp_path / "lapis"
        self._write_n_fresh_messages(inbox, 25)

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=10)

        # result[0] is summary, result[1:] are the 10 newest messages
        msgs = result[1:]
        assert len(msgs) == 10
        contents = [m.content for m in msgs]
        # Newest 10 are msg 15..24
        assert "msg 15" in contents
        assert "msg 24" in contents
        assert "msg 0" not in contents

    def test_summary_lists_senders(self, tmp_path: Path) -> None:
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        now = time.time()
        for i, sender in enumerate(["alice", "bob", "carol"] * 10):
            data = _make_msg(from_identity=sender, content=f"msg {i}").to_dict()
            data["timestamp"] = now - i
            (inbox / f"{i:08d}.000000-x.json").write_text(json.dumps(data))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=10)

        summary = result[0]
        assert "alice" in summary.content
        assert "bob" in summary.content
        assert "carol" in summary.content

    def test_max_replay_zero_means_no_limit(self, tmp_path: Path) -> None:
        """max_replay=0 (default) returns all messages without summary."""
        inbox = tmp_path / "lapis"
        self._write_n_fresh_messages(inbox, 30)

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=0)

        assert len(result) == 30
        assert all(m.action == "message" for m in result)

    def test_prune_preserves_old_task_controls_and_bounds_ordinary(
        self, tmp_path: Path
    ) -> None:
        """Write-time pruning evicts ordinary chatter before controls."""
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        controls = [
            HubMessage(
                action="message",
                from_identity="task-cron",
                content="old task-cron",
                metadata={"task_cron": True, "task_id": "cron-old"},
            ),
            HubMessage(
                action="message",
                from_identity="koordinator",
                content="old assignment",
                metadata={"task_assignment": True, "task_id": "task-old"},
            ),
        ]
        for i, message in enumerate(controls):
            (inbox / f"{i:08d}.json").write_text(json.dumps(message.to_dict()))
        for i in range(4):
            (inbox / f"{i + 2:08d}.json").write_text(
                json.dumps(_make_msg(content=f"ordinary {i}").to_dict())
            )

        from plugins.hub.messenger import _prune_inbox

        _prune_inbox(inbox, max_size=2)

        remaining = [json.loads(path.read_text()) for path in sorted(inbox.glob("*.json"))]
        remaining_ids = {data["id"] for data in remaining}
        assert {message.id for message in controls} <= remaining_ids
        ordinary_count = sum(
            not data.get("metadata", {}).get("task_cron")
            and not data.get("metadata", {}).get("task_assignment")
            for data in remaining
        )
        assert ordinary_count == 2

    def test_prune_malformed_json_value_does_not_disable_bound(
        self, tmp_path: Path
    ) -> None:
        """Malformed and non-object JSON remain evictable ordinary entries."""
        payloads = ["[]", '"scalar"', "null", "not-json"]
        for case, payload in enumerate(payloads):
            inbox = tmp_path / f"lapis-{case}"
            inbox.mkdir()
            (inbox / "00000000-malformed.json").write_text(payload)
            for i in range(5):
                (inbox / f"{i + 1:08d}.json").write_text(
                    json.dumps(_make_msg(content=f"ordinary {i}").to_dict())
                )

            _prune_inbox(inbox, max_size=2)

            assert len(list(inbox.glob("*.json"))) == 2

    def test_ttl_expired_task_cron_is_preserved_for_stale_ack(
        self, tmp_path: Path
    ) -> None:
        """TTL cannot silently delete a cron control before classification."""
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        reminder = HubMessage(
            action="message",
            from_identity="task-cron",
            to="lapis",
            content="[task reminder: ttl-old] classify me",
            metadata={"task_cron": True, "task_id": "ttl-old"},
        )
        data = reminder.to_dict()
        data["timestamp"] = time.time() - INBOX_TTL_SECS - 100
        (inbox / "old-control.json").write_text(json.dumps(data))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=20)

        assert [message.id for message in result] == [reminder.id]

    def test_old_task_cron_is_preserved_beyond_ordinary_replay_bound(
        self, tmp_path: Path
    ) -> None:
        """Controls survive the cap while ordinary replay stays bounded."""
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        now = time.time()
        reminder = HubMessage(
            action="message",
            from_identity="task-cron",
            to="lapis",
            content="[task reminder: stale-old] classify me",
            metadata={"task_cron": True, "task_id": "stale-old"},
        )
        reminder_data = reminder.to_dict()
        reminder_data["timestamp"] = now - 100
        (inbox / "00000000.000000-x-from-task-cron.json").write_text(
            json.dumps(reminder_data)
        )
        for i in range(25):
            data = _make_msg(content=f"ordinary {i}").to_dict()
            data["timestamp"] = now - (25 - i)
            (inbox / f"{i + 1:08d}.000000-x-from-peer.json").write_text(
                json.dumps(data)
            )

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=20)

        assert result[0].action == "inbox_summary"
        replayed = result[1:]
        assert reminder.id in {message.id for message in replayed}
        ordinary = [
            message for message in replayed if not message.metadata.get("task_cron")
        ]
        assert len(ordinary) == 20
        assert result[0].metadata["controls_preserved"] == 1
        assert result[0].metadata["dropped"] == 5

    def test_old_task_assignment_is_preserved_beyond_ordinary_replay_bound(
        self, tmp_path: Path
    ) -> None:
        """Task assignments receive the same control-priority replay policy."""
        inbox = tmp_path / "lapis"
        inbox.mkdir()
        now = time.time()
        assignment = HubMessage(
            action="message",
            from_identity="koordinator",
            to="lapis",
            content="objective: retained assignment",
            metadata={"task_assignment": True, "task_id": "task-old"},
        )
        data = assignment.to_dict()
        data["timestamp"] = now - 100
        (inbox / "00000000.000000-x-from-koordinator.json").write_text(
            json.dumps(data)
        )
        for i in range(25):
            ordinary = _make_msg(content=f"ordinary {i}").to_dict()
            ordinary["timestamp"] = now - (25 - i)
            (inbox / f"{i + 1:08d}.000000-x-from-peer.json").write_text(
                json.dumps(ordinary)
            )

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailbox("lapis", max_replay=20)

        assert assignment.id in {message.id for message in result[1:]}
        assert result[0].metadata["controls_preserved"] == 1
        assert result[0].metadata["dropped"] == 5


# ---------------------------------------------------------------------------
# 4. read_mailboxes passes max_replay through and deduplicates
# ---------------------------------------------------------------------------

class TestReadMailboxes:
    def _write_to(self, tmp_path: Path, identity: str, n: int) -> None:
        inbox = tmp_path / identity
        inbox.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for i in range(n):
            data = _make_msg(content=f"{identity} msg {i}").to_dict()
            data["timestamp"] = now - i
            (inbox / f"{i:08d}.000000-x.json").write_text(json.dumps(data))

    def test_deduplicates_same_message_across_keys(self, tmp_path: Path) -> None:
        """Same message written under two keys must appear only once."""
        inbox1 = tmp_path / "agent-abc"
        inbox2 = tmp_path / "lapis"
        inbox1.mkdir(parents=True, exist_ok=True)
        inbox2.mkdir(parents=True, exist_ok=True)
        msg = _make_msg(content="shared")
        data = msg.to_dict()
        (inbox1 / "00000001.000000-x.json").write_text(json.dumps(data))
        (inbox2 / "00000001.000000-x.json").write_text(json.dumps(data))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailboxes(["agent-abc", "lapis"])

        ids = [m.id for m in result if m.action != "inbox_summary"]
        assert ids.count(msg.id) == 1

    def test_max_replay_forwarded(self, tmp_path: Path) -> None:
        self._write_to(tmp_path, "lapis", 30)

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailboxes(["lapis"], max_replay=10)

        # summary + 10 messages
        assert result[0].action == "inbox_summary"
        assert len(result) == 11


# ---------------------------------------------------------------------------
# 5. Coordinator visibility: get_all_inbox_counts
# ---------------------------------------------------------------------------

class TestCoordinatorInboxVisibility:
    def test_counts_non_empty_inboxes(self, tmp_path: Path) -> None:
        (tmp_path / "lapis").mkdir()
        (tmp_path / "ruby").mkdir()
        (tmp_path / "koordinator").mkdir()

        # Write 3 to lapis, 0 to ruby, 1 to koordinator
        for i in range(3):
            (tmp_path / "lapis" / f"{i}.json").write_text("{}")
        (tmp_path / "koordinator" / "0.json").write_text("{}")

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            counts = AgentMessenger.get_all_inbox_counts()

        assert counts.get("lapis") == 3
        assert counts.get("koordinator") == 1
        assert "ruby" not in counts  # empty inbox excluded

    def test_get_inbox_count_single(self, tmp_path: Path) -> None:
        (tmp_path / "lapis").mkdir()
        for i in range(7):
            (tmp_path / "lapis" / f"{i}.json").write_text("{}")

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            count = AgentMessenger.get_inbox_count("lapis")

        assert count == 7

    def test_get_inbox_count_missing_identity(self, tmp_path: Path) -> None:
        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            assert AgentMessenger.get_inbox_count("nobody") == 0

    def test_counts_empty_when_all_inboxes_empty(self, tmp_path: Path) -> None:
        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            assert AgentMessenger.get_all_inbox_counts() == {}


# ---------------------------------------------------------------------------
# 6. Integration: send_to_file + read_mailbox round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_send_and_receive_single_message(self, tmp_path: Path) -> None:
        async def run():
            msg = _make_msg(content="hello lapis")
            await AgentMessenger.send_to_file("lapis", msg)
            result = AgentMessenger.read_mailbox("lapis")
            return result

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            msgs = asyncio.run(run())

        assert len(msgs) == 1
        assert msgs[0].content == "hello lapis"

    def test_messages_consumed_on_read(self, tmp_path: Path) -> None:
        async def run():
            await AgentMessenger.send_to_file("lapis", _make_msg())
            AgentMessenger.read_mailbox("lapis")  # consume
            return AgentMessenger.read_mailbox("lapis")  # second read = empty

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = asyncio.run(run())

        assert result == []

    def test_flood_bounded_at_write(self, tmp_path: Path) -> None:
        """Sending 100 messages to an offline agent caps at INBOX_MAX_SIZE."""
        async def run():
            for i in range(100):
                await AgentMessenger.send_to_file("lapis", _make_msg(content=f"flood {i}"))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            asyncio.run(run())
            count = AgentMessenger.get_inbox_count("lapis")

        assert count <= INBOX_MAX_SIZE

    def test_replay_on_reconnect_bounded(self, tmp_path: Path) -> None:
        """Simulates reconnect: 71 queued messages → summary + last 20."""
        inbox = tmp_path / "koordinator"
        inbox.mkdir()
        now = time.time()
        for i in range(71):
            data = _make_msg(from_identity="lapis", content=f"reply {i}").to_dict()
            data["timestamp"] = now - (71 - i)
            (inbox / f"{i:08d}.000000-x.json").write_text(json.dumps(data))

        with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
            result = AgentMessenger.read_mailboxes(
                ["koordinator-old-id", "koordinator"],
                max_replay=INBOX_MAX_REPLAY,
            )

        assert result[0].action == "inbox_summary"
        assert "71" in result[0].content
        assert result[0].metadata["total"] == 71
        assert result[0].metadata["showing"] == INBOX_MAX_REPLAY
        # Only newest 20 after summary
        assert len(result) == INBOX_MAX_REPLAY + 1


# ---------------------------------------------------------------------------
# 7. Single-block delivery: _deliver_inbox_batch / _inject_inbox_replay
# ---------------------------------------------------------------------------

def _make_plugin_stub():
    """Create a minimal HubPlugin stub (bypasses __init__) for method tests."""
    from plugins.hub.plugin import HubPlugin

    plugin = HubPlugin.__new__(HubPlugin)
    plugin.event_bus = None
    plugin._identity = None
    plugin._seen_messages = OrderedDict()
    plugin._seen_content_hashes = {}
    return plugin


def _make_summary_batch(n_msgs: int = 5, n_total: int = 30) -> List[HubMessage]:
    """Build a [summary, msg1, ..., msgN] list as read_mailboxes would return."""
    summary = HubMessage(
        action="inbox_summary",
        from_identity="hub",
        content=f"[offline inbox] {n_total} message(s) arrived while offline "
                f"(senders: koordinator). Showing most recent {n_msgs}.",
        timestamp=0.0,
        metadata={"total": n_total, "showing": n_msgs, "senders": ["koordinator"],
                  "dropped": n_total - n_msgs},
    )
    msgs = [
        HubMessage(
            action="message",
            from_identity="koordinator",
            content=f"msg {i}",
            timestamp=float(i),
        )
        for i in range(n_msgs)
    ]
    return [summary] + msgs


class TestSingleBlockDelivery:
    """_deliver_inbox_batch must call inject_system_message ONCE for replay batches."""

    def _run(self, coro):
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # _deliver_inbox_batch: replay batch → single injection
    # ------------------------------------------------------------------

    def test_replay_batch_calls_inject_once(self) -> None:
        """inject_system_message called exactly once for a replay batch."""
        plugin = _make_plugin_stub()

        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock

        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        batch = _make_summary_batch(n_msgs=5, n_total=30)

        self._run(plugin._deliver_inbox_batch(batch))

        inject_mock.assert_awaited_once()

    def test_replay_batch_subtype_is_inbox_replay(self) -> None:
        """inject_system_message is called with subtype='inbox_replay'."""
        plugin = _make_plugin_stub()

        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock

        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        batch = _make_summary_batch(n_msgs=3, n_total=25)
        self._run(plugin._deliver_inbox_batch(batch))

        _, kwargs = inject_mock.await_args
        assert kwargs.get("subtype") == "inbox_replay"

    def test_replay_block_contains_summary_line(self) -> None:
        """The injected block starts with the summary content."""
        plugin = _make_plugin_stub()

        captured: List[str] = []

        async def capture(text, subtype=None):
            captured.append(text)

        llm_mock = MagicMock()
        llm_mock.inject_system_message = capture

        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        batch = _make_summary_batch(n_msgs=3, n_total=25)
        self._run(plugin._deliver_inbox_batch(batch))

        assert len(captured) == 1
        assert "[offline inbox]" in captured[0]

    def test_replay_block_contains_all_replay_messages(self) -> None:
        """Every replayed message's content appears in the single block."""
        plugin = _make_plugin_stub()

        captured: List[str] = []

        async def capture(text, subtype=None):
            captured.append(text)

        llm_mock = MagicMock()
        llm_mock.inject_system_message = capture

        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        batch = _make_summary_batch(n_msgs=5, n_total=30)
        self._run(plugin._deliver_inbox_batch(batch))

        block = captured[0]
        for i in range(5):
            assert f"msg {i}" in block

    def test_replay_stale_task_cron_is_acknowledged_and_excluded(
        self, tmp_path: Path
    ) -> None:
        """Stale task-cron controls use the receive path, not the replay block."""
        plugin = _make_plugin_stub()
        plugin._task_ledger = TaskLedger(str(tmp_path / "tasks"))
        plugin._vault = MagicMock()
        plugin._presence = MagicMock()
        plugin._route_message = AsyncMock(return_value=[])
        plugin._identity = AgentRuntime(
            name="coder",
            identity="lapis",
            agent_id="lapis-id",
            state="idle",
        )

        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock
        llm_mock.conversation_history = []
        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        bus.emit_with_hooks = AsyncMock()
        plugin.event_bus = bus

        summary, *ordinary = _make_summary_batch(n_msgs=2, n_total=30)
        reminder = HubMessage(
            action="message",
            from_agent="task-cron-id",
            from_identity="task-cron",
            to="lapis",
            content="[task reminder: missing-card] stale replay task",
            scope=MessageScope.DIRECT.value,
            metadata={
                "task_cron": True,
                "task_id": "missing-card",
                "source_identity": "koordinator",
            },
        )

        self._run(plugin._deliver_inbox_batch([summary, reminder, *ordinary]))

        ack = plugin._route_message.await_args.args[0]
        assert ack.to == "koordinator"
        assert ack.reply_to == reminder.id
        assert ack.metadata["task_cron_ack"] is True
        assert ack.metadata["task_id"] == "missing-card"
        assert ack.metadata["disposition"] == "stale"
        assert ack.metadata["reason"] == "task card not found"
        bus.emit_with_hooks.assert_not_awaited()
        assert llm_mock.conversation_history == []

        inject_mock.assert_awaited_once()
        block = inject_mock.await_args.args[0]
        assert "stale replay task" not in block
        assert "msg 0" in block
        assert "msg 1" in block

    def test_replay_task_assignment_uses_normal_receive_once(self) -> None:
        """Retained task directives are classified, never flattened into prose."""
        plugin = _make_plugin_stub()
        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock
        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        received: List[HubMessage] = []

        async def capture_receive(message: HubMessage) -> None:
            received.append(message)

        plugin._on_message_received = capture_receive
        summary, *ordinary = _make_summary_batch(n_msgs=2, n_total=30)
        assignment = HubMessage(
            action="message",
            from_identity="koordinator",
            to="lapis",
            content="objective: execute the retained directive",
            metadata={"task_assignment": True, "task_id": "task-retained"},
        )

        self._run(
            plugin._deliver_inbox_batch(
                [summary, ordinary[0], assignment, ordinary[1]]
            )
        )

        assert received == [assignment]
        inject_mock.assert_awaited_once()
        block = inject_mock.await_args.args[0]
        assert "retained directive" not in block
        assert "msg 0" in block
        assert "msg 1" in block

    def test_replay_metadata_task_cron_uses_normal_receive_once(self) -> None:
        """Anything preserved as task-cron metadata is classified, not flattened."""
        plugin = _make_plugin_stub()
        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock
        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        received: List[HubMessage] = []

        async def capture_receive(message: HubMessage) -> None:
            received.append(message)

        plugin._on_message_received = capture_receive
        summary, *ordinary = _make_summary_batch(n_msgs=2, n_total=30)
        reminder = HubMessage(
            action="message",
            from_identity="koordinator",
            to="lapis",
            content="[task reminder: metadata-only] classify me",
            metadata={"task_cron": True, "task_id": "metadata-only"},
        )

        self._run(
            plugin._deliver_inbox_batch(
                [summary, ordinary[0], reminder, ordinary[1]]
            )
        )

        assert received == [reminder]
        inject_mock.assert_awaited_once()
        assert "classify me" not in inject_mock.await_args.args[0]


    def test_replay_active_task_cron_uses_receive_path_and_is_excluded(
        self, tmp_path: Path
    ) -> None:
        """An active task reminder keeps receive semantics outside coalescing."""
        plugin = _make_plugin_stub()
        ledger = TaskLedger(str(tmp_path / "tasks"))
        card = ledger.create(
            assigner="koordinator",
            assignee="lapis",
            directive="continue active replay task",
            report_to="koordinator",
        )
        plugin._task_ledger = ledger

        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock
        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        received: List[HubMessage] = []

        async def capture_receive(message: HubMessage) -> None:
            received.append(message)

        plugin._on_message_received = capture_receive
        summary, *ordinary = _make_summary_batch(n_msgs=2, n_total=30)
        reminder = HubMessage(
            action="message",
            from_identity="task-cron",
            to="lapis",
            content=f"[task reminder: {card.id}] continue active replay task",
            scope=MessageScope.DIRECT.value,
            metadata={"task_cron": True, "task_id": card.id},
        )

        self._run(
            plugin._deliver_inbox_batch([summary, ordinary[0], reminder, ordinary[1]])
        )

        assert received == [reminder]
        inject_mock.assert_awaited_once()
        block = inject_mock.await_args.args[0]
        assert "continue active replay task" not in block
        assert "msg 0" in block
        assert "msg 1" in block

    def test_old_stale_task_cron_is_acknowledged_without_wake(
        self, tmp_path: Path
    ) -> None:
        """A control older than the ordinary cap still receives a correlated ACK."""
        inbox = tmp_path / "mailbox" / "lapis"
        inbox.mkdir(parents=True)
        now = time.time()
        reminder = HubMessage(
            action="message",
            from_identity="task-cron",
            to="lapis",
            content="[task reminder: stale-bounded] classify me",
            metadata={
                "task_cron": True,
                "task_id": "stale-bounded",
                "source_identity": "koordinator",
            },
        )
        reminder_data = reminder.to_dict()
        reminder_data["timestamp"] = now - 100
        (inbox / "00000000.000000-x-from-task-cron.json").write_text(
            json.dumps(reminder_data)
        )
        for i in range(25):
            data = _make_msg(content=f"ordinary bounded {i}").to_dict()
            data["timestamp"] = now - (25 - i)
            (inbox / f"{i + 1:08d}.000000-x-from-peer.json").write_text(
                json.dumps(data)
            )

        with patch(
            "plugins.hub.messenger.get_messages_dir",
            return_value=tmp_path / "mailbox",
        ):
            batch = AgentMessenger.read_mailbox("lapis", max_replay=20)

        plugin = _make_plugin_stub()
        plugin._task_ledger = TaskLedger(str(tmp_path / "tasks"))
        plugin._vault = MagicMock()
        plugin._presence = MagicMock()
        plugin._route_message = AsyncMock(return_value=[])
        plugin._identity = AgentRuntime(
            name="coder",
            identity="lapis",
            agent_id="lapis-id",
            state="idle",
        )
        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock
        llm_mock.conversation_history = []
        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        bus.emit_with_hooks = AsyncMock()
        plugin.event_bus = bus

        self._run(plugin._deliver_inbox_batch(batch))

        ack = plugin._route_message.await_args.args[0]
        assert ack.reply_to == reminder.id
        assert ack.metadata["reminder_id"] == reminder.id
        assert ack.metadata["task_id"] == "stale-bounded"
        assert ack.metadata["disposition"] == "stale"
        bus.emit_with_hooks.assert_not_awaited()
        assert llm_mock.conversation_history == []
        inject_mock.assert_awaited_once()
        assert "classify me" not in inject_mock.await_args.args[0]

    def test_replay_task_cron_control_is_not_duplicated_in_fallback(self) -> None:
        """Fallback delivers controls once and only ordinary messages afterward."""
        plugin = _make_plugin_stub()
        bus = MagicMock()
        bus.get_service.return_value = None
        plugin.event_bus = bus

        received: List[HubMessage] = []

        async def capture_receive(message: HubMessage) -> None:
            received.append(message)

        plugin._on_message_received = capture_receive
        summary, *ordinary = _make_summary_batch(n_msgs=2, n_total=30)
        reminder = HubMessage(
            action="message",
            from_identity="task-cron",
            to="lapis",
            content="[task reminder: active] control fallback",
            scope=MessageScope.DIRECT.value,
            metadata={"task_cron": True, "task_id": "active"},
        )

        self._run(
            plugin._deliver_inbox_batch([summary, ordinary[0], reminder, ordinary[1]])
        )

        assert received == [reminder, ordinary[0], ordinary[1]]
        assert received.count(reminder) == 1

    def test_replay_with_only_task_cron_controls_does_not_inject_empty_block(
        self, tmp_path: Path
    ) -> None:
        """A control-only replay is handled without an empty HUD injection."""
        plugin = _make_plugin_stub()
        plugin._task_ledger = TaskLedger(str(tmp_path / "tasks"))
        plugin._vault = MagicMock()
        plugin._presence = MagicMock()
        plugin._route_message = AsyncMock(return_value=[])
        plugin._identity = AgentRuntime(
            name="coder",
            identity="lapis",
            agent_id="lapis-id",
            state="idle",
        )

        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock
        llm_mock.conversation_history = []
        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        bus.emit_with_hooks = AsyncMock()
        plugin.event_bus = bus

        summary = _make_summary_batch(n_msgs=1, n_total=30)[0]
        reminder = HubMessage(
            action="message",
            from_identity="task-cron",
            to="lapis",
            content="[task reminder: missing-only] stale replay task",
            scope=MessageScope.DIRECT.value,
            metadata={
                "task_cron": True,
                "task_id": "missing-only",
                "source_identity": "koordinator",
            },
        )

        self._run(plugin._deliver_inbox_batch([summary, reminder]))

        plugin._route_message.assert_awaited_once()
        inject_mock.assert_not_awaited()
        bus.emit_with_hooks.assert_not_awaited()
        assert llm_mock.conversation_history == []

    # ------------------------------------------------------------------
    # _deliver_inbox_batch: normal batch → individual delivery, NOT inject
    # ------------------------------------------------------------------

    def test_normal_batch_does_not_call_inject(self) -> None:
        """Without a summary header, inject_system_message is NOT called."""
        plugin = _make_plugin_stub()

        inject_mock = AsyncMock()
        llm_mock = MagicMock()
        llm_mock.inject_system_message = inject_mock

        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        received: List[HubMessage] = []

        async def fake_on_message(msg):
            received.append(msg)

        plugin._on_message_received = fake_on_message

        normal_msgs = [_make_msg(content=f"normal {i}") for i in range(3)]
        self._run(plugin._deliver_inbox_batch(normal_msgs))

        inject_mock.assert_not_awaited()
        assert len(received) == 3

    def test_normal_batch_calls_on_message_per_msg(self) -> None:
        """Without summary, each message goes through _on_message_received."""
        plugin = _make_plugin_stub()
        plugin.event_bus = None

        received: List[HubMessage] = []

        async def fake_on_message(msg):
            received.append(msg)

        plugin._on_message_received = fake_on_message

        normal_msgs = [_make_msg(content=f"m{i}") for i in range(4)]
        self._run(plugin._deliver_inbox_batch(normal_msgs))

        assert len(received) == 4
        assert [m.content for m in received] == [f"m{i}" for i in range(4)]

    # ------------------------------------------------------------------
    # _inject_inbox_replay: fallback when no inject_system_message
    # ------------------------------------------------------------------

    def test_fallback_to_on_message_when_no_llm_service(self) -> None:
        """Falls back to per-message delivery when event_bus has no llm_service."""
        plugin = _make_plugin_stub()

        bus = MagicMock()
        bus.get_service.return_value = None  # no llm_service
        plugin.event_bus = bus

        received: List[HubMessage] = []

        async def fake_on_message(msg):
            received.append(msg)

        plugin._on_message_received = fake_on_message

        batch = _make_summary_batch(n_msgs=4, n_total=20)
        self._run(plugin._deliver_inbox_batch(batch))

        # Summary is not delivered via _on_message_received, only real msgs
        assert len(received) == 4
        assert all(m.action == "message" for m in received)

    def test_fallback_to_on_message_when_no_inject_attr(self) -> None:
        """Falls back when llm_service exists but has no inject_system_message."""
        plugin = _make_plugin_stub()

        llm_mock = MagicMock(spec=[])  # no inject_system_message attribute
        bus = MagicMock()
        bus.get_service.return_value = llm_mock
        plugin.event_bus = bus

        received: List[HubMessage] = []

        async def fake_on_message(msg):
            received.append(msg)

        plugin._on_message_received = fake_on_message

        batch = _make_summary_batch(n_msgs=3, n_total=15)
        self._run(plugin._deliver_inbox_batch(batch))

        assert len(received) == 3
