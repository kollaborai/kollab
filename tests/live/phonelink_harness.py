"""A real relay + PhoneLink host on scratch ports for browser proofs.

    HOME=<scratch> python tests/live/phonelink_harness.py <relay-port> <control-port> <scratch-dir>

The relay serves only the loader. The host serves the signed phone app and a
stub engine. Nothing here touches the real ~/.kollab, engine, or web UI.

KOLLAB_PHONELINK_ORIGIN replaces the relay's origin (https://relay.example). With
KOLLAB_PHONELINK_TLS=<folder holding cert.pem and key.pem> the relay also serves
https on that origin's port, so the phone app in the Android emulator can link
through https://relay.localhost:<port> (tests/live/phonelink_emulator.sh).
"""

import asyncio
import base64
import json
import os
import secrets
import shutil
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web
from fastapi import FastAPI
from kollabor_engine import phone_link
from kollabor_engine.history_paging import build_history_page, resolve_history_output
from kollabor_engine.phone_link import PhoneLink, decode_phone_frame

from plugins.hub import phone_portal, relay_service
from plugins.hub.relay_client import RelayClient

ORIGIN = os.environ.get("KOLLAB_PHONELINK_ORIGIN", "https://relay.example")
ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "packages/kollabor-webui/frontend/dist/phonelink"


