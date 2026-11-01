"""The KOLLAB_RELAY_DEBUG gate for relay room-member lifecycle tracing."""

import logging

from plugins.hub import relay_service


def test_debug_member_event_is_gated_on_kollab_relay_debug(
    monkeypatch, caplog
):
    # Off by default: the tracing call must be free (no log record at all)
    # unless the operator exported KOLLAB_RELAY_DEBUG=1.
    monkeypatch.delenv("KOLLAB_RELAY_DEBUG", raising=False)
    with caplog.at_level(logging.DEBUG, logger=relay_service._LOGGER.name):
        relay_service._debug_member_event("row_removed", "k", "r", "reason")
    assert caplog.records == []

    monkeypatch.setenv("KOLLAB_RELAY_DEBUG", "1")
    with caplog.at_level(logging.DEBUG, logger=relay_service._LOGGER.name):
        relay_service._debug_member_event("row_removed", "k", "r", "reason")
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "row_removed" in message and "key=k" in message and "room=r" in message
