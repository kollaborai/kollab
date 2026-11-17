"""Agent-using packages receive the common agent limit settings."""

import json

from kollabor_plugins.packages import files_sha256
from kollabor_plugins.registry import plugin_entries
from kollabor_plugins.settings import save


def test_agent_limits_are_listed_and_stored_without_a_settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    home = tmp_path / ".kollab"
    root = home / "plugins" / "agent-demo"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "agent-demo", "uses": ["agents"]}))
    (home / "plugins.json").write_text(
        json.dumps({"plugins": {"agent-demo": {"enabled": False, "files_sha256": files_sha256(root)}}, "dev": {}})
    )
    builtins = tmp_path / "builtins"
    builtins.mkdir()
    entry = next(row for row in plugin_entries(builtins) if row["id"] == "agent-demo")
    widgets = {widget["config_path"]: widget for widget in entry["settings"]}
    assert widgets["max_tokens_per_day"]["default"] == 2_000_000
    assert widgets["max_concurrent_runs"]["default"] == 4
    save("agent-demo", {"max_tokens_per_day": 1000, "max_concurrent_runs": 2})
    values = json.loads((home / "plugin-data/agent-demo/values.json").read_text())
    assert values == {"max_tokens_per_day": 1000, "max_concurrent_runs": 2}
