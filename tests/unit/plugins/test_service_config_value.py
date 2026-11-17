"""Saved service state drives the config checkbox after refresh."""

import json

from kollabor_plugins.settings import config_value


def test_service_config_value_reads_desired_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / ".kollab/plugin-data/hello/run/worker.state"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"desired": "stopped"}))
    assert config_value("plugin-service.hello.worker", True) is False
    state.write_text(json.dumps({"desired": "running"}))
    assert config_value("plugin-service.hello.worker", False) is True
