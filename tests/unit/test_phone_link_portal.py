"""Real directory registration and JavaScript/Python PhoneLink interoperability."""

import asyncio
import hashlib
import json
import secrets
import shutil
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from kollabor_engine.phone_link import PhoneLink

from plugins.hub import phone_portal, relay_service
from plugins.hub.relay_client import RelayClient

ORIGIN = "https://relay.example"
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_browser_socket_requires_exact_origin_native_still_rejects_browsers(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>PhoneLink</title>")
    monkeypatch.setattr(phone_portal, "static_directory", lambda: tmp_path)
    app = relay_service.create_app(relay_service.RelayConfig(ORIGIN, "f" * 32, dev_in_memory=True))
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/relay/v1/phone/status")
        assert await response.json() == {"protocol": "kollab-phonelink/1", "ready": True}
        page = await client.get("/relay/v1/phone/")
        assert page.status == 200 and page.headers["Referrer-Policy"] == "no-referrer"
        assert page.headers["Cache-Control"] == "no-store"
        assert (await client.get("/relay/v1/phone/status?secret=bad")).status == 400
        for path, origin in (
            ("/relay/v1/ws", ORIGIN),
            ("/relay/v1/phone/ws", "https://other.example"),
            ("/relay/v1/phone/ws", ""),
        ):
            with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
                await client.ws_connect(path, headers={"Origin": origin})
            assert caught.value.status == 403
        socket = await client.ws_connect("/relay/v1/phone/ws", headers={"Origin": ORIGIN})
        assert (await socket.receive_json())["type"] == "challenge"
        await socket.close()


@pytest.mark.asyncio
async def test_javascript_phone_client_reaches_streaming_engine(tmp_path, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("Node is required for the actual JavaScript protocol client")
    frontend = ROOT / "packages/kollabor-webui/frontend"
    if not (frontend / "node_modules/libsodium-wrappers").is_dir():
        pytest.skip("Install the frontend's locked dependencies")
    app = FastAPI()

    @app.get("/sessions")
    async def sessions(request: Request):
        return {"sessions": [], "authenticated": request.headers.get("authorization") == "Bearer private-host-token"}

    @app.post("/sessions/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"length": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    @app.get("/sessions/stream")
    async def stream():
        async def chunks():
            yield b"data: first\n\n"
            await asyncio.Future()

        return StreamingResponse(chunks(), media_type="text/event-stream")

    monkeypatch.setattr("kollabor_engine.phone_link.get_token", lambda: "private-host-token")
    manager = PhoneLink(app, tmp_path / "phones")
    await manager.start()
    identifier = secrets.token_hex(16)
    host = RelayClient(tmp_path, state_dir=tmp_path / "host")
    host.set_request_handler(lambda peer, method, payload: manager._receive(identifier, peer, method, payload))
    host.add_peer_session_listener(lambda _: manager._invalidate(identifier))
    manager.records[identifier] = {
        "origin": ORIGIN,
        "name": "Phone",
        "key": "",
        "host": host.public_key,
        "seen": 0,
        "created": int(time.time()),
        "expires": int(time.time()) + 300,
    }
    manager.clients[identifier] = host

    async def network():
        return {"origin": ORIGIN, "network": "test", "device": "test"}

    async def ready(_):
        return True

    manager.network = network
    manager.directory_ready = ready
    relay = TestServer(relay_service.create_app(relay_service.RelayConfig(ORIGIN, "e" * 32, dev_in_memory=True)))
    await relay.start_server()
    process = None
    reader = None
    try:
        async with aiohttp.ClientSession() as http:
            socket = await http.ws_connect(relay.make_url("/relay/v1/ws"), protocols=["kollab-relay/1"])
            challenge = await socket.receive_json()
            host._session_id = secrets.token_hex(16)
            signed = relay_service._registration_message(
                ORIGIN, challenge["nonce"], host.public_key, host.state.room, host._session_id
            )
            await socket.send_json(
                {
                    "type": "register",
                    "key": host.public_key,
                    "room": host.state.room,
                    "session": host._session_id,
                    "signature": host._store.key.sign(signed).signature.hex(),
                }
            )
            assert (await socket.receive_json())["type"] == "registered"
            host._set_peers(await socket.receive_json())
            host._state, host._ws, host._closed = "online", socket, False
            host.state.origin = ORIGIN

            async def read():
                async for message in socket:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        await host._handle_frame(json.loads(message.data))

            reader = asyncio.create_task(read())
            process = await asyncio.create_subprocess_exec(
                "node",
                "--experimental-strip-types",
                "tests/phonelink-interop.mts",
                cwd=frontend,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            settings = {
                "socket": str(relay.make_url("/relay/v1/ws")).replace("http:", "ws:"),
                "identity": {
                    "id": identifier,
                    "room": host.state.room,
                    "host": host.public_key,
                    "expires": int(time.time()) + 300,
                    "seed": secrets.token_hex(32),
                    "approved": False,
                },
            }
            process.stdin.write((json.dumps(settings) + "\n").encode())
            await process.stdin.drain()
            process.stdin.close()
            async with asyncio.timeout(15):
                while not host.peers():
                    if process.returncode is not None:
                        break
                    await asyncio.sleep(0.01)
            assert host.peers(), (await process.stderr.read()).decode()
            await manager.approve(identifier, host.peers()[0]["key"])
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
            assert process.returncode == 0, stderr.decode() + stdout.decode()
            assert b"passed: JS/Python encryption" in stdout
            events = (await manager.event_history())["events"]
            assert any(
                event["event"] == "phone_diagnostic"
                and event["phone_event"] == "pagehide"
                for event in events
            )
            assert any(
                event["event"] == "phone_diagnostic"
                and event["phone_event"] == "relay_socket_closed"
                and event["close_code"] == 1001
                and event["close_reason"] == "going_away"
                for event in events
            )
    finally:
        if process and process.returncode is None:
            process.terminate()
            await process.wait()
        if reader:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        await manager.close()
        await relay.close()
