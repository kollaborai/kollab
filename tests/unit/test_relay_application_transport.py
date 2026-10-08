"""Application transport contracts using real endpoint crypto and an opaque wire.

No network service or model is started. The bridge's model/tool authorization and
real cross-host flow are separate integration acceptance gates.
"""

import asyncio
import base64
import hashlib
import json
import secrets
import time

import pytest
import pytest_asyncio
from nacl.public import Box
from nacl.signing import VerifyKey

from plugins.hub import relay_client as transport
from plugins.hub.relay_client import RelayClient, RelayError


class Wire:
    def __init__(self):
        self.clients = {}
        self.queues = {}
        self.tasks = []
        self.sent = []
        self.drop = False

    def add(self, client):
        queue = asyncio.Queue()
        self.clients[client.public_key] = client
        self.queues[client.public_key] = queue
        client._session_id = secrets.token_hex(16)
        client._state = "online"
        client._closed = False
        client.state.origin = "https://relay.example"
        wire = self

        class Socket:
            closed = False

            async def send_str(self, raw):
                frame = json.loads(raw)
                wire.sent.append((client.public_key, frame))
                if not wire.drop:
                    wire.queues[frame["to"]].put_nowait(wire.routed(client, frame))

        client._ws = Socket()

        async def receive():
            while True:
                await client._handle_frame(await queue.get())

        self.tasks.append(asyncio.create_task(receive()))

    @staticmethod
    def routed(sender, frame):
        return {
            "type": "message",
            "from": sender.public_key,
            "session": sender._session_id,
            "id": frame["id"],
            "ciphertext": frame["ciphertext"],
        }

    async def close(self):
        for client in self.clients.values():
            await client.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


@pytest_asyncio.fixture
async def pair(tmp_path):
    wire = Wire()
    left = RelayClient(tmp_path / "left", state_dir=tmp_path / "state-left")
    right = RelayClient(tmp_path / "right", state_dir=tmp_path / "state-right")
    right.state.room = left.state.room
    for client in (left, right):
        wire.add(client)
    left.approve(right.public_key)
    right.approve(left.public_key)
    left._peers[right.public_key] = right._session_id
    right._peers[left.public_key] = left._session_id
    yield left, right, wire
    await wire.close()


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def encrypted(sender, recipient, kind, payload, **overrides):
    now = int(time.time())
    body = {
        "v": 1,
        "from": sender.public_key,
        "to": recipient.public_key,
        "from_session": sender._session_id,
        "to_session": recipient._session_id,
        "room": hashlib.sha256(bytes.fromhex(sender.state.room)).hexdigest(),
        "id": secrets.token_hex(16),
        "sent_at": now,
        "expires_at": now + 30,
        "kind": kind,
        "payload": payload,
    }
    body.update(overrides)
    box = Box(
        sender._store.key.to_curve25519_private_key(),
        VerifyKey(bytes.fromhex(recipient.public_key)).to_curve25519_public_key(),
    )
    return {
        "type": "message",
        "from": sender.public_key,
        "session": sender._session_id,
        "id": body["id"],
        "ciphertext": base64.b64encode(box.encrypt(json.dumps(body).encode())).decode(),
    }


@pytest.mark.asyncio
async def test_real_crypto_request_response_and_ping_regression(pair):
    left, right, wire = pair
    received = []

    async def handle(peer, method, payload):
        received.append((peer, method, payload))
        return {"accepted": True, "id": payload["id"]}

    right.set_request_handler(handle)
    result = await left.request(right.public_key, "message", {"id": "task-1", "text": "private message"})
    assert result == {"accepted": True, "id": "task-1"}
    assert received == [(left.public_key, "message", {"id": "task-1", "text": "private message"})]
    assert "private message" not in json.dumps(wire.sent)
    pong = await left.ping(right.public_key)
    assert pong["workspace_id"] == right.state.workspace_id
    assert not left._application_pending


