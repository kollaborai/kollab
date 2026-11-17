"""Service listing reports only verified live identities."""

import json

from kollabor_plugins import registry, services


def test_service_entries_report_live_child_and_since(tmp_path, monkeypatch):
    run = tmp_path / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.state").write_text(json.dumps({"status": "running", "desired": "running", "exit_history": []}))
    (run / "worker.pid").write_text(
        json.dumps({"wrapper": {"pid": 10, "start_time": 20}, "child": {"pid": 11, "start_time": 21}})
    )
    monkeypatch.setattr(services, "_identity_alive", lambda identity, **_: identity.get("pid") == 11)
    entry = registry._service_entries(tmp_path, "hello", {"services": {"worker": {}}})[0]
    assert entry["pid"] == 11
    assert entry["since"] == 21
    monkeypatch.setattr(services, "_identity_alive", lambda *_args, **_kwargs: False)
    entry = registry._service_entries(tmp_path, "hello", {"services": {"worker": {}}})[0]
    assert entry["pid"] is None
    assert entry["state"] == "unknown"
