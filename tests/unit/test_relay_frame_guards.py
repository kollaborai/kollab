"""Non-object relay frames are counted and dropped, never reconnecting."""

import pytest

from plugins.hub.relay_client import RelayClient, RelayConnectionEvent
from plugins.hub.relay_state import (
    RelayError,
    RelayFrameShapeError,
    strict_json,
)


@pytest.mark.asyncio
async def test_non_object_frame_is_counted_and_keeps_the_link(tmp_path):
    # _handle_frame defends its own boundary: a caller handing it a list
    # (or any non-dict) counts as one bad frame, not a transport failure.
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    client._state = "online"
    client._session_id = "a" * 32

    await client._handle_frame([1, 2, 3])

    assert client._counts["peer_state_frame_errors"] == 1
    assert client._state == "online"
    assert client._session_id == "a" * 32


def test_strict_json_names_non_objects_as_frame_shape_errors():
    with pytest.raises(RelayFrameShapeError):
        strict_json("[1, 2]")


def test_garbage_wire_json_stays_a_plain_relay_error():
    # The subclass exists so the receive loop can tell "valid text, wrong
    # shape" (healthy socket: keep it up) from "undecodable garbage"
    # (transport failure: reconnect). This pins that distinction.
    with pytest.raises(RelayError) as caught:
        strict_json("not json at all")
    assert type(caught.value) is RelayError


def test_connection_listener_failure_does_not_escape_the_notifier(tmp_path):
    # A crashing connection listener must neither break later listeners
    # nor leak its exception into whatever raised the notification.
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    seen = []
    client.add_connection_listener(lambda event: 1 / 0)
    client.add_connection_listener(seen.append)

    event = RelayConnectionEvent(kind="socket_opened")
    client._notify_connection_listeners(event)

    assert seen == [event]