async def main(relay_port: int, control_port: int, scratch: Path) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    app_dir = scratch / "app"
    shutil.copytree(DIST / "app", app_dir, dirs_exist_ok=True)
    phone_link.app_directory = lambda: app_dir
    phone_portal.static_directory = lambda: DIST / "loader"

    engine = FastAPI()
    synthetic_session_id = "synthetic-phone-history"
    synthetic_history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"synthetic-history-{index:03d} " + ("fixture-data-" * 220),
        }
        for index in range(219)
    ]
    synthetic_history.append({
        "role": "tool",
        "tool_call_id": "synthetic-tool-219",
        "metadata": {"tool_call_id": "synthetic-tool-219"},
        "content": (
            "SYNTHETIC_TOOL_OUTPUT_START"
            + "x" * 1_200_000
            + "SYNTHETIC_TOOL_OUTPUT_END"
        ),
    })
    metrics = {
        "phone_sessions": 0,
        "history_starts": [],
        "history_reads": [],
        "history_output_ref": False,
        "tool_output_reads": [],
    }
    exchange_paths = {}

    @engine.get("/sessions")
    async def sessions():
        return {
            "sessions": [{
                "session_id": synthetic_session_id,
                "name": "Synthetic PhoneLink History",
                "history_length": len(synthetic_history),
                "updated_at": "2026-01-01T00:00:00Z",
                "active": False,
            }],
            "network": {"device": "scratch", "remote": []},
            "offline": [],
            "hidden": [],
            "order": [synthetic_session_id],
            "order_revision": 0,
        }

    @engine.get("/sessions/{session_id}/history")
    async def history_page(
        session_id: str,
        limit: int = 50,
        before: str | None = None,
        after: str | None = None,
    ):
        if session_id != synthetic_session_id:
            return {"error": "unknown synthetic session"}
        return build_history_page(
            session_id,
            synthetic_history,
            limit=limit,
            before=before,
            after=after,
        )

    @engine.get("/sessions/{session_id}/history/output")
    async def history_output(session_id: str, ref: str):
        if session_id != synthetic_session_id:
            return {"error": "unknown synthetic session"}
        return {
            "session_id": session_id,
            "content": resolve_history_output(session_id, synthetic_history, ref),
        }

    @engine.get("/sessions/{session_id}/permissions")
    async def permissions(session_id: str):
        return {"pending_prompts": [], "approval_mode": "trust_all"}


    @engine.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anything(path: str):
        return []

    manager = PhoneLink(engine, scratch / "phones")
    await manager.start()

    async def handle_phone_request(identifier, peer, method, payload):
        session = manager.sessions.get(identifier)
        request_info = None
        if method == "phone_session":
            metrics["phone_sessions"] += 1
        if method == "phone_http" and session:
            raw = session.incoming.decrypt(base64.b64decode(payload["packet"]))
            if session.frame_compression:
                raw = decode_phone_frame(raw)
            request_info = json.loads(raw)
            if request_info.get("op") == "start":
                path = request_info.get("path", "")
                exchange_paths[(identifier, str(request_info.get("id")))] = path
                if "/history" in path and "/output" not in path:
                    metrics["history_starts"].append(path)
        response = await manager._receive(identifier, peer, method, payload)
        if method == "phone_http" and session and request_info and request_info.get("op") == "read":
            raw = session.outgoing.decrypt(base64.b64decode(response["packet"]))
            if session.frame_compression:
                raw = decode_phone_frame(raw)
            event = json.loads(raw)
            path = exchange_paths.get((identifier, str(request_info.get("id"))), "")
            body_chunk = base64.b64decode(event["data"]) if isinstance(event.get("data"), str) else b""
            size = len(body_chunk)
            if "/history" in path and "/output" not in path:
                try:
                    page_data = json.loads(body_chunk) if body_chunk else {}
                    history_items = len(page_data.get("history", []))
                    metrics["history_output_ref"] = metrics["history_output_ref"] or any(
                        message.get("metadata", {}).get("history_output_ref")
                        for message in page_data.get("history", [])
                        if isinstance(message.get("metadata"), dict)
                    )
                except (json.JSONDecodeError, AttributeError):
                    history_items = None
                metrics["history_reads"].append({
                    "path": path,
                    "bytes": size,
                    "items": history_items,
                    "end": event.get("end") is True or event.get("type") == "end",
                })
            if "/history/output" in path:
                metrics["tool_output_reads"].append({"path": path, "bytes": size})
        return response



    async def network():
        return {"origin": ORIGIN, "network": "scratch", "device": "scratch"}

    async def ready(_):
        return True

    manager.network, manager.directory_ready = network, ready
    runner = web.AppRunner(relay_service.create_app(relay_service.RelayConfig(ORIGIN, "e" * 32, dev_in_memory=True)))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", relay_port).start()
    if tls := os.environ.get("KOLLAB_PHONELINK_TLS"):
        if urlsplit(ORIGIN).port is None:
            raise SystemExit("KOLLAB_PHONELINK_TLS needs a port in KOLLAB_PHONELINK_ORIGIN")
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(Path(tls) / "cert.pem", Path(tls) / "key.pem")
        await web.TCPSite(runner, "127.0.0.1", urlsplit(ORIGIN).port, ssl_context=context).start()

    state: dict = {}

    async def offer(_request):
        identifier = secrets.token_hex(16)
        host = RelayClient(scratch, state_dir=scratch / "host" / identifier)
        host.set_request_handler(lambda peer, method, payload: handle_phone_request(identifier, peer, method, payload))
        host.add_peer_session_listener(lambda _: manager._invalidate(identifier))
        manager.records[identifier] = {
            "origin": ORIGIN, "name": "Scratch Phone", "key": "", "host": host.public_key, "seen": 0,
            "created": int(time.time()), "expires": int(time.time()) + 300,
            "network": "scratch",  # offer() writes it; revoke and expiry read it
        }
        manager.clients[identifier] = host
        import aiohttp

        http = aiohttp.ClientSession()
        socket = await http.ws_connect(f"http://127.0.0.1:{relay_port}/relay/v1/ws", protocols=["kollab-relay/1"])
        challenge = await socket.receive_json()
        host._session_id = secrets.token_hex(16)
        signed = relay_service._registration_message(
            ORIGIN, challenge["nonce"], host.public_key, host.state.room, host._session_id
        )
        await socket.send_json({"type": "register", "key": host.public_key, "room": host.state.room,
                                "session": host._session_id, "signature": host.sign(signed)})
        assert (await socket.receive_json())["type"] == "registered"
        host._set_peers(await socket.receive_json())
        host._state, host._ws, host._closed = "online", socket, False
        host.state.origin = ORIGIN

        async def read():
            async for message in socket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await host._handle_frame(json.loads(message.data))

        state[identifier] = asyncio.create_task(read())
        manager._persist()
        fragment = (
            f"v=1&id={identifier}&room={host.state.room}&host={host.public_key}"
            f"&expires={manager.records[identifier]['expires']}"
        )
        return web.json_response({"id": identifier, "fragment": fragment,
                                  "fingerprint": phone_link.host_fingerprint(host.public_key)})

    async def status(_request):
        return web.json_response(await manager.status())


    async def measurements(_request):
        return web.json_response(metrics)

    async def approve(_request):
        for phone in (await manager.status())["phones"]:
            for pending in phone["pending"]:
                return web.json_response(await manager.approve(phone["id"], pending["key"]))
        return web.json_response({"error": "nothing pending"}, status=409)

    async def revoke(request):
        return web.json_response(await manager.revoke(request.match_info["id"]))

    control = web.Application()
    control.add_routes([
        web.post("/offer", offer),
        web.get("/status", status),
        web.get("/metrics", measurements),
        web.post("/approve", approve),
        web.delete("/phones/{id}", revoke),
    ])
    control_runner = web.AppRunner(control)
    await control_runner.setup()
    await web.TCPSite(control_runner, "127.0.0.1", control_port).start()
    print("ready", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])))