@pytest.mark.asyncio
async def test_bidirectional_nested_request_does_not_block_receive_loop(pair):
    left, right, _ = pair

    async def left_handler(peer, method, payload):
        return {"agents": ["local-agent"]}

    async def right_handler(peer, method, payload):
        return await right.request(peer, "directory", {})

    left.set_request_handler(left_handler)
    right.set_request_handler(right_handler)
    assert await left.request(right.public_key, "message", {}) == {"agents": ["local-agent"]}


@pytest.mark.asyncio
async def test_no_handler_fixed_failure_and_no_unapproved_callback(pair):
    left, right, _ = pair
    with pytest.raises(RelayError, match="not_supported"):
        await left.request(right.public_key, "directory", {})
    called = []

    async def handle(*args):
        called.append(args)
        return {}

    right.set_request_handler(handle)
    right.revoke(left.public_key)
    frame = encrypted(left, right, "request", {"method": "message", "arguments": {}, "timeout_ms": 1000})
    await right._handle_frame(frame)
    assert not called
    assert right.status()["counters"]["rejected_messages"] == 1
    left.revoke(right.public_key)
    with pytest.raises(RelayError, match="approval"):
        await left.request(right.public_key, "message", {})


@pytest.mark.asyncio
async def test_a_newer_devices_method_or_kind_never_breaks_this_one(pair, monkeypatch):
    # Versioning: a newer device may call a method or send a kind this version
    # does not know, with fields it does not know. The method is answered
    # not_supported at once; the kind is ignored; neither counts as rejected.
    left, right, _ = pair
    right.set_request_handler(lambda *args: None)
    answered = []

    async def respond(*args, **kwargs):
        answered.append((args[4], kwargs))

    monkeypatch.setattr(right, "_application_response", respond)
    request = {"method": "newer.method", "arguments": {"x": 1}, "timeout_ms": 1000, "newer": True}
    await right._handle_frame(encrypted(left, right, "request", request))
    await right._handle_frame(encrypted(left, right, "newer_kind", {}))
    assert answered == [("newer.method", {"error": "not_supported"})]
    counters = right.status()["counters"]
    assert counters["ignored_messages"] == 1 and counters.get("rejected_messages", 0) == 0


@pytest.mark.asyncio
async def test_only_correlated_authenticated_response_succeeds(pair):
    left, right, wire = pair
    wire.drop = True
    task = asyncio.create_task(left.request(right.public_key, "message", {}))
    await until(lambda: left._application_pending)
    request_id = next(iter(left._application_pending))
    response = {"reply_to": request_id, "method": "directory", "result": {"accepted": True}, "error": ""}
    await left._handle_frame(encrypted(right, left, "response", response))
    await left._handle_frame(
        encrypted(
            right,
            left,
            "pong",
            {
                "reply_to": request_id,
                "label": "right",
                "workspace_id": right.state.workspace_id,
            },
        )
    )
    await asyncio.sleep(0)
    assert not task.done()
    response["method"] = "message"
    await left._handle_frame(encrypted(right, left, "response", response))
    assert await task == {"accepted": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["room", "from_session", "to_session", "to", "from"])
async def test_wrong_binding_never_dispatches(pair, binding):
    left, right, _ = pair
    called = []

    async def handle(*args):
        called.append(args)
        return {}

    right.set_request_handler(handle)
    frame = encrypted(
        left,
        right,
        "request",
        {
            "method": "message",
            "arguments": {},
            "timeout_ms": 1000,
        },
        **{binding: "0" * 64},
    )
    await right._handle_frame(frame)
    assert not called
    assert not right._dispatch
    assert right.status()["counters"]["rejected_messages"] == 1


@pytest.mark.asyncio
async def test_replayed_request_executes_once_and_tampered_ciphertext_never_executes(pair):
    left, right, _ = pair
    called = []

    async def handle(*args):
        called.append(args)
        return {}

    right.set_request_handler(handle)
    frame = encrypted(left, right, "request", {"method": "message", "arguments": {}, "timeout_ms": 1000})
    await right._handle_frame(frame)
    await right._handle_frame(frame)
    await until(lambda: called)
    assert len(called) == 1
    raw = bytearray(base64.b64decode(frame["ciphertext"]))
    raw[-1] ^= 1
    frame["ciphertext"] = base64.b64encode(raw).decode()
    await right._handle_frame(frame)
    assert len(called) == 1
    assert right.status()["counters"]["rejected_messages"] == 2


