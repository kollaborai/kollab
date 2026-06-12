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


def test_trace_summary_counts_decisions_and_caps_recent(tmp_path):
    trace = DeliveryTrace(tmp_path / "delivery_trace.jsonl")

    for index, event in enumerate(
        [
            "route_started",
            "socket_send_succeeded",
            "quarantined",
            "rejected",
            "queued_identity_mailbox",
        ],
        start=1,
    ):
        trace.record(
            message_id=f"msg-{index}",
            event=event,
            sender="lapis",
            target="koordinator",
            detail=f"detail-{index}",
        )

    summary = trace.summary(recent_limit=3)

    assert summary["total"] == 5
    assert summary["counts"]["quarantined"] == 1
    assert summary["counts"]["rejected"] == 1
    assert summary["counts"]["queued_identity_mailbox"] == 1
    assert [item["event"] for item in summary["recent"]] == [
        "quarantined",
        "rejected",
        "queued_identity_mailbox",
    ]
