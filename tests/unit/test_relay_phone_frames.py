import json

import pytest

from plugins.hub.relay_client import (
    MAX_APPLICATION_PAYLOAD,
    MAX_CIPHERTEXT,
    MAX_FRAME,
    MAX_PHONE_APPLICATION_PAYLOAD,
    RelayClient,
    RelayError,
    _application_payload_limit,
)
from plugins.hub.relay_service import MAX_CIPHERTEXT_CHARS, MAX_FRAME_BYTES


def test_phone_methods_get_a_bounded_large_payload_while_regular_methods_stay_small():
    assert _application_payload_limit("phone_http") == MAX_PHONE_APPLICATION_PAYLOAD
    assert _application_payload_limit("phone_session") == MAX_PHONE_APPLICATION_PAYLOAD
    assert _application_payload_limit("message") == MAX_APPLICATION_PAYLOAD

    overhead = len(json.dumps({"packet": ""}, separators=(",", ":")).encode())
    payload = {"packet": "x" * (MAX_PHONE_APPLICATION_PAYLOAD - overhead)}
    assert RelayClient._application_payload(
        payload,
        limit=_application_payload_limit("phone_http"),
    ) == payload
    with pytest.raises(RelayError, match="oversized application payload"):
        RelayClient._application_payload(payload)


def test_phone_payload_and_ciphertext_limits_remain_finite_and_aligned():
    assert MAX_PHONE_APPLICATION_PAYLOAD < MAX_CIPHERTEXT
    assert MAX_CIPHERTEXT <= MAX_CIPHERTEXT_CHARS
    assert MAX_FRAME <= MAX_FRAME_BYTES
    with pytest.raises(RelayError, match="oversized application payload"):
        RelayClient._application_payload(
            {"packet": "x" * MAX_PHONE_APPLICATION_PAYLOAD},
            limit=_application_payload_limit("phone_http"),
        )
