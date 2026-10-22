"""A real relay + PhoneLink host on scratch ports for browser proofs.

    HOME=<scratch> python tests/live/phonelink_harness.py <relay-port> <control-port> <scratch-dir>

The relay serves only the loader. The host serves the signed phone app and a
stub engine. Nothing here touches the real ~/.kollab, engine, or web UI.
"""

import asyncio
import json
import secrets
import shutil
import sys
import time
from pathlib import Path

from aiohttp import web
from fastapi import FastAPI
from kollabor_engine import phone_link
from kollabor_engine.phone_link import PhoneLink

from plugins.hub import phone_portal, relay_service
from plugins.hub.relay_client import RelayClient

ORIGIN = "https://relay.example"
ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "packages/kollabor-webui/frontend/dist/phonelink"


async def main(relay_port: int, control_port: int, scratch: Path) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    app_dir = scratch / "app"
    shutil.copytree(DIST / "app", app_dir, dirs_exist_ok=True)
    phone_link.app_directory = lambda: app_dir
    phone_portal.static_directory = lambda: DIST / "loader"

    engine = FastAPI()

    @engine.get("/sessions")
    async def sessions():
        return {"sessions": []}

    @engine.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anything(path: str):
        return []

    manager = PhoneLink(engine, scratch / "phones")
    await manager.start()

    async def network():
        return {"origin": ORIGIN, "network": "scratch", "device": "scratch"}

    async def ready(_):
        return True

    manager.network, manager.directory_ready = network, ready
    runner = web.AppRunner(relay_service.create_app(relay_service.RelayConfig(ORIGIN, "e" * 32, dev_in_memory=True)))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", relay_port).start()

    state: dict = {}

    async def offer(_request):
        identifier = secrets.token_hex(16)
        host = RelayClient(scratch, state_dir=scratch / "host" / identifier)
        host.set_request_handler(lambda peer, method, payload: manager._receive(identifier, peer, method, payload))
        host.add_peer_session_listener(lambda _: manager._invalidate(identifier))
        manager.records[identifier] = {
            "origin": ORIGIN, "name": "Scratch Phone", "key": "", "host": host.public_key, "seen": 0,
            "created": int(time.time()), "expires": int(time.time()) + 300,
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

    async def approve(_request):
        for phone in (await manager.status())["phones"]:
            for pending in phone["pending"]:
                return web.json_response(await manager.approve(phone["id"], pending["key"]))
        return web.json_response({"error": "nothing pending"}, status=409)

    async def revoke(request):
        return web.json_response(await manager.revoke(request.match_info["id"]))

    control = web.Application()
    control.add_routes([web.post("/offer", offer), web.get("/status", status), web.post("/approve", approve),
                        web.delete("/phones/{id}", revoke)])
    control_runner = web.AppRunner(control)
    await control_runner.setup()
    await web.TCPSite(control_runner, "127.0.0.1", control_port).start()
    print("ready", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])))
