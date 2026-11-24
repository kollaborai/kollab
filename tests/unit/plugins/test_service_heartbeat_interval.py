"""The UI needs the declared interval to interpret service heartbeats."""

from kollabor_plugins.registry import _service_entries


def test_service_entries_expose_only_valid_declared_heartbeat_intervals(tmp_path):
    manifest = {
        "services": {
            "worker": {"command": ["true"], "heartbeat_s": 60},
            "silent": {"command": ["true"]},
            "invalid": {"command": ["true"], "heartbeat_s": -3},
            "boolean": {"command": ["true"], "heartbeat_s": True},
        }
    }
    entries = {row["name"]: row for row in _service_entries(tmp_path, "hello", manifest)}
    assert entries["worker"]["heartbeat_s"] == 60
    assert entries["silent"]["heartbeat_s"] is None
    assert entries["invalid"]["heartbeat_s"] is None
    assert entries["boolean"]["heartbeat_s"] is None
