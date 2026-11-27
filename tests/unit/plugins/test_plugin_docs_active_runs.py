from pathlib import Path


def test_orphaned_run_scenario_matches_disable_and_remove_conflicts():
    repo_root = Path(__file__).resolve().parents[3]
    spec = (repo_root / "docs/specs/plugins.md").read_text(encoding="utf-8")
    scenario = spec.split("11. **Orphaned runs and half-finished steps.**", 1)[1].split("\n12.", 1)[0]

    assert "cancels its live agent runs" not in scenario
    assert "refuses to turn off or remove" in scenario
    assert "cancel those runs first" in scenario
