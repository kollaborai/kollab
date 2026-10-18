"""Print what the web UI's sidebar and New Session get from an engine in this HOME.

Seeds a vault for sapphire (ran 3 days ago) and for maya (a persona of the
marketing-crew roster, just now) in the folder the engine runs in
and a network roster (lapis@prod-box), starts kollabor_engine from this
checkout on a free port with no agent running, and prints one line each:

  first gem: <name> available=<bool>
  sapphire last_ran: <yes|no>
  offline: <identities>
  offline look: maya=<display name>/<avatar kind> sapphire=<display name>/<avatar kind>
  network: live=<bool> remote=<handles>
  workspaces: home=<name> browse=<subfolders>

(specs/web_sidebar_roster.json). usage: HOME=<spec home> web_sidebar_roster.py
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(port, path):
    token = (Path.home() / ".kollab" / "engine.token").read_text().strip()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
    resp = conn.getresponse()
    try:
        return resp.status, json.loads(resp.read() or b"null")
    finally:
        conn.close()


def _seed(folder: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "kollabor-config" / "src"))
    from kollabor_config.config_utils import encode_project_path

    vault = Path.home() / ".kollab" / "projects" / encode_project_path(folder) / "hub" / "vaults" / "sapphire"
    vault.mkdir(parents=True, exist_ok=True)
    stream = vault / "stream.jsonl"
    stream.write_text('{"type": "session_start"}\n')
    three_days = time.time() - 3 * 86400
    os.utime(stream, (three_days, three_days))
    maya = vault.parent / "maya"
    maya.mkdir(parents=True, exist_ok=True)
    (maya / "stream.jsonl").write_text('{"type": "session_start"}\n')
    (folder / "alpha").mkdir(exist_ok=True)
    roster = {
        "networks": {
            "home-net": {
                "network": "home-net",
                "device": "mac-box",
                "trust": "open",
                "folder": str(folder),
                "agents": {
                    "lapis@prod-box": {"name": "lapis", "device": "prod-box", "handle": "lapis@prod-box",
                                       "state": "idle", "last_seen": time.time() - 7200},
                },
            },
        },
    }
    (Path.home() / ".kollab" / "engine-network.json").write_text(json.dumps(roster))


def main() -> None:
    folder = Path.cwd().resolve()
    _seed(folder)
    port = _free_port()
    log = open(Path.home() / "engine.log", "ab")
    engine = subprocess.Popen(
        [sys.executable, "-m", "kollabor_engine", "serve", "--port", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={k: v for k, v in os.environ.items() if k != "KOLLAB_WEBUI_HOSTS"},
    )
    try:
        deadline = time.time() + 40
        agents = None
        while time.time() < deadline and agents is None:
            time.sleep(0.5)
            try:
                status, body = _get(port, "/agents?refresh=true")
            except (OSError, http.client.HTTPException):
                continue
            if status == 200:
                agents = body["agents"]
        if agents is None:
            sys.exit("engine never answered /agents")
        first = agents[0]
        print(f"first gem: {first['name']} available={first['available']}")
        sapphire = next(agent for agent in agents if agent["name"] == "sapphire")
        print(f"sapphire last_ran: {'yes' if sapphire.get('last_ran') else 'no'}")

        _, listing = _get(port, "/sessions")
        print("offline:", ",".join(row["identity"] for row in listing.get("offline", [])) or "none")
        rows = {row["identity"]: row for row in listing.get("offline", [])}
        look = {name: f"{rows[name].get('display_name')}/{(rows[name].get('avatar') or {}).get('kind')}" for name in ("maya", "sapphire")}
        print(f"offline look: maya={look['maya']} sapphire={look['sapphire']}")
        network = listing["network"]
        handles = ",".join(row["handle"] for row in network["remote"]) or "none"
        print(f"network: live={network.get('live')} remote={handles}")

        _, places = _get(port, "/workspaces")
        _, browsed = _get(port, f"/workspaces/browse?path={folder}")
        print(f"workspaces: home={places['home']['name']} browse={','.join(browsed['dirs'])}")
    finally:
        engine.terminate()
        try:
            engine.wait(timeout=10)
        except subprocess.TimeoutExpired:
            engine.kill()


if __name__ == "__main__":
    main()
