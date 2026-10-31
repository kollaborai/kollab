"""Inspect an actual detached agent and engine on the Windows Actions runner."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
AGENT_ONLINE = re.compile(r"^\s+(?P<name>\S+).*\bpid=(?P<pid>\d+)", re.MULTILINE)


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("diagnostic must run on Windows")
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="kollab466-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        profile = root / "profile"
        project = root / "project"
        config = profile / ".kollab"
        config.mkdir(parents=True)
        project.mkdir()
        (config / "config.json").write_text(json.dumps({"application": {"setup_completed": True}}))
        env = {**os.environ, "USERPROFILE": str(profile), "KOLLAB_CONFIG_DIR": str(config),
               "KOLLAB_PROJECT_ROOT": str(project), "KOLLAB_NO_KEYRING": "1",
               "KOLLAB_WEBUI_NO_BROWSER": "1"}
        os.environ.update({key: env[key] for key in ("USERPROFILE", "KOLLAB_CONFIG_DIR", "KOLLAB_PROJECT_ROOT", "KOLLAB_NO_KEYRING")})
        os.chdir(project)
        online = None
        engine = None
        agent_log = root / "agent.log"
        engine_log = root / "engine.log"

        def kollab(*args, **kwargs):
            return subprocess.run([str(binary), *args], cwd=project, env=env, text=True, timeout=60, **kwargs)

        try:
            with agent_log.open("w", encoding="utf-8") as stream:
                started = kollab("--detached", stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
            print(f"detached_exit={started.returncode}", flush=True)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status = kollab("--hub", "status", capture_output=True)
                online = AGENT_ONLINE.search(status.stdout)
                if online:
                    break
                time.sleep(2)
            if not online:
                print(f"status_exit={status.returncode}; status={status.stdout[-1000:]}; errors={status.stderr[-1000:]}")
                raise RuntimeError("detached agent did not appear online")
            name = online.group("name")
            print(f"online_identity={name}; pid={online.group('pid')}", flush=True)
            records = list(config.glob("projects/*/hub/presence/*.json")) + list(config.glob("hub/presence/*.json"))
            matches = []
            for record in records:
                data = json.loads(record.read_text(encoding="utf-8"))
                if data.get("identity") == name:
                    matches.append(data)
            if len(matches) != 1:
                raise RuntimeError(f"expected one actual presence record, got {len(matches)}")
            data = matches[0]
            socket_text = data.get("socket_path") or ""
            endpoint = Path(socket_text) if socket_text else None
            relative = str(endpoint.relative_to(profile)) if endpoint and endpoint.is_relative_to(profile) else "outside_profile"
            endpoint_keys = sorted(json.loads(endpoint.read_text(encoding="utf-8")).keys()) if endpoint and endpoint.is_file() else []
            print(json.dumps({"record_has_socket_path": bool(socket_text), "relative_socket_path": relative,
                "endpoint_exists": bool(endpoint and endpoint.exists()),
                "endpoint_is_regular_file": bool(endpoint and endpoint.is_file()),
                "endpoint_is_unix_socket": bool(endpoint and endpoint.is_socket()),
                "endpoint_json_keys": endpoint_keys}), flush=True)

            from kollabor_engine.hub_bridge import HubBridge
            from plugins.hub.presence import live_agents_on_machine
            bridge = HubBridge()
            raw = bridge.get_agents(use_cache=False)
            rows = bridge.discover_sessions(use_cache=False)
            matched = [row for row in rows if row.get("identity") == name]
            machine = [r for _, r in live_agents_on_machine() if r.get("identity") == name]
            print(json.dumps({"bridge_presence_matches": sum(r.get("identity") == name for r in raw),
                "bridge_discovered_matches": len(matched), "machine_discovery_matches": len(machine)}), flush=True)
            if matched:
                reply = asyncio.run(bridge.query_socket(matched[0]["session_id"], "get_status"))
                print(json.dumps({"bridge_ipc_reply_received": isinstance(reply, dict),
                                  "bridge_ipc_reply_keys": sorted(reply) if isinstance(reply, dict) else []}), flush=True)

            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            with engine_log.open("w", encoding="utf-8") as errors:
                engine = subprocess.Popen([sys.executable, "-m", "kollabor_engine", "serve", "--port", str(port)],
                    cwd=project, env=env, stdout=subprocess.PIPE, stderr=errors, text=True)
                lines: queue.Queue[str | None] = queue.Queue()
                def pump():
                    for line in engine.stdout:
                        lines.put(line.strip())
                    lines.put(None)
                threading.Thread(target=pump, daemon=True).start()
                deadline = time.monotonic() + 90
                ready = False
                while time.monotonic() < deadline:
                    line = lines.get(timeout=max(1, deadline - time.monotonic()))
                    if line is None:
                        break
                    if line.startswith(f"READY:{port}"):
                        ready = True
                        break
                if not ready:
                    raise RuntimeError("engine did not become ready")
                token = (config / "engine.token").read_text(encoding="utf-8").strip()
                request = urllib.request.Request(f"http://127.0.0.1:{port}/sessions", headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                visible = [row for row in payload.get("sessions", []) if row.get("identity") == name]
                print(json.dumps({"http_sessions_matches": len(visible), "http_session_keys": sorted(visible[0]) if visible else []}), flush=True)
        finally:
            if engine and engine.poll() is None:
                engine.terminate()
                try:
                    engine.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    engine.kill()
                    engine.wait()
            if online:
                stopped = kollab("--hub", "stop", online.group("name"), capture_output=True)
                print(f"hub_stop_exit={stopped.returncode}", flush=True)


if __name__ == "__main__":
    main()
