from pathlib import Path


def test_plugin_spec_does_not_claim_unimplemented_agent_limit_enforcement():
    repo_root = Path(__file__).resolve().parents[3]
    spec = (repo_root / "docs/specs/plugins.md").read_text(encoding="utf-8")
    d11 = next(line for line in spec.splitlines() if line.startswith("| D11 |"))

    assert "daemon enforces these defaults" not in d11
    assert "daemon does not enforce them yet" in d11


def test_plugin_spec_does_not_claim_agent_limits_are_enforced_elsewhere():
    repo_root = Path(__file__).resolve().parents[3]
    spec = (repo_root / "docs/specs/plugins.md").read_text(encoding="utf-8")
    unsupported_claims = (
        "Daily Token Cap reached.",
        "daemon refuses provider requests past either limit",
        "at most 4 agents at once",
        "the next start gets 429",
        "refuses the next agent run with 429",
        "Per-run limits enforced by the daemon itself",
        "agent-run limits hold",
        "limits the daemon enforces",
    )

    for claim in unsupported_claims:
        assert claim not in spec


def test_plugin_limit_scenarios_are_marked_as_future_acceptance():
    repo_root = Path(__file__).resolve().parents[3]
    spec = (repo_root / "docs/specs/plugins.md").read_text(encoding="utf-8")

    assert "After agent-run support ships, if a run passes `timeout_s` or `max_tokens`" in spec
    assert "After agent-run support ships, if a plugin's counted tokens for the day reach" in spec
