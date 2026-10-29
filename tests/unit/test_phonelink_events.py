"""PhoneLink lifecycle event redaction and diagnostics contracts."""

import hashlib
import json
import logging

from kollabor_engine.phonelink_events import (
    PhoneLinkEventLog,
    normalize_phone_diagnostic,
)


def test_event_log_redacts_identifiers_actor_secrets_and_unknown_fields(caplog):
    log = PhoneLinkEventLog()
    phone_id = "private-phone-identifier"
    host_key = "a" * 64
    invite_url = "https://relay.example/relay/v1/phone/?key=private-invite"
    session = "private-web-session"
    pairing_code = "123456"
    actor = {
        "route": "/phonelink/phones/{identifier}/approve",
        "method": "POST",
        "client_kind": "webui",
        "remote_addr": "127.0.0.1",
        "user_agent": "Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36 private-agent-data",
        "web_session": session,
        "confirmation_shown": True,
    }

    with caplog.at_level(logging.INFO, logger="kollabor_engine.phonelink_events"):
        record = log.emit(
            "approved",
            identifier=phone_id,
            peer=host_key,
            actor=actor,
            cause=invite_url,
            invite_url=invite_url,
            host_key=host_key,
            pairing_code=pairing_code,
            device_name=invite_url,
            network=host_key,
        )

    output = json.dumps(log.snapshot()) + caplog.text
    for secret in (
        phone_id,
        host_key,
        invite_url,
        session,
        pairing_code,
        "private-agent-data",
    ):
        assert secret not in output
    assert record["phone_ref"] == hashlib.sha256(phone_id.encode()).hexdigest()[:12]
    assert record["device_ref"] == hashlib.sha256(host_key.encode()).hexdigest()[:12]
    assert record["cause"] == "unknown"
    assert record["device_name"] == "redacted"
    assert record["network"] == "redacted"
    assert record["actor"]["user_agent"] == "Chrome/124"
    assert (
        record["actor"]["web_session_ref"]
        == hashlib.sha256(session.encode()).hexdigest()[:12]
    )
    assert record["actor"]["confirmation_shown"] is True
    assert (
        "invite_url" not in record
        and "host_key" not in record
        and "pairing_code" not in record
    )


def test_event_log_preserves_safe_device_and_network_labels():
    record = PhoneLinkEventLog().emit(
        "approved",
        identifier="phone-id",
        device_name="My iPhone",
        network="mine via relay.example",
    )

    assert record["device_name"] == "My iPhone"
    assert record["network"] == "mine via relay.example"

    for secret in (
        "123456",
        "My iPhone 123456",
        "iPhone123456",
        "My device token abcd",
        f"phone {'a' * 64}",
    ):
        redacted = PhoneLinkEventLog().emit("approved", device_name=secret)
        assert redacted["device_name"] == "redacted"


def test_event_log_history_is_bounded_and_close_codes_are_validated():
    log = PhoneLinkEventLog(max_events=2)
    log.emit("relay_socket_closed", close_code=1000)
    log.emit("approved", identifier="second")
    log.emit("relay_socket_closed", close_code=999)

    history = log.snapshot()

    assert [event["id"] for event in history] == [2, 3]
    assert "close_code" not in history[-1]
    assert history[-1]["event"] == "relay_socket_closed"
    assert all(
        {"phone_ref", "device_name", "network"}.issubset(event) for event in history
    )
    assert history[-1]["phone_ref"] == "unknown"
    assert history[-1]["device_name"] == "unknown"
    assert history[-1]["network"] == "unknown"

    sanitized = PhoneLinkEventLog().emit(
        "relay_socket_closed",
        close_code=1000,
        close_reason="normal",
        reason_text="private URL and key",
    )
    assert sanitized["close_reason"] == "normal"
    assert "reason_text" not in sanitized


def test_phone_diagnostic_payload_allowlists_visibility_and_retry_metadata():
    assert normalize_phone_diagnostic({"event": "visibility_visible"}) == {
        "event": "visibility_visible"
    }
    assert normalize_phone_diagnostic(
        {
            "event": "relay_socket_closed",
            "close_code": 1001,
            "close_reason": "going_away",
        }
    ) == {
        "event": "relay_socket_closed",
        "close_code": 1001,
        "close_reason": "going_away",
    }
    assert normalize_phone_diagnostic(
        {"event": "relay_reconnecting", "retry_count": 3}
    ) == {"event": "relay_reconnecting", "retry_count": 3}
    assert normalize_phone_diagnostic(
        {"event": "relay_socket_closed", "close_code": 1000}
    ) == {
        "event": "relay_socket_closed",
        "close_code": 1000,
        "close_reason": "normal",
    }
    assert (
        normalize_phone_diagnostic(
            {
                "event": "relay_socket_closed",
                "close_code": 1000,
                "close_reason": "going_away",
            }
        )
        is None
    )

    assert normalize_phone_diagnostic({"event": "unknown_event"}) is None
    assert normalize_phone_diagnostic({"event": "offline", "url": "secret"}) is None
    assert (
        normalize_phone_diagnostic({"event": "relay_socket_closed", "close_code": 999})
        is None
    )
    assert (
        normalize_phone_diagnostic(
            {
                "event": "relay_socket_closed",
                "close_code": 1000,
                "close_reason": "private https://relay/?token=secret",
            }
        )
        is None
    )
    assert (
        normalize_phone_diagnostic({"event": "relay_reconnecting", "retry_count": True})
        is None
    )
