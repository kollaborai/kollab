"""The tmux sidebar scenario must assert the bundled persona it seeds."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_sidebar_roster_spec_expects_the_role_named_gem():
    roster = json.loads((ROOT / "bundles/rosters/marketing-crew.json").read_text())
    persona = next(persona for persona in roster["personas"] if persona["name"] == "brand-strategist")
    steps = json.loads((ROOT / "tests/tmux/specs/web_sidebar_roster.json").read_text())["steps"]
    patterns = [step.get("pattern", "") for step in steps]

    assert "^offline: brand-strategist,sapphire$" in patterns
    assert (
        f"^offline look: brand-strategist={persona['display_name']}/{persona['avatar']['kind']} "
        "sapphire=Sapphire/gem$"
    ) in patterns
