from pathlib import Path


def test_plugin_background_process_spec_matches_inventory_stop_deadline_contract():
    spec = Path("docs/specs/plugins.md").read_text(encoding="utf-8")
    background = spec.split("### Background processes\n", 1)[1].split("\n### ", 1)[0]

    assert "does not return a stop deadline" not in spec
    assert "does not return a stop countdown" not in spec
    assert "`stop_deadline`" in background
    assert "Settings" in background and "countdown" in background