@pytest.mark.asyncio
async def test_handler_exception_never_leaks_message(pair):
    left, right, _ = pair

    async def handle(*args):
        raise ValueError("secret /private/key was exposed")

    right.set_request_handler(handle)
    with pytest.raises(RelayError) as caught:
        await left.request(right.public_key, "message", {})
    assert str(caught.value) == "peer application request failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["revoke", "disconnect", "session_change", "handler_reset"])
async def test_inflight_handler_cancelled_on_authority_or_connection_change(pair, action):
    left, right, _ = pair
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def handle(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    right.set_request_handler(handle)
    request = asyncio.create_task(left.request(right.public_key, "message", {}))
    await entered.wait()
    if action == "revoke":
        right.revoke(left.public_key)
    elif action == "disconnect":
        await right.close()
    elif action == "session_change":
        right._set_peers({"type": "peers", "peers": [{"key": left.public_key, "session": "a" * 32}]})
    else:
        right.set_request_handler(None)
    await asyncio.wait_for(cancelled.wait(), 1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await until(lambda: not right._dispatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["revoke", "disconnect", "session_change", "transport_error"])
async def test_pending_request_settled_on_authority_or_connection_change(pair, action):
    left, right, wire = pair
    wire.drop = True
    request = asyncio.create_task(left.request(right.public_key, "message", {}))
    await until(lambda: left._application_pending)
    request_id = next(iter(left._application_pending))
    if action == "revoke":
        left.revoke(right.public_key)
    elif action == "disconnect":
        await left.close()
    elif action == "session_change":
        left._set_peers({"type": "peers", "peers": [{"key": right.public_key, "session": "a" * 32}]})
    else:
        await left._handle_frame({"type": "error", "code": "peer_offline", "id": request_id})
    with pytest.raises(RelayError):
        await request
    assert not left._application_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["timeout", "caller_cancel"])
async def test_request_timeout_and_caller_cancellation_cancel_remote_handler(pair, action):
    left, right, _ = pair
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def handle(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    right.set_request_handler(handle)
    request = asyncio.create_task(
        left.request(right.public_key, "message", {}, timeout=0.05 if action == "timeout" else 10)
    )
    await entered.wait()
    if action == "caller_cancel":
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    else:
        with pytest.raises(RelayError, match="deadline"):
            await request
    await asyncio.wait_for(cancelled.wait(), 1)
    await until(lambda: not right._dispatch)
    assert not left._application_pending


@pytest.mark.asyncio
async def test_receiver_deadline_independent_of_requester(pair):
    left, right, _ = pair
    cancelled = asyncio.Event()

    async def handle(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    right.set_request_handler(handle)
    await right._handle_frame(
        encrypted(
            left,
            right,
            "request",
            {
                "method": "message",
                "arguments": {},
                "timeout_ms": 10,
            },
        )
    )
    await asyncio.wait_for(cancelled.wait(), 1)
    await until(lambda: not right._dispatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["peer", "global"])
async def test_pending_and_dispatch_capacity_are_bounded(pair, monkeypatch, scope):
    left, right, _ = pair
    entered = asyncio.Event()

    async def handle(*args):
        entered.set()
        await asyncio.Event().wait()

    right.set_request_handler(handle)
    pending_limit = "MAX_PEER_PENDING" if scope == "peer" else "MAX_PENDING"
    dispatch_limit = "MAX_PEER_DISPATCH" if scope == "peer" else "MAX_DISPATCH"
    monkeypatch.setattr(transport, pending_limit, 1)
    request = asyncio.create_task(left.request(right.public_key, "message", {}))
    await entered.wait()
    with pytest.raises(RelayError, match="capacity"):
        await left.request(right.public_key, "message", {})
    monkeypatch.setattr(transport, pending_limit, 4)
    monkeypatch.setattr(transport, dispatch_limit, 1)
    with pytest.raises(RelayError, match="busy"):
        await left.request(right.public_key, "message", {})
    assert len(right._dispatch) == 1
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{"text": "x" * transport.MAX_APPLICATION_PAYLOAD}, {"x": 1.5}, {"x": -1}, ["not object"]]
)
async def test_payload_bounds_before_send(pair, payload):
    left, right, wire = pair
    with pytest.raises(RelayError):
        await left.request(right.public_key, "message", payload)
    assert not wire.sent
    assert not left._application_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, 301, float("inf"), float("nan"), True, "10"])
