"""Source retirement and receiver claims must share one ownership boundary (#935)."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

from plugins.hub import messenger
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


def test_reader_cannot_delete_evidence_after_receiver_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(messenger, "get_messages_dir", lambda: tmp_path)
    message = HubMessage(action="message", to="receiver", content="one owner")
    asyncio.run(AgentMessenger.send_to_file("receiver", message))
    stale = AgentMessenger.read_mailboxes(["receiver"], consume=False)
    scanned = threading.Event()
    release = threading.Event()
    delivery_started = threading.Event()
    validate = messenger._validated_mailbox_timestamp

    def pause_reader(data):
        if threading.current_thread().name.startswith("source-reader"):
            scanned.set()
            assert release.wait(5)
        return validate(data)

    monkeypatch.setattr(messenger, "_validated_mailbox_timestamp", pause_reader)
    effects = []
    receiver = HubPlugin.__new__(HubPlugin)
    receiver._identity = SimpleNamespace(agent_id="session", identity="receiver")

    async def receive(msg):
        effects.append(msg.id)
        raise asyncio.CancelledError

    receiver._on_message_received = receive

    def deliver():
        delivery_started.set()
        try:
            return asyncio.run(receiver._deliver_inbox_batch(stale))
        except asyncio.CancelledError:
            return set()

    with ThreadPoolExecutor(1, thread_name_prefix="source-reader") as readers:
        with ThreadPoolExecutor(1) as deliveries:
            source_read = readers.submit(AgentMessenger.read_mailbox, "receiver")
            try:
                assert scanned.wait(5)
                delivery = deliveries.submit(deliver)
                assert delivery_started.wait(5)
                try:
                    delivery.result(timeout=0.5)
                except TimeoutError:
                    pass  # the reader owns the source until its consume completes
            finally:
                release.set()
            assert [msg.id for msg in source_read.result(timeout=5)] == [message.id]
            assert delivery.result(timeout=5) == set()

    # The reader consumed the source, so its stale competitor has no capability
    # to invoke a receiver whose evidence the reader would subsequently delete.
    assert effects == []
