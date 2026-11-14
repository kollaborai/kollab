"""The example service produces visible logs and shuts down gracefully."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def test_hello_service_logs_and_stops():
    script = Path(__file__).resolve().parents[3] / "bundles/plugins/hello/bin/hello.py"
    proc = subprocess.Popen([sys.executable, "-u", str(script)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(0.25)
        os.kill(proc.pid, signal.SIGTERM)
        output, _ = proc.communicate(timeout=4)
        assert proc.returncode == 0
        assert "Hello service started" in output
        assert "Hello service stopped" in output
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
