"""proxy.py <op> [json-args]: one connect_proxy op on this folder's live agent.

proxy.py rpc <state.method> [json-params]: one handshake-phase state RPC on it.

Run from the folder (cwd). Prints the JSON reply. Test harness only.
"""

import asyncio
import json
import os
import sys

from plugins.hub.messenger import AgentMessenger
from plugins.hub.presence import get_presence_dir


def folder_socket() -> str:
    for record in get_presence_dir().glob("*.json"):
        try:
            data = json.loads(record.read_text())
            os.kill(int(data["pid"]), 0)
        except (OSError, ValueError, KeyError):
            continue
        if data.get("project") == os.getcwd() and data.get("socket_path"):
            return data["socket_path"]
    raise SystemExit("no live agent in " + os.getcwd())


async def rpc(method: str, params: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(folder_socket())
    req = {"action": "rpc_request", "request_id": "ks1", "method": method, "params": params, "timeout": 60}
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    reply = json.loads(await asyncio.wait_for(reader.readline(), 70))
    writer.close()
    return reply


op = sys.argv[1]
if op == "rpc":
    params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(json.dumps(asyncio.run(rpc(sys.argv[2], params)), indent=1))
else:
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    reply = asyncio.run(AgentMessenger.request_connect_proxy(folder_socket(), op, args, timeout=40))
    print(json.dumps(reply, indent=1))
