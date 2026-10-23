"""PhoneLink exercises real relay encryption and the engine's streaming boundary."""

import asyncio
import base64
import hashlib
import json
import secrets
import sys
import time

import pytest
import pytest_asyncio
import segno
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from kollabor_engine.phone_link import (
    CHUNK,
    PhoneLink,
    PhoneLinkError,
    decode_phone_frame,
    encode_phone_frame,
    pairing_code,
    session_keys,
)
from nacl.public import Box, PrivateKey, PublicKey
from nacl.secret import SecretBox

from plugins.hub.relay_client import RelayClient, RelayError
from tests.unit.test_relay_application_transport import Wire


@pytest_asyncio.fixture
async def setup_link(tmp_path, monkeypatch):
    app = FastAPI()
    stopped = asyncio.Event()

    @app.get("/sessions")
    async def sessions(request: Request):
        return {"sessions": [], "authenticated": request.headers["authorization"] == "Bearer host-only-token"}


    @app.get("/sessions/history-page")
    async def history_page():
        prefix, suffix = b'{"history":"', b'"}'
        body = prefix + b"x" * (150_000 - len(prefix) - len(suffix)) + suffix
        return StreamingResponse(iter([body]), media_type="application/json")

    @app.post("/sessions/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"length": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    @app.get("/sessions/stream")
    async def stream():
        async def chunks():
            try:
                yield b"data: first\n\n"
                await asyncio.Future()
            finally:
                stopped.set()

        return StreamingResponse(chunks(), media_type="text/event-stream")

    @app.get("/sessions/burst")
    async def burst(width: int = 128):
        async def chunks():
            body = b"01234567" * CHUNK
            for offset in range(0, len(body), width):
                yield body[offset : offset + width]

        return StreamingResponse(chunks(), media_type="application/octet-stream")

    monkeypatch.setattr("kollabor_engine.phone_link.get_token", lambda: "host-only-token")
    manager = PhoneLink(app, tmp_path / "phones")
    wire = Wire()

    async def network():
        return {"origin": "https://relay.example", "network": "mine via relay.example", "device": "computer"}

    async def ready(_origin):
        return True

    async def client(identifier):
        if identifier not in manager.clients:
            host = RelayClient(tmp_path, state_dir=manager.directory / identifier)
            host.set_request_handler(lambda peer, method, payload: manager._receive(identifier, peer, method, payload))
            host.add_peer_session_listener(lambda _: manager._invalidate(identifier))
            wire.add(host)
            manager.clients[identifier] = host
        return manager.clients[identifier]

    manager.network = network
    manager.directory_ready = ready
    manager._client = client
    offer = await manager.offer("My iPhone")
    host = manager.clients[offer["id"]]
    phone = RelayClient(tmp_path / "phone", state_dir=tmp_path / "browser-identity")
    phone.state.room = host.state.room
    phone.approve(host.public_key)
    wire.add(phone)
    host._peers[phone.public_key] = phone._session_id
    phone._peers[host.public_key] = host._session_id
    yield manager, offer, host, phone, wire, stopped
    await manager.close()
    await wire.close()


async def secure_phone(
    setup_link,
    *,
    frame_compression=False,
    max_chunk=None,
    coalesce_reads=False,
    zero_body_autorun=False,
):
    manager, offer, host, phone, *_ = setup_link
    await manager.approve(offer["id"], phone.public_key)
    private = PrivateKey.generate()
    capabilities = {
        "public_key": private.public_key.encode().hex(),
        "frame_compression": frame_compression,

        "coalesce_reads": coalesce_reads,
        "zero_body_autorun": zero_body_autorun,
    }
    if max_chunk is not None:
        capabilities["max_chunk"] = max_chunk
    response = await phone.request(host.public_key, "phone_session", capabilities)
    shared = Box(private, PublicKey(bytes.fromhex(response["public_key"]))).shared_key()
    outgoing, incoming = session_keys(shared, host._session_id, phone._session_id)
    negotiated_compression = response.get("frame_compression") is True
    sequence = 0

    async def packet(**data):
        nonlocal sequence
        data["seq"] = sequence
        sequence += 1
        raw = json.dumps(data).encode()
        if negotiated_compression:
            raw = encode_phone_frame(raw)
        encrypted = SecretBox(outgoing).encrypt(raw)
        response = await phone.request(host.public_key, "phone_http", {"packet": base64.b64encode(encrypted).decode()})
        raw = SecretBox(incoming).decrypt(base64.b64decode(response["packet"]))
        if negotiated_compression:
            raw = decode_phone_frame(raw)
        return json.loads(raw)

    return packet


@pytest.mark.asyncio
async def test_scan_does_not_approve_or_transfer_network_credentials(setup_link):
    manager, offer, host, phone, wire, _ = setup_link
    status = await manager.status()
    assert not status["phones"][0]["linked"]
    assert status["phones"][0]["pending"] == [{"key": phone.public_key, "code": pairing_code(phone.public_key)}]
    with pytest.raises(RelayError):
        await phone.request(
            host.public_key,
            "phone_session",
            {"public_key": PrivateKey.generate().public_key.encode().hex()},
            timeout=0.05,
        )
    assert not host.state.approvals
    assert not host.state.config_recipients
    assert "qr_svg" not in json.dumps(status)
    assert host.state.room not in json.dumps(status)
    assert all("host-only-token" not in json.dumps(frame) for _, frame in wire.sent)


@pytest.mark.asyncio
async def test_offer_link_is_the_invitation_the_qr_encodes(setup_link):
    manager, offer, host, *_ = setup_link
    assert offer["link"].startswith("https://")
    assert "/relay/v1/phone/#v=1&id=" + offer["id"] in offer["link"]
    assert f"room={host.state.room}" in offer["link"] and f"host={host.public_key}" in offer["link"]
    qr = segno.make(offer["link"], error="m")
    assert offer["qr_svg"] == qr.svg_inline(scale=4, border=4, dark="#000", light="#fff")


@pytest.mark.asyncio
async def test_approved_phone_streams_authenticated_app_without_engine_token(setup_link):
    manager, offer, host, phone, wire, stopped = setup_link
    packet = await secure_phone(setup_link)
    identifier = secrets.token_hex(16)
    assert (await packet(op="start", id=identifier, method="GET", path="/sessions", headers=[], size=0))["ok"]
    await packet(op="run", id=identifier)
    assert (await packet(op="read", id=identifier))["status"] == 200
    result = await packet(op="read", id=identifier)
    assert json.loads(base64.b64decode(result["data"])) == {"sessions": [], "authenticated": True}
    assert (await packet(op="read", id=identifier))["type"] == "end"
    assert (await manager.status())["phones"][0]["connected"]
    assert all("host-only-token" not in json.dumps(frame) for _, frame in wire.sent)

    identifier = secrets.token_hex(16)
    await packet(op="start", id=identifier, method="GET", path="/sessions/stream", headers=[], size=0)
    await packet(op="run", id=identifier)
    assert (await packet(op="read", id=identifier))["headers"] == [["content-type", "text/event-stream; charset=utf-8"]]
    assert base64.b64decode((await packet(op="read", id=identifier))["data"]) == b"data: first\n\n"
    await packet(op="cancel", id=identifier)
    await asyncio.wait_for(stopped.wait(), 1)
    assert not manager.exchanges


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [128, CHUNK // 2 + 1])
async def test_stream_packets_are_bounded_and_coalesce_small_events(setup_link, width):
    packet = await secure_phone(
        setup_link,
        frame_compression=True,
        max_chunk=CHUNK,
    )
    identifier = secrets.token_hex(16)
    await packet(op="start", id=identifier, method="GET", path=f"/sessions/burst?width={width}", headers=[], size=0)
    await packet(op="run", id=identifier)
    assert (await packet(op="read", id=identifier))["type"] == "headers"
    chunks = []
    while True:
        event = await packet(op="read", id=identifier)
        if event["type"] == "end":
            break
        assert event["type"] == "data"
        chunk = base64.b64decode(event["data"])
        assert 0 < len(chunk) <= CHUNK
        chunks.append(chunk)
    assert b"".join(chunks) == b"01234567" * CHUNK
    if width == 128:
        assert len(chunks) < CHUNK * 8 // width


@pytest.mark.asyncio
async def test_another_phone_can_be_revoked_during_a_connection_attempt(setup_link):
    manager, offer, *_ = setup_link
    original = manager._client
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(identifier):
        entered.set()
        await release.wait()
        return await original(identifier)

    manager._client = blocked
    second = asyncio.create_task(manager.offer("Another Phone"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(manager.revoke(offer["id"]), 1)
        assert offer["id"] not in manager.records
    finally:
        release.set()
        await second


@pytest.mark.asyncio
async def test_upload_chunks_and_local_only_paths(setup_link):
    packet = await secure_phone(
        setup_link,
        frame_compression=True,
        max_chunk=CHUNK,
    )
    for path in (
        "/phonelink",
        "/phone",
        "https://other.example/sessions",
        "//other.example/sessions",
        "/api/config",
        "/sessions/../phonelink",
        "/sessions/%2e%2e/phonelink",
        "/%70honelink",
        "/sessions/%5csecret",
        "/sessions/%00secret",
    ):
        result = await packet(op="start", id=secrets.token_hex(16), method="GET", path=path, headers=[], size=0)
        assert "only on the computer" in result["error"]
    identifier = secrets.token_hex(16)
    body = secrets.token_bytes(70_000)
    await packet(
        op="start",
        id=identifier,
        method="POST",
        path="/sessions/echo",
        headers=[["content-type", "application/octet-stream"]],
        size=len(body),
    )
    for offset in range(0, len(body), CHUNK):
        await packet(
            op="write", id=identifier, offset=offset, data=base64.b64encode(body[offset : offset + CHUNK]).decode()
        )
    await packet(op="run", id=identifier)
    assert (await packet(op="read", id=identifier))["status"] == 200
    response = json.loads(base64.b64decode((await packet(op="read", id=identifier))["data"]))
    assert response == {"length": len(body), "sha256": hashlib.sha256(body).hexdigest()}


@pytest.mark.asyncio
async def test_expired_and_consumed_invitation_and_revoke(setup_link):
    manager, offer, host, phone, *_ = setup_link
    manager.records[offer["id"]]["expires"] = int(time.time()) - 1
    with pytest.raises(PhoneLinkError, match="no longer waiting"):
        await manager.approve(offer["id"], phone.public_key)
    manager.records[offer["id"]]["expires"] = int(time.time()) + 300
    await secure_phone(setup_link)
    with pytest.raises(PhoneLinkError, match="no longer waiting"):
        await manager.approve(offer["id"], phone.public_key)
    await manager.revoke(offer["id"])
    assert not manager.sessions and not manager.clients and not manager.records
    with pytest.raises(RelayError, match="not approved"):
        await manager._receive(offer["id"], phone.public_key, "phone_http", {})


@pytest.mark.asyncio
async def test_persisted_approval_is_private_and_corruption_preserves_data(setup_link, tmp_path):
    manager, offer, host, phone, *_ = setup_link
    await secure_phone(setup_link)
    path = manager.directory / "phones.json"
    assert path.stat().st_mode & 0o777 == 0o600
    data = path.read_text()
    assert phone._store.key.encode().hex() not in data and host.state.room not in data
    await manager.close()
    restored = PhoneLink(manager.app, manager.directory)
    await restored.start()
    try:
        assert restored.records[offer["id"]]["key"] == phone.public_key
    finally:
        await restored.close()
    path.write_text("corrupt private state")
    broken = PhoneLink(manager.app, manager.directory)
    with pytest.raises(PhoneLinkError, match="preserved"):
        await broken.start()
    assert path.read_text() == "corrupt private state"


@pytest.mark.asyncio
async def test_another_engine_cannot_report_revocation_while_owner_still_serves(setup_link):
    manager, offer, host, phone, *_ = setup_link
    await secure_phone(setup_link)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        """
import asyncio, sys
from pathlib import Path
from kollabor_engine.phone_link import PhoneLink, PhoneLinkError
async def main():
    manager = PhoneLink(None, Path(sys.argv[1]))
    try:
        await manager.start()
        await manager.revoke(sys.argv[2])
        print('revoked')
    except PhoneLinkError as exc:
        print('blocked' if 'another' in str(exc).lower() else str(exc))
    finally:
        await manager.close()
asyncio.run(main())
""",
        str(manager.directory),
        offer["id"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
    assert process.returncode == 0, stderr.decode()
    assert stdout.strip() == b"blocked"
    assert json.loads((manager.directory / "phones.json").read_bytes())["phones"]
    assert manager.records[offer["id"]]["key"] == phone.public_key


@pytest.mark.asyncio
async def test_owner_handoff_reloads_revocation_instead_of_cached_approval(setup_link):
    manager, offer, *_ = setup_link
    await secure_phone(setup_link)
    await manager.close()
    successor = PhoneLink(manager.app, manager.directory)
    successor.network = manager.network
    successor.directory_ready = manager.directory_ready
    await successor.start()
    try:
        await successor.revoke(offer["id"])
    finally:
        await successor.close()
    await manager.start()
    assert manager.records == {}
    assert manager._owner_fd is not None


@pytest.mark.asyncio
async def test_lock_failure_preserves_phone_state_and_reports_unavailability(setup_link, monkeypatch):
    manager, *_ = setup_link
    path = manager.directory / "phones.json"
    saved = path.read_bytes()
    await manager.close()

    def denied():
        raise PermissionError("private lock unavailable")

    monkeypatch.setattr(manager, "_claim_owner", denied)
    with pytest.raises(PhoneLinkError, match="could not be locked"):
        await manager.start()
    assert path.read_bytes() == saved
    assert manager._owner_fd is None


@pytest.mark.asyncio
async def test_approval_recovers_after_process_exits_between_durable_saves(setup_link, monkeypatch):
    manager, offer, host, phone, wire, _ = setup_link
    identifier, original_key, original_room = offer["id"], host.public_key, host.state.room
    await manager.close()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        """
import asyncio, os, secrets, sys
from pathlib import Path
from kollabor_engine.phone_link import PhoneLink
from plugins.hub.relay_client import RelayClient
async def main():
    directory, identifier, key = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    manager = PhoneLink(None, directory)
    await manager.start()
    client = RelayClient(directory, state_dir=directory / identifier)
    client._peers[key] = secrets.token_hex(16)
    manager.clients[identifier] = client
    client.approve = lambda _: os._exit(77)
    await manager.approve(identifier, key)
asyncio.run(main())
""",
        str(manager.directory),
        identifier,
        phone.public_key,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
    assert process.returncode == 77, stderr.decode() + stdout.decode()
    restored = PhoneLink(manager.app, manager.directory)

    async def no_background_connect(_):
        return False

    async def connect(client, *_args, **_kwargs):
        assert client.state.approvals == [phone.public_key]
        wire.add(client)
        client._peers[phone.public_key] = phone._session_id
        phone._peers[client.public_key] = client._session_id

    restored.directory_ready = no_background_connect
    monkeypatch.setattr(RelayClient, "connect", connect)
    await restored.start()
    try:
        client = await restored._client(identifier)
        assert (client.public_key, client.state.room) == (original_key, original_room)
        response = await phone.request(
            client.public_key,
            "phone_session",
            {"public_key": PrivateKey.generate().public_key.encode().hex()},
        )
        assert response["protocol"] == "kollab-phonelink/1"
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_saved_phone_restores_without_a_terminal_agent(setup_link):
    manager, offer, host, phone, *_ = setup_link
    await secure_phone(setup_link)
    await manager.close()
    restored = PhoneLink(manager.app, manager.directory)
    attempted = asyncio.Event()

    async def no_agent_lookup():
        raise AssertionError("Saved phones must not depend on agent-daemon status.")

    async def ready(_origin):
        return True

    async def reconnect(identifier):
        assert identifier == offer["id"]
        assert not restored._lock.locked(), "A connection attempt must not block phone revocation."
        attempted.set()

    restored.network = no_agent_lookup
    restored.directory_ready = ready
    restored._client = reconnect
    await restored.start()
    try:
        await asyncio.wait_for(attempted.wait(), 1)
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_session_change_and_replay_are_rejected(setup_link):
    manager, offer, host, phone, *_ = setup_link
    await secure_phone(setup_link)
    session = manager.sessions[offer["id"]]
    encrypted = base64.b64encode(
        session.incoming.encrypt(
            json.dumps(
                {
                    "seq": 0,
                    "op": "start",
                    "id": secrets.token_hex(16),
                    "method": "GET",
                    "path": "/sessions",
                    "headers": [],
                    "size": 0,
                }
            ).encode()
        )
    ).decode()
    await manager._receive(offer["id"], phone.public_key, "phone_http", {"packet": encrypted})
    with pytest.raises(RelayError, match="invalid phone session"):
        await manager._receive(offer["id"], phone.public_key, "phone_http", {"packet": encrypted})
    host._set_peers({"type": "peers", "peers": [{"key": phone.public_key, "session": secrets.token_hex(16)}]})
    assert not manager.sessions and not manager.exchanges
