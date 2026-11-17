"""Koordinator intake must check for existing open PRs before dispatch."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KOORDINATOR_SECTION = REPO / "bundles/agents/koordinator/sections/00-header.md"


def test_koordinator_checks_open_prs_before_dispatching_a_lane():
    prompt = KOORDINATOR_SECTION.read_text().lower()

    assert "existing open pr" in prompt
    assert "before dispatch" in prompt
    assert "same linked issue" in prompt
    assert "do not dispatch duplicate work" in prompt
