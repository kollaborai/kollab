from pathlib import Path


def test_plugin_spec_does_not_claim_unimplemented_agent_limit_enforcement():
    repo_root = Path(__file__).resolve().parents[3]
    spec = (repo_root / "docs/specs/plugins.md").read_text(encoding="utf-8")
    d11 = next(line for line in spec.splitlines() if line.startswith("| D11 |"))

    assert "daemon enforces these defaults" not in d11
    assert "daemon does not enforce them yet" in d11
