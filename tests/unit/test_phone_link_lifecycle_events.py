"""PhoneLink lifecycle events that no other test names (#366)."""

from types import SimpleNamespace

import pytest
from kollabor_engine import phone_link

from tests.unit.test_phone_link import setup_link  # noqa: F401


@pytest.mark.asyncio
async def test_invite_opened_is_recorded(setup_link):  # noqa: F811
    manager, *_ = setup_link
    await manager.status()
    history = await manager.event_history()
    event = next((e for e in history["events"] if e["event"] == "invite_opened"), None)
    assert event is not None, history["events"]
    assert event["record_state"] == "pending"


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
async def test_relay_socket_opened_is_recorded(setup_link, monkeypatch):  # noqa: F811
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
                listener(SimpleNamespace(kind="socket_opened"))

        async def close(self):
            pass

    # The fixture replaces _client; the real one attaches the lifecycle listener.
    monkeypatch.setattr(phone_link, "RelayClient", OpeningClient)
    manager.clients.pop(identifier)
    await phone_link.PhoneLink._client(manager, identifier)
    history = await manager.event_history()
    event = next(
        (e for e in history["events"] if e["event"] == "relay_socket_opened"), None
    )
    assert event is not None, history["events"]
    assert event["device_name"] == "My iPhone"
