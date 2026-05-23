#!/usr/bin/env python
"""Test health/status/version/ready endpoints."""

import importlib.util
import subprocess
import sys
import time

import pytest

requests = pytest.importorskip("requests")

BASE_URL = "http://localhost:7433"


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="engine web dependencies not installed in current test environment",
)


def test_endpoints():
    """Test all health endpoints."""
    # Start server
    proc = subprocess.Popen(
        [sys.executable, "-m", "kollabor_engine", "serve", "--port", "7433"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(5)  # Give more time for startup

        # Test /health
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
        data = resp.json()
        assert resp.status_code == 200
        assert data["status"] == "healthy"
        assert data["uptime"] > 0
        print("  /health OK")

        # Test /version
        resp = requests.get(f"{BASE_URL}/version", timeout=5)
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
        resp = requests.get(f"{BASE_URL}/status", timeout=5)
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
        resp = requests.get(f"{BASE_URL}/ready", timeout=5)
        data = resp.json()
        assert resp.status_code == 200
        assert "ready" in data
        assert "checks" in data
        assert "profiles" in data["checks"]
        assert "sessions" in data["checks"]
        print("  /ready OK")

        # Test auth exemption - endpoints should work without auth
        resp = requests.get(
            f"{BASE_URL}/health", headers={"Authorization": "Bearer invalid"}, timeout=5
        )
        assert resp.status_code == 200
        print("  auth exemption OK")

        # Test protected endpoint - should fail without auth
        resp = requests.get(f"{BASE_URL}/sessions", timeout=5)
        assert resp.status_code == 401
        print("  protected endpoint OK")

        print("\nAll tests passed!")
        return 0

    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(test_endpoints())