async def test_deadline_bounds_before_send(pair, timeout):
    left, right, wire = pair
    with pytest.raises(RelayError, match="timeout"):
        await left.request(right.public_key, "message", {}, timeout=timeout)
    assert not wire.sent


@pytest.mark.asyncio
async def test_status_method_and_unknown_method_rejected(pair):
    left, right, _ = pair

    async def handle(peer, method, payload):
        return {"method": method, "state": "running"}

    right.set_request_handler(handle)
    assert (await left.request(right.public_key, "status", {}))["state"] == "running"
    with pytest.raises(RelayError, match="unsupported"):
        await left.request(right.public_key, "shell", {})


@pytest.mark.asyncio
async def test_application_response_cannot_complete_ping_and_relay_ack_cannot_complete_request(pair):
    left, right, wire = pair
    wire.drop = True
    ping = asyncio.create_task(left.ping(right.public_key))
    await until(lambda: left._pending)
    ping_id = next(iter(left._pending))
    await left._handle_frame(
        encrypted(
            right,
            left,
            "response",
            {
                "reply_to": ping_id,
                "method": "message",
                "result": {},
                "error": "",
            },
        )
    )
    assert not ping.done()
    request = asyncio.create_task(left.request(right.public_key, "message", {}))
    await until(lambda: left._application_pending)
    request_id = next(iter(left._application_pending))
    # A frame this version does not know is ignored: it completes nothing.
    await left._handle_frame({"type": "ack", "id": request_id})
    assert left._counts["ignored_frames"] == 1
    assert not request.done()
    for task in (ping, request):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_handler_cancel_is_method_bound_and_running_id_cannot_be_replaced(pair):
    left, right, _ = pair
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def handle(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    right.set_request_handler(handle)
    request_id = secrets.token_hex(16)
    frame = encrypted(
        left,
        right,
        "request",
        {
            "method": "message",
            "arguments": {},
            "timeout_ms": 10000,
        },
        id=request_id,
    )
    await right._handle_frame(frame)
    await entered.wait()
    await right._handle_frame(
        encrypted(
            left,
            right,
            "request_cancel",
            {
                "reply_to": request_id,
                "method": "directory",
            },
        )
    )
    await asyncio.sleep(0)
    assert not cancelled.is_set()
    original = next(iter(right._dispatch.values()))
    right._replay.clear()  # Simulate expiry of the short envelope replay window.
    await right._handle_frame(frame)
    assert list(right._dispatch.values()) == [original]
    assert right.status()["counters"]["rejected_messages"] == 1
    await right._handle_frame(
        encrypted(
            left,
            right,
            "request_cancel",
            {
                "reply_to": request_id,
                "method": "message",
            },
        )
    )
    await asyncio.wait_for(cancelled.wait(), 1)


@pytest.mark.asyncio
async def test_revocation_stops_dispatch_even_if_state_persistence_fails(pair, monkeypatch):
    left, right, _ = pair
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def handle(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    def failed_save():
        raise OSError("private path")

    right.set_request_handler(handle)
    request = asyncio.create_task(left.request(right.public_key, "message", {}))
    await entered.wait()
    monkeypatch.setattr(right._store, "save", failed_save)
    with pytest.raises(OSError):
        right.revoke(left.public_key)
    await asyncio.wait_for(cancelled.wait(), 1)
    assert left.public_key not in right.state.approvals
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{"text": "x" * transport.MAX_APPLICATION_PAYLOAD}, ["wrong type"], {"value": 0.1}])
async def test_invalid_handler_result_has_fixed_error(pair, result):
    left, right, _ = pair

    async def handle(*args):
        return result

    right.set_request_handler(handle)
    with pytest.raises(RelayError, match="peer application request failed"):
        await left.request(right.public_key, "message", {})
