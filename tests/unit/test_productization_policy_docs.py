"""Productization policy docs (issue #731).

Every enhancement ships as an opt-in, default-off, repo-agnostic product
surface (skill, extension, agent or plugin). The contributor docs must state
that rule and its checklist, and every issue in the #711-#730 batch must be
mapped to a surface.
"""

import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

REPO = Path(__file__).parent.parent.parent
CONTRIBUTING = REPO / "CONTRIBUTING.md"
SDLC = REPO / "SOFTWARE_DEVELOPMENT_LIFECYCLE.md"
POLICY = REPO / "docs" / "productization.md"
MAPPED_ISSUES = range(711, 731)
REQUIRED_TERMS = (
    "opt-in",
    "default off",
    "repo-agnostic",
    "product surface",
    "skill",
    "extension",
    "agent",
    "plugin",
)


def _section(text: str, heading: str) -> str | None:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1) if match else None


def test_contributing_states_productization_rule():
    section = _section(CONTRIBUTING.read_text(), "Productization")
    assert section is not None, "CONTRIBUTING.md needs a '## Productization' section"
    lowered = section.lower()
    for term in REQUIRED_TERMS:
        assert term in lowered, f"CONTRIBUTING.md Productization lacks {term!r}"
    assert "docs/productization.md" in section


def test_sdlc_requires_productization_checklist():
    section = _section(SDLC.read_text(), "Productization")
    assert (
        section is not None
    ), "SOFTWARE_DEVELOPMENT_LIFECYCLE.md needs a '## Productization' section"
    lowered = section.lower()
    assert "opt-in" in lowered
    assert "default off" in lowered
    assert "docs/productization.md" in section


def test_policy_doc_has_checklist():
    text = POLICY.read_text()
    assert len(re.findall(r"^- \[ \] ", text, re.M)) >= 5


def test_policy_doc_maps_every_batch_issue_to_a_surface():
    text = POLICY.read_text()
    missing = [n for n in MAPPED_ISSUES if not re.search(rf"#{n}\b", text)]
    assert not missing, f"issues without a surface mapping: {missing}"
    for line in text.splitlines():
        if re.search(r"\|\s*#7\d\d\s*\|", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            assert len(cells) >= 3 and cells[1], f"empty surface cell: {line}"
