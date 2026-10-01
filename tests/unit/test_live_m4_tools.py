"""The helpers tests/live/m4 runs on production-shaped inputs: the edge vhost rewrite and the old-stack finder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

M4 = Path(__file__).resolve().parents[2] / "tests" / "live" / "m4"

# The shape of the selfhost.kollabor.ai vhost: two relay workers, a health port and a static key file server.
VHOST = """\
upstream selfhost_relay {
    least_conn;
    server 10.0.0.5:9178 max_fails=2 fail_timeout=5s;
    server 10.0.0.5:9179 max_fails=2 fail_timeout=5s;
}
server {
    listen 443 ssl;
    server_name selfhost.kollabor.ai;
    location = /relay/v1/ws {
        proxy_pass http://selfhost_relay;
    }
    location = /relay/v1/health {
        proxy_pass http://10.0.0.5:9180;
    }
    location /relay/v1/enrollment/ {
        limit_except POST { deny all; }
        proxy_pass http://selfhost_relay;
    }
    location = /.well-known/agent-keys.json {
        proxy_pass http://10.0.0.5:9177/agent-keys.json;
    }
    location = /.well-known/agent-keys {
        proxy_pass http://10.0.0.5:9177/agent-keys.json;
    }
    location / {
        return 404;
    }
}
"""


def repoint(vhost: str, target: str = "10.0.0.5:9178") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(M4 / "repoint_vhost.py"), target], input=vhost, capture_output=True, text=True, check=False
    )


def test_every_route_of_the_manual_stack_ends_up_on_the_one_port():
    result = repoint(VHOST)
    assert result.returncode == 0, result.stderr
    new = result.stdout
    assert new.count("    server ") == 1 and "    server 10.0.0.5:9178 max_fails=2 fail_timeout=5s;" in new
    assert "least_conn;" in new and new.count("proxy_pass http://selfhost_relay;") == 2  # the upstream name stays
    assert "location = /relay/v1/health {\n        proxy_pass http://10.0.0.5:9178;" in new
    assert new.count("proxy_pass http://10.0.0.5:9178/.well-known/agent-keys.json;") == 2  # both spellings
    for gone in ("9177", "9179", "9180"):
        assert gone not in new
    assert "limit_except POST { deny all; }" in new and "return 404;" in new  # nothing else was touched


def test_the_rewrite_is_idempotent():
    once = repoint(VHOST).stdout
    assert repoint(once).stdout == once


UNPLACEABLE = "server {\n    location = /other {\n        proxy_pass http://10.0.0.5:1234;\n    }\n}\n"
NOTHING_TO_REPOINT = "server {\n    location / { return 404; }\n}\n"


@pytest.mark.parametrize("vhost", [UNPLACEABLE, NOTHING_TO_REPOINT])
def test_the_rewrite_refuses_what_it_does_not_understand(vhost):
    result = repoint(vhost)
    assert result.returncode == 3 and result.stdout == "" and "repoint_vhost:" in result.stderr


def test_a_bad_target_is_refused():
    assert repoint(VHOST, "not-a-target").returncode == 3


def fake_proc(root: Path, pid: int, args: list[str], cwd: str) -> None:
    entry = root / str(pid)
    entry.mkdir()
    (entry / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
    os.symlink(cwd, entry / "cwd")


def test_the_old_stack_is_found_by_what_it_is_configured_to_do(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    ours, theirs = tmp_path / "selfhost.json", tmp_path / "public.json"
    ours.write_text(
        json.dumps({"origin": "https://selfhost.kollabor.ai", "bind_host": "10.0.0.5", "trusted_proxies": ["10.0.0.1"]})
    )
    theirs.write_text(json.dumps({"origin": "https://kollabor.ai", "bind_host": "10.0.0.5"}))
    py, venv = "/home/u/venv/bin/python", "/home/u/venv/bin/kollab"
    fake_proc(proc, 11, [py, venv, "relay", "run", "--config", str(ours)], "/home/u")
    publisher = [py, "-m", "plugins.hub.dns.discovery_publish", "--origin", "https://selfhost.kollabor.ai"]
    publisher += ["--state-dir", "/home/u/pub-state", "--output", "/home/u/www/agent-keys.json", "--watch"]
    fake_proc(proc, 12, publisher, "/home/u")
    static = ["python3", "-m", "http.server", "9177", "--bind", "10.0.0.5", "--directory", "/home/u/www"]
    fake_proc(proc, 13, static, "/home/u")
    fake_proc(proc, 14, [py, venv, "relay", "run", "--config", str(theirs)], "/home/u")  # kollabor.ai's own relay
    fake_proc(proc, 15, ["python3", "-m", "http.server", "8000"], "/tmp")  # some other static server

    def inspect(domain: str) -> dict:
        out = subprocess.run(
            [sys.executable, str(M4 / "inspect_old.py"), domain],
            env={**os.environ, "M4_PROC": str(proc)}, capture_output=True, text=True, check=True,
        )  # fmt: skip
        return json.loads(out.stdout)

    found = inspect("selfhost.kollabor.ai")
    assert (found["relay"]["pid"], found["publisher"]["pid"], found["static"]["pid"]) == (11, 12, 13)
    assert found["relay"]["settings"]["bind_host"] == "10.0.0.5"
    assert found["relay"]["settings"]["trusted_proxies"] == ["10.0.0.1"]
    assert Path(found["publisher"]["state_dir"]).name == "pub-state"
    assert Path(found["static"]["directory"]) == Path(found["publisher"]["output"]).parent
    assert inspect("nothing.example.com") == {
        "domain": "nothing.example.com",
        "relay": None,
        "publisher": None,
        "static": None,
    }
