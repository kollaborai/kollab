import json

from plugins.hub.delivery import DeliveryTrace


def test_trace_appends_compact_jsonl(tmp_path):
    trace = DeliveryTrace(tmp_path / "delivery_trace.jsonl")

    trace.record(
        message_id="msg-1",
        event="created",
        sender="lapis",
        target="koordinator",
        detail="manual task assignment",
    )
    trace.record(
        message_id="msg-1",
        event="queued_identity_mailbox",
        sender="lapis",
        target="koordinator",
        detail="offline direct target",
    )

    lines = (tmp_path / "delivery_trace.jsonl").read_text().splitlines()
    payloads = [json.loads(line) for line in lines]

    assert [p["event"] for p in payloads] == [
        "created",
        "queued_identity_mailbox",
    ]
    assert payloads[0]["message_id"] == "msg-1"
    assert payloads[1]["target"] == "koordinator"
