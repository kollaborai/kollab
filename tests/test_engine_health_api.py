#!/usr/bin/env python
"""Test health/status/version/ready endpoints."""

import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

requests = pytest.importorskip("requests")

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="engine web dependencies not installed in current test environment",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_ready(proc: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if line == "" and proc.poll() is not None:
            raise AssertionError(f"engine exited before READY on port {port}")
        if line.strip() == f"READY:{port}":
            return
    raise AssertionError(f"engine did not report READY on port {port}")


def test_endpoints():
    """Test all health endpoints."""
    port = _free_port()
    base_url = f"http://localhost:{port}"
    env = os.environ.copy()
    env["KOLLAB_NO_AUTO_DETECT"] = "1"

    # Start server
    with tempfile.TemporaryDirectory() as home_dir:
        env["HOME"] = home_dir
        proc = subprocess.Popen(
            [sys.executable, "-m", "kollabor_engine", "serve", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            _wait_for_ready(proc, port)

            # Test /health
            resp = requests.get(f"{base_url}/health", timeout=5)
            data = resp.json()
            assert resp.status_code == 200
            assert data["status"] == "healthy"
            assert data["uptime"] >= 0
            print("  /health OK")

            # Test /version
            resp = requests.get(f"{base_url}/version", timeout=5)
            data = resp.json()
            assert resp.status_code == 200
            assert "version" in data
            assert "engine" in data
            assert "kollabor_ai" in data
            assert "kollabor_agent" in data
            assert "kollabor_events" in data
            assert "python" in data
            print("  /version OK")

            # Test /status
            resp = requests.get(f"{base_url}/status", timeout=5)
            data = resp.json()
            assert resp.status_code == 200
            assert "sessions" in data
            assert "uptime" in data
            assert "session_ids" in data
            assert "version" in data
            assert "providers" in data
            assert "mcp_servers" in data
            assert isinstance(data["providers"], list)
            assert isinstance(data["mcp_servers"], dict)
            print("  /status OK")

            # Test /ready
            resp = requests.get(f"{base_url}/ready", timeout=5)
            data = resp.json()
            assert resp.status_code == 200
            assert "ready" in data
            assert "checks" in data
            assert "profiles" in data["checks"]
            assert "sessions" in data["checks"]
            print("  /ready OK")

            # Test auth exemption - endpoints should work without auth
            resp = requests.get(
                f"{base_url}/health",
                headers={"Authorization": "Bearer invalid"},
                timeout=5,
            )
            assert resp.status_code == 200
            print("  auth exemption OK")

            # Test protected endpoint - should fail without auth
            resp = requests.get(f"{base_url}/sessions", timeout=5)
            assert resp.status_code == 401
            print("  protected endpoint OK")

            print("\nAll tests passed!")
            return 0

        finally:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(test_endpoints())
