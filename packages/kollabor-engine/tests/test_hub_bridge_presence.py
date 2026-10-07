"""Presence lists only agents whose process is still running."""

import json
import os
import subprocess
import sys
import time

from kollabor_engine import hub_bridge


def test_presence_skips_an_agent_whose_process_is_gone(tmp_path, monkeypatch):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    for name, pid in (("lapis", os.getpid()), ("ruby", gone.pid)):
        (tmp_path / f"{name}.json").write_text(
            json.dumps(
                {
                    "agent_id": name,
                    "identity": name,
                    "pid": pid,
                    "last_heartbeat": time.time(),
                }
            )
        )
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [tmp_path])

    agents = hub_bridge.HubBridge()._read_presence_files()

    assert [agent["identity"] for agent in agents] == ["lapis"]
