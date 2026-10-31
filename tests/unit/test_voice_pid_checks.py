"""Voice process checks must probe without signalling: os.kill(pid, 0) sends a signal on Windows (#460)."""

import json
import signal
import time

import kollabor_engine.voice_livekit as voice_livekit
import kollabor_voice.client as voice_client
import pytest
from kollabor_engine.voice_livekit import LiveKitRuntime
from kollabor_voice.client import VoiceClient

PID = 424242


class _Service:
    def __init__(self, status):
        self.status = status

    async def call(self, op):
        assert op == "status"
        return self.status


def _write_setup(root, pid=PID):
    data = {"pid": pid, "state": "setup", "updated_at": time.time()}
    (root / "setup.json").write_text(json.dumps(data))
    return data


@pytest.mark.asyncio
async def test_stale_voice_service_wait_returns_at_once_when_pid_is_dead(monkeypatch):
    kills = []
    monkeypatch.setattr(voice_livekit, "pid_alive", lambda pid: False)
    monkeypatch.setattr(voice_livekit.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    started = time.monotonic()
    await LiveKitRuntime._retire_stale_service(
        _Service({"owner": None, "pid": PID}), runtime_ready=False
    )
    assert time.monotonic() - started < 1.0  # no 10 s deadline wait: the pid is gone
    assert kills == [(PID, signal.SIGTERM)]  # probed via pid_alive, never os.kill(pid, 0)


def test_windows_setup_status_reports_stopped_when_pid_is_dead(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_client.os, "name", "nt")
    monkeypatch.setattr("kollabor_config.proc_ctl.pid_alive", lambda pid: False)
    _write_setup(tmp_path)
    result = VoiceClient(root=tmp_path).setup_status()
    assert result["state"] == "error" and result["detail"] == "Voice setup stopped"


def test_windows_setup_status_returns_data_while_pid_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_client.os, "name", "nt")
    monkeypatch.setattr("kollabor_config.proc_ctl.pid_alive", lambda pid: True)
    data = _write_setup(tmp_path)
    assert VoiceClient(root=tmp_path).setup_status() == data


def test_posix_setup_status_probes_the_pid_with_signal_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_client.os, "name", "posix")
    kills = []
    monkeypatch.setattr(voice_client.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    data = _write_setup(tmp_path)
    assert VoiceClient(root=tmp_path).setup_status() == data
    assert kills == [(PID, 0)]
