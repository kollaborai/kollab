"""Koordinator prompt contract (issue #211).

The coordinator's system prompt must contain the end-of-turn ledger check
directive: check the TaskLedger for open/stalled cards before ending a
turn, and act on them. The supervisor is the backstop; this contract is
the first line of defense.
"""

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

REPO = Path(__file__).parent.parent.parent
KOORDINATOR_DIR = REPO / "bundles" / "agents" / "koordinator"


def _prompt_text() -> str:
    """Render the koordinator prompt's include structure the way the
    runtime does: system_prompt.md plus every <trender type="include">
    fragment under sections/."""
    parts = []
    base = (KOORDINATOR_DIR / "system_prompt.md").read_text()
    parts.append(base)
    sections_dir = KOORDINATOR_DIR / "sections"
    if sections_dir.exists():
        for section in sorted(sections_dir.glob("*.md")):
            parts.append(section.read_text())
    return "\n".join(parts)


@pytest.fixture(scope="module")
def prompt():
    return _prompt_text()


def test_end_of_turn_ledger_check_directive_exists(prompt):
    assert "END-OF-TURN LEDGER CHECK" in prompt


def test_directive_requires_checking_before_ending_turn(prompt):
    assert "before you END ANY TURN" in prompt


def test_directive_names_the_work_listing_tool(prompt):
    assert "hub_work" in prompt


def test_directive_names_the_claim_tool(prompt):
    assert "hub_claim" in prompt


def test_directive_covers_open_and_stalled_cards(prompt):
    assert "open" in prompt and "stalled" in prompt


def test_directive_defines_done_as_empty_ledger(prompt):
    assert "no open cards" in prompt


def test_directive_explains_assignment_path(prompt):
    assert "hub_msg" in prompt


def test_directive_mentions_supervisor_backstop(prompt):
    assert "idle supervisor" in prompt


def test_directive_notes_dual_store_split(prompt):
    """hub_queue slots and TaskLedger cards are distinct stores; the
    coordinator must not mistake a queued slot for a tracked card."""
    assert "DISTINCT stores" in prompt


def test_system_prompt_includes_sections(prompt):
    base = (KOORDINATOR_DIR / "system_prompt.md").read_text()
    assert "<trender" in base or "sections" in base


def test_prompt_sections_actually_exist():
    sections = sorted((KOORDINATOR_DIR / "sections").glob("*.md"))
    assert sections, "koordinator has no sections to include"
