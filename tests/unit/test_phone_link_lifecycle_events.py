"""PhoneLink lifecycle events, each driven through the code that emits it (#366)."""

from types import SimpleNamespace

import pytest
from kollabor_engine import phone_link
from kollabor_engine.phonelink_events import opaque_ref

from tests.unit.test_phone_link import setup_link  # noqa: F401


@pytest.mark.asyncio
async def test_invite_opened_is_recorded(setup_link):  # noqa: F811
    manager, offer, _host, phone, *_ = setup_link
    await manager.status()
    history = await manager.event_history()
    event = next((e for e in history["events"] if e["event"] == "invite_opened"), None)
    assert event is not None, history["events"]
    assert event["record_state"] == "pending"
    assert event["phone_ref"] == opaque_ref(offer["id"])
    assert event["device_ref"] == opaque_ref(phone.public_key)


@pytest.mark.asyncio
async def test_relay_error_is_recorded(setup_link):  # noqa: F811
    manager, *_ = setup_link

    async def unreachable(_origin):
        raise OSError("relay unreachable")

    manager.directory_ready = unreachable
    await manager.status()
    history = await manager.event_history()
    event = next((e for e in history["events"] if e["event"] == "relay_error"), None)
    assert event is not None, history["events"]
    assert event["error_kind"] == "socket"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, event_name, fields",
    [
        ("socket_opened", "relay_socket_opened", {}),
        ("socket_closed", "relay_socket_closed", {"close_code": 1006}),
        ("reconnecting", "relay_reconnecting", {"retry_count": 2, "retry_seconds": 4}),
        ("error", "relay_error", {"error_kind": "socket"}),
    ],
)
async def test_relay_lifecycle_events_are_recorded(
    setup_link, monkeypatch, kind, event_name, fields  # noqa: F811
):
    manager, *_ = setup_link
    identifier = next(iter(manager.records))

    class OpeningClient:
        def __init__(self, *args, **kwargs):
            self.listeners = []

        def approve(self, key):
            pass

        def set_request_handler(self, handler):
            pass

        def add_peer_session_listener(self, listener):
            pass

        def add_connection_listener(self, listener):
            self.listeners.append(listener)

        async def connect(self, *args, **kwargs):
            for listener in self.listeners:
                listener(SimpleNamespace(kind=kind, **fields))

        async def close(self):
            pass

    # The fixture replaces _client; the real one attaches the lifecycle listener.
    monkeypatch.setattr(phone_link, "RelayClient", OpeningClient)
    manager.clients.pop(identifier)
    await phone_link.PhoneLink._client(manager, identifier)
    history = await manager.event_history()
    event = next((e for e in history["events"] if e["event"] == event_name), None)
    assert event is not None, history["events"]
    assert event["phone_ref"] == opaque_ref(identifier)
    assert event["device_name"] == "My iPhone"
    for name, value in fields.items():
        assert event[name] == value
