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


def test_service_entries_rejects_non_list_declared_secrets(tmp_path):
    entry = registry._service_entries(
        tmp_path,
        "hello",
        {"services": {"worker": {"secrets": "API_TOKEN"}}},
    )[0]

    assert entry["secrets"] == []


def test_service_entries_normalizes_malformed_state_fields(tmp_path, monkeypatch):
    run = tmp_path / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.state").write_text(
        json.dumps(
            {
                "status": {"secret": "not a state"},
                "desired": ["running"],
                "exit_history": {"bad": "shape"},
                "stop_deadline": {"bad": "shape"},
                "error": {"secret": "not an error string"},
            }
        )
    )
    (run / "worker.pid").write_text(json.dumps({"child": {"pid": True, "start_time": True}}))
    monkeypatch.setattr(services, "_identity_alive", lambda *_args, **_kwargs: True)

    entry = registry._service_entries(
        tmp_path,
        "hello",
        {"services": {"worker": {"heartbeat_s": True}}},
    )[0]

    assert entry["state"] == "unknown"
    assert entry["desired"] == "stopped"
    assert entry["pid"] is None
    assert entry["since"] is None
    assert entry["restarts"] == 0
    assert entry["heartbeat_s"] is None
    assert entry["stop_deadline"] is None
    assert entry["error"] is None
