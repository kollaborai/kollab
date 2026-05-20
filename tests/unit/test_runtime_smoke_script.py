from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_smoke_script_covers_prd_surface():
    script = ROOT / "tests" / "tmux" / "runtime_smoke.sh"

    assert script.exists()
    text = script.read_text(encoding="utf-8")

    required_terms = [
        "mktemp -d",
        "HOME=",
        "mcp_settings.json",
        "mock_mcp_server.sh",
        "/doctor proof",
        "/status",
        "/hub status",
        "/profile default",
        "/agent coder",
        "/permissions strict",
        "reattach",
        "delivery trace",
    ]
    for term in required_terms:
        assert term in text

    assert "docker rm" not in text
    assert "docker volume rm" not in text


def test_stabilization_gate_runs_runtime_smoke():
    gate = ROOT / "scripts" / "stabilization-gate.sh"

    assert "tests/tmux/runtime_smoke.sh" in gate.read_text(encoding="utf-8")
